"""LDIF parsing, validation, serialization, and safe file writing."""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn

LDIFValue = str | bytes
ChangeType = Literal["add", "modify", "delete", "modrdn"]
ModifyOperation = Literal["add", "delete", "replace"]

_ATTRIBUTE_DESCRIPTION = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)"
    r"(?:;[A-Za-z0-9-]+)*\Z"
)


class LDIFValidationError(ValueError):
    """Raised when an LDIF file is malformed or contains unsupported syntax."""


@dataclass(frozen=True)
class LDIFModification:
    operation: ModifyOperation
    attribute: str
    values: tuple[LDIFValue, ...]


@dataclass(frozen=True)
class LDIFRecord:
    dn: str
    change_type: ChangeType
    attributes: dict[str, list[LDIFValue]] = field(default_factory=dict)
    modifications: tuple[LDIFModification, ...] = ()
    new_rdn: str | None = None
    delete_old_rdn: bool = True
    new_superior: str | None = None


@dataclass(frozen=True)
class _Line:
    text: str
    number: int


def _logical_records(text: str) -> list[list[_Line]]:
    records: list[list[_Line]] = []
    record: list[_Line] = []
    pending: _Line | None = None

    def finish_line() -> None:
        nonlocal pending
        if pending is not None:
            record.append(pending)
            pending = None

    def finish_record() -> None:
        finish_line()
        if record:
            records.append(record.copy())
            record.clear()

    for number, physical_line in enumerate(text.splitlines(), start=1):
        if physical_line.startswith(" "):
            if pending is None:
                raise LDIFValidationError(
                    f"Line {number}: a folded line must follow an LDIF line."
                )
            pending = _Line(pending.text + physical_line[1:], pending.number)
        elif physical_line == "":
            finish_record()
        elif physical_line.startswith("#"):
            finish_line()
        else:
            finish_line()
            pending = _Line(physical_line, number)

    finish_record()
    return records


def _validate_attribute(attribute: str, line_number: int) -> None:
    if not _ATTRIBUTE_DESCRIPTION.fullmatch(attribute):
        raise LDIFValidationError(
            f"Line {line_number}: invalid LDAP attribute description {attribute!r}."
        )


def _plain_value_is_safe(value: str) -> bool:
    if not value.isascii():
        return False
    safe_character = all(
        0x01 <= ord(character) <= 0x09
        or 0x0B <= ord(character) <= 0x0C
        or 0x0E <= ord(character) <= 0x7F
        for character in value
    )
    if not safe_character or not value:
        return safe_character
    first = ord(value[0])
    return (
        0x01 <= first <= 0x09
        or 0x0B <= first <= 0x0C
        or 0x0E <= first <= 0x1F
        or 0x21 <= first <= 0x39
        or first == 0x3B
        or 0x3D <= first <= 0x7F
    )


