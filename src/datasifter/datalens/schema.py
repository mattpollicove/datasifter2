"""A read-only view of the parts of a server's schema that the engine reasons about."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from datasifter.datalens.models import base_attribute_name

# Attribute syntaxes whose values are the DN of another entry (RFC 4517):
# Distinguished Name, and Name And Optional UID (used by uniqueMember).
_ENTRY_REFERENCE_SYNTAXES = frozenset(
    {"1.3.6.1.4.1.1466.115.121.1.12", "1.3.6.1.4.1.1466.115.121.1.34"}
)
# Reference attributes that some servers define without stating a syntax, because
# they inherit it from a parent attribute type (lower case).
_ENTRY_REFERENCE_ATTRIBUTES = frozenset(
    {
        "manager",
        "member",
        "uniquemember",
        "owner",
        "secretary",
        "seealso",
        "roleoccupant",
        "managedby",
        "memberof",
        "ismemberof",
        "directreports",
    }
)


@dataclass(frozen=True)
class DirectorySchema:
    """Object classes and attribute types as the server defines them.

    Attribute types can have several names (``cn`` and ``commonName``); they are
    compared by OID so that either name satisfies a requirement.
    """

    # Lower-cased object class name -> {"required": [...], "superiors": [...], ...}.
    classes: dict[str, dict[str, Any]]
    # Lower-cased attribute name -> OID (or the name itself when the server gave no OID).
    attribute_ids: dict[str, str]
    # Lower-cased attribute name -> the name as the server spells it.
    attribute_names: dict[str, str]
    single_valued_ids: frozenset[str]
    entry_reference_ids: frozenset[str]

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> DirectorySchema:
        """Build the view from ``LDAPService.schema_options()``."""
        attribute_ids: dict[str, str] = {}
        attribute_names: dict[str, str] = {}
        single_valued: set[str] = set()
        references: set[str] = set()
        for detail in options.get("attribute_details", []):
            name = detail["name"]
            identifier = detail.get("oid") or name.casefold()
            attribute_ids[name.casefold()] = identifier
            attribute_names[name.casefold()] = name
            if detail.get("single_value"):
                single_valued.add(identifier)
            if (
                detail.get("syntax") in _ENTRY_REFERENCE_SYNTAXES
                or name.casefold() in _ENTRY_REFERENCE_ATTRIBUTES
            ):
                references.add(identifier)
        return cls(
            classes={name.casefold(): dict(info) for name, info in options["object_classes"].items()},
            attribute_ids=attribute_ids,
            attribute_names=attribute_names,
            single_valued_ids=frozenset(single_valued),
            entry_reference_ids=frozenset(references),
        )

    def knows_class(self, name: str) -> bool:
        return name.casefold() in self.classes

    def knows_attribute(self, attribute: str) -> bool:
        return base_attribute_name(attribute) in self.attribute_ids

    def attribute_name(self, attribute: str) -> str | None:
        """Return the server's spelling of an attribute name, or None when it is unknown."""
        return self.attribute_names.get(base_attribute_name(attribute))

    def attribute_id(self, attribute: str) -> str:
        name = base_attribute_name(attribute)
        return self.attribute_ids.get(name, name)

    def is_single_valued(self, attribute: str) -> bool:
        return self.attribute_id(attribute) in self.single_valued_ids

    def references_entry(self, attribute: str) -> bool:
        """Return True when the attribute's values are DNs of other entries."""
        return self.attribute_id(attribute) in self.entry_reference_ids

    def required_attributes(self, class_names: Sequence[str]) -> dict[str, str]:
        """Return ``{attribute id: attribute name}`` required by the classes and their parents."""
        required: dict[str, str] = {}
        pending = [name.casefold() for name in class_names]
        seen: set[str] = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            details = self.classes.get(name)
            if details is None:
                continue
            for attribute in details.get("required", []):
                required.setdefault(self.attribute_id(attribute), attribute)
            pending.extend(superior.casefold() for superior in details.get("superiors", []))
        return required


def references_entry_by_name(attribute: str) -> bool:
    """Fallback for :meth:`DirectorySchema.references_entry` when no schema could be read."""
    return base_attribute_name(attribute) in _ENTRY_REFERENCE_ATTRIBUTES