def _field(line: _Line) -> tuple[str, str, LDIFValue]:
    match = re.fullmatch(r"([^:]+)(::|:<|:)(?: *(.*))?", line.text)
    if match is None:
        raise LDIFValidationError(f"Line {line.number}: invalid LDIF attribute line.")
    attribute, separator, raw_value = match.groups()
    _validate_attribute(attribute, line.number)
    raw_value = raw_value or ""
    if separator == ":<":
        raise LDIFValidationError(
            f"Line {line.number}: URL-valued LDIF attributes are not supported."
        )
    if separator == "::":
        try:
            value: LDIFValue = base64.b64decode(raw_value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise LDIFValidationError(
                f"Line {line.number}: invalid base64 value."
            ) from error
    else:
        if not _plain_value_is_safe(raw_value):
            raise LDIFValidationError(
                f"Line {line.number}: unsafe plain-text values must be base64 encoded."
            )
        value = raw_value
    return attribute, separator, value


def _text_value(value: LDIFValue, field_name: str, line_number: int) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LDIFValidationError(
                f"Line {line_number}: {field_name} must be valid UTF-8."
            ) from error
    return value


def _validate_dn(value: str, field_name: str, line_number: int) -> None:
    try:
        parsed = parse_dn(value, escape=False, strip=False)
    except LDAPInvalidDnError as error:
        raise LDIFValidationError(
            f"Line {line_number}: invalid {field_name} distinguished name: {error}"
        ) from error
    if not parsed:
        raise LDIFValidationError(
            f"Line {line_number}: {field_name} distinguished name cannot be empty."
        )


def _attributes(lines: list[_Line], missing_line_number: int) -> dict[str, list[LDIFValue]]:
    attributes: dict[str, list[LDIFValue]] = {}
    names: dict[str, str] = {}
    for line in lines:
        attribute, _separator, value = _field(line)
        if attribute.casefold() in {"dn", "changetype"}:
            raise LDIFValidationError(
                f"Line {line.number}: {attribute} is only allowed in its LDIF header position."
            )
        key = attribute.casefold()
        name = names.setdefault(key, attribute)
        attributes.setdefault(name, []).append(value)
    if not attributes:
        raise LDIFValidationError(
            f"Line {missing_line_number}: an add record must contain at least one attribute."
        )
    return attributes


def _add_attributes(lines: list[_Line], missing_line_number: int) -> dict[str, list[LDIFValue]]:
    attributes = _attributes(lines, missing_line_number)
    if not any(
        name.casefold() == "objectclass"
        and any(value not in ("", b"") for value in values)
        for name, values in attributes.items()
    ):
        line_number = lines[-1].number if lines else missing_line_number
        raise LDIFValidationError(
            f"Line {line_number}: add records must include a non-empty objectClass value."
        )
    return attributes


def _parse_record(lines: list[_Line]) -> LDIFRecord:
    if not lines:
        raise LDIFValidationError("LDIF records cannot be empty.")
    dn_attribute, _separator, dn_value = _field(lines[0])
    if dn_attribute.casefold() != "dn":
        raise LDIFValidationError(f"Line {lines[0].number}: each record must start with dn.")
    dn = _text_value(dn_value, "dn", lines[0].number)
    _validate_dn(dn, "dn", lines[0].number)

    position = 1
    while position < len(lines):
        attribute, _separator, _value = _field(lines[position])
        if attribute.casefold() != "control":
            break
        raise LDIFValidationError(
            f"Line {lines[position].number}: LDIF controls are not supported."
        )

    change_type: str | None = None
    if position < len(lines):
        attribute, separator, value = _field(lines[position])
        if attribute.casefold() == "changetype":
            if separator != ":" or not isinstance(value, str):
                raise LDIFValidationError(
                    f"Line {lines[position].number}: changetype must be a plain-text value."
                )
            change_type = value.casefold()
            position += 1

    remaining = lines[position:]
    if change_type is None:
        return LDIFRecord(
            dn=dn,
            change_type="add",
            attributes=_add_attributes(remaining, lines[position - 1].number + 1),
        )

    if change_type == "add":
        return LDIFRecord(
            dn=dn,
            change_type="add",
            attributes=_add_attributes(remaining, lines[position - 1].number + 1),
        )
    if change_type == "delete":
        if remaining:
            raise LDIFValidationError(
                f"Line {remaining[0].number}: delete records cannot contain attributes."
            )
        return LDIFRecord(dn=dn, change_type="delete")
    if change_type == "modify":
        modifications: list[LDIFModification] = []
        cursor = 0
        while cursor < len(remaining):
            directive = remaining[cursor]
            operation, separator, attribute_value = _field(directive)
            operation = operation.casefold()
            operations: dict[str, ModifyOperation] = {
                "add": "add",
                "delete": "delete",
                "replace": "replace",
            }
            typed_operation = operations.get(operation)
            if typed_operation is None or separator != ":":
                raise LDIFValidationError(
                    f"Line {directive.number}: expected add, delete, or replace modification."
                )
            if not isinstance(attribute_value, str) or not attribute_value:
                raise LDIFValidationError(
                    f"Line {directive.number}: modification requires an attribute name."
                )
            _validate_attribute(attribute_value, directive.number)
            cursor += 1
            values: list[LDIFValue] = []
            while cursor < len(remaining) and remaining[cursor].text != "-":
                attribute_line = remaining[cursor]
                attribute, _value_separator, value = _field(attribute_line)
                if attribute.casefold() != attribute_value.casefold():
                    raise LDIFValidationError(
                        f"Line {attribute_line.number}: expected values for "
                        f"{attribute_value!r} or a '-' separator."
                    )
                values.append(value)
                cursor += 1
            if cursor == len(remaining):
                raise LDIFValidationError(
                    f"Line {directive.number}: each modification must end with '-'."
                )
            if typed_operation == "add" and not values:
                raise LDIFValidationError(
                    f"Line {directive.number}: add modification requires at least one value."
                )
            modifications.append(
                LDIFModification(
                    operation=typed_operation,
                    attribute=attribute_value,
                    values=tuple(values),
                )
            )
            cursor += 1
        if not modifications:
            raise LDIFValidationError(
                f"Line {lines[-1].number}: modify record must contain a modification."
            )
        return LDIFRecord(
            dn=dn,
            change_type="modify",
            modifications=tuple(modifications),
        )
    if change_type in {"modrdn", "moddn"}:
        fields: dict[str, tuple[str, LDIFValue, int]] = {}
        for line in remaining:
            attribute, separator, value = _field(line)
            key = attribute.casefold()
            if key not in {"newrdn", "deleteoldrdn", "newsuperior"}:
                raise LDIFValidationError(
                    f"Line {line.number}: unsupported {attribute!r} in a modrdn record."
                )
            if key in fields or separator == ":<":
                raise LDIFValidationError(
                    f"Line {line.number}: invalid or duplicate {attribute} field."
                )
            fields[key] = (separator, value, line.number)
        if "newrdn" not in fields or "deleteoldrdn" not in fields:
            raise LDIFValidationError(
                f"Line {lines[-1].number}: modrdn requires newrdn and deleteoldrdn."
            )
        new_rdn = _text_value(fields["newrdn"][1], "newrdn", fields["newrdn"][2])
        _validate_dn(new_rdn, "newrdn", fields["newrdn"][2])
        if len(parse_dn(new_rdn, escape=False, strip=False)) != 1:
            raise LDIFValidationError(
                f"Line {fields['newrdn'][2]}: newrdn must contain one RDN."
            )
        delete_old_text = _text_value(
            fields["deleteoldrdn"][1],
            "deleteoldrdn",
            fields["deleteoldrdn"][2],
        )
        if delete_old_text not in {"0", "1"}:
            raise LDIFValidationError(
                f"Line {fields['deleteoldrdn'][2]}: deleteoldrdn must be 0 or 1."
            )
        new_superior = None
        if "newsuperior" in fields:
            new_superior = _text_value(
                fields["newsuperior"][1],
                "newsuperior",
                fields["newsuperior"][2],
            )
            _validate_dn(new_superior, "newsuperior", fields["newsuperior"][2])
        return LDIFRecord(
            dn=dn,
            change_type="modrdn",
            new_rdn=new_rdn,
            delete_old_rdn=delete_old_text == "1",
            new_superior=new_superior,
        )
    raise LDIFValidationError(
        f"Line {lines[position - 1].number}: unsupported changetype {change_type!r}."
    )


def parse_ldif(text: str) -> list[LDIFRecord]:
    """Parse and validate LDIF content and change records."""
    records = _logical_records(text)
    if records:
        first = records[0]
        if len(first) == 1:
            attribute, separator, value = _field(first[0])
            if attribute.casefold() == "version":
                if separator != ":" or value != "1":
                    raise LDIFValidationError(
                        f"Line {first[0].number}: only LDIF version 1 is supported."
                    )
                records = records[1:]
    return [_parse_record(record) for record in records]


def read_ldif(path: str | Path) -> list[LDIFRecord]:
    """Read a UTF-8 LDIF file and validate every record before returning."""
    source = Path(path)
    try:
        content = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise LDIFValidationError(f"Could not read LDIF file {source}: {error}") from error
    return parse_ldif(content)


def _is_safe_value(value: str | bytes) -> bool:
    try:
        text = value.decode("ascii") if isinstance(value, bytes) else value
    except UnicodeDecodeError:
        return False
    if not text.isascii() or not all(" " <= character <= "~" for character in text):
        return False
    return not text.startswith((" ", ":", "<")) and not text.endswith(" ")


def _value_line(attribute: str, value: LDIFValue) -> str:
    if not isinstance(value, (str, bytes)):
        raise LDIFValidationError(
            f"LDIF values must be text or bytes, not {type(value).__name__}."
        )
    if _is_safe_value(value):
        text = value.decode("ascii") if isinstance(value, bytes) else value
        return f"{attribute}: {text}"
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return f"{attribute}:: {base64.b64encode(raw).decode('ascii')}"


def _fold(line: str, width: int = 76) -> list[str]:
    if len(line) <= width:
        return [line]
    return [line[:width], *(" " + line[start : start + width - 1]
                             for start in range(width, len(line), width - 1))]


def _record_lines(record: LDIFRecord) -> list[str]:
    _validate_dn(record.dn, "dn", 1)
    lines = [_value_line("dn", record.dn), f"changetype: {record.change_type}"]
    if record.change_type == "add":
        if not record.attributes:
            raise LDIFValidationError("An add record must contain at least one attribute.")
        for attribute, values in record.attributes.items():
            _validate_attribute(attribute, 1)
            if not values:
                raise LDIFValidationError(
                    f"Attribute {attribute!r} must contain at least one value."
                )
            lines.extend(_value_line(attribute, value) for value in values)
    elif record.change_type == "modify":
        if not record.modifications:
            raise LDIFValidationError("A modify record must contain a modification.")
        for modification in record.modifications:
            _validate_attribute(modification.attribute, 1)
            if modification.operation not in {"add", "delete", "replace"}:
                raise LDIFValidationError(
                    f"Unsupported modify operation {modification.operation!r}."
                )
            if modification.operation == "add" and not modification.values:
                raise LDIFValidationError("An add modification requires at least one value.")
            lines.append(f"{modification.operation}: {modification.attribute}")
            lines.extend(
                _value_line(modification.attribute, value)
                for value in modification.values
            )
            lines.append("-")
    elif record.change_type == "delete":
        if record.attributes or record.modifications:
            raise LDIFValidationError("A delete record cannot contain attributes or modifications.")
    elif record.change_type == "modrdn":
        if not record.new_rdn:
            raise LDIFValidationError("A modrdn record requires newrdn.")
        _validate_dn(record.new_rdn, "newrdn", 1)
        if len(parse_dn(record.new_rdn, escape=False, strip=False)) != 1:
            raise LDIFValidationError("newrdn must contain one RDN.")
        lines.append(_value_line("newrdn", record.new_rdn))
        lines.append(f"deleteoldrdn: {1 if record.delete_old_rdn else 0}")
        if record.new_superior is not None:
            _validate_dn(record.new_superior, "newsuperior", 1)
            lines.append(_value_line("newsuperior", record.new_superior))
    else:
        raise LDIFValidationError(f"Unsupported changetype {record.change_type!r}.")
    return [physical for line in lines for physical in _fold(line)]


def serialize_ldif(records: list[LDIFRecord]) -> str:
    """Serialize records and parse the result again to validate the output."""
    output = "version: 1\n\n"
    output += "\n\n".join("\n".join(_record_lines(record)) for record in records)
    if records:
        output += "\n"
    parse_ldif(output)
    return output


def write_ldif(path: str | Path, records: list[LDIFRecord]) -> None:
    """Validate and atomically write LDIF content to the selected path."""
    destination = Path(path)
    content = serialize_ldif(records)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
