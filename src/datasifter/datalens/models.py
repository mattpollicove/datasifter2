"""Typed request and result models for the Data Lens engine.

These models are the contract between callers (today: tests and Python code; later:
the natural-language layer and an API or MCP server) and the directory adapters.
They validate shape and syntax only. Checks that need the directory, such as
"does the parent entry exist", are reported as :class:`PlanIssue` items in a
:class:`DryRunResult`.

Secrets: results never carry password values. Adapters return redacted copies of
plans and entries (see :func:`is_sensitive`), and validation errors hide their input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from datasifter.datalens.dn import normalize_dn, normalize_rdn
from datasifter.directory.filters import validate_filter

REDACTED = "<redacted>"

# Attributes whose values are credentials or credential material (lower case).
SENSITIVE_ATTRIBUTES = frozenset(
    {
        "userpassword",
        "unicodepwd",
        "authpassword",
        "dbcspwd",
        "supplementalcredentials",
        "sambantpassword",
        "sambalmpassword",
        "krbprincipalkey",
    }
)

_ATTRIBUTE_NAME = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)(?:;[A-Za-z0-9-]+)*"
)
# Search-only selectors: all user attributes, all operational attributes, no attributes.
_ATTRIBUTE_SELECTORS = frozenset({"*", "+", "1.1"})

# A value as read from a directory. ldap3 decodes values using the server schema,
# so integers, booleans and timestamps arrive typed.
AttributeValue = bool | int | float | str | bytes | datetime | timedelta
# A value a caller may write. Timestamps are excluded on purpose: their wire format
# differs per attribute and backend, so callers must pass the exact string or number.
WritableValue = bool | int | str | bytes


def base_attribute_name(attribute: str) -> str:
    """Return the attribute type without options, lower-cased (``cn;lang-en`` -> ``cn``)."""
    return attribute.split(";", 1)[0].casefold()


def is_sensitive(attribute: str) -> bool:
    """Return True when the attribute's values must never appear in results or logs."""
    return base_attribute_name(attribute) in SENSITIVE_ATTRIBUTES


def _valid_attribute_name(attribute: str) -> str:
    if not isinstance(attribute, str) or not _ATTRIBUTE_NAME.fullmatch(attribute):
        raise ValueError(f"Not a valid LDAP attribute name: {attribute!r}")
    return attribute


def _reject_empty_values(attribute: str, values: tuple[Any, ...]) -> None:
    if any(value in ("", b"") for value in values):
        raise ValueError(f"Attribute {attribute!r} contains an empty value.")


class DirectoryBackend(str, Enum):
    """Which family of directory an adapter talks to."""

    STANDARD_LDAP = "standard_ldap"
    ACTIVE_DIRECTORY = "active_directory"


class SearchScope(str, Enum):
    BASE = "base"
    LEVEL = "level"
    SUBTREE = "subtree"


class ChangeOperation(str, Enum):
    """LDAP modify operations (RFC 4511 section 4.6)."""

    ADD = "add"
    DELETE = "delete"
    REPLACE = "replace"


class IssueSeverity(str, Enum):
    """ERROR blocks a mutation; WARNING is shown but does not block it."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Stable identifiers so callers can react to a problem without parsing text."""

    READ_ONLY = "read_only"
    OUTSIDE_BASE_DN = "outside_base_dn"
    ENTRY_NOT_FOUND = "entry_not_found"
    ENTRY_EXISTS = "entry_exists"
    PARENT_NOT_FOUND = "parent_not_found"
    HAS_CHILDREN = "has_children"
    PROTECTED_ENTRY = "protected_entry"
    INVALID_TARGET = "invalid_target"
    NO_CHANGE = "no_change"
    RDN_ATTRIBUTE = "rdn_attribute"
    SYSTEM_ATTRIBUTE = "system_attribute"
    UNKNOWN_ATTRIBUTE = "unknown_attribute"
    UNKNOWN_OBJECT_CLASS = "unknown_object_class"
    MISSING_REQUIRED_ATTRIBUTE = "missing_required_attribute"
    SINGLE_VALUED_ATTRIBUTE = "single_valued_attribute"
    VALUE_CONFLICT = "value_conflict"
    INVALID_VALUE = "invalid_value"
    INSECURE_TRANSPORT = "insecure_transport"
    UNSUPPORTED = "unsupported"
    ACCOUNT_STATE = "account_state"
    SCHEMA_UNAVAILABLE = "schema_unavailable"


class ClarificationReason(str, Enum):
    """Why the engine needs an answer before it can continue."""

    # The request leaves out something that cannot be assumed.
    MISSING_INFORMATION = "missing_information"
    # A name matches more than one entry.
    AMBIGUOUS_TARGET = "ambiguous_target"
    # An everyday term could mean more than one attribute.
    AMBIGUOUS_ATTRIBUTE = "ambiguous_attribute"
    # A term matches no known concept and no attribute of this directory.
    UNKNOWN_TERM = "unknown_term"
    # A name matches no entry.
    NOT_FOUND = "not_found"
    # The directory cannot do what was asked.
    UNSUPPORTED_REQUEST = "unsupported_request"


class EntryKind(str, Enum):
    """What sort of entry a request is about."""

    USER = "user"
    GROUP = "group"
    # An organizational unit or other entry that holds entries.
    CONTAINER = "container"
    ANY = "any"


class AccountState(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    LOCKED = "locked"


class MatchOperator(str, Enum):
    """How a search condition compares an attribute with a value."""

    EQUALS = "equals"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    # The attribute has any value; no value is given.
    PRESENT = "present"


class DataLensModel(BaseModel):
    """Base for all models: immutable, strict about unknown fields, quiet about input.

    ``hide_input_in_errors`` keeps rejected input, which may include a password,
    out of validation error text.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        ser_json_bytes="base64",
    )


class DirectoryEntry(DataLensModel):
    """One directory entry: its DN and the attributes that were read or will be written."""

    dn: str = Field(min_length=1)
    attributes: dict[str, tuple[AttributeValue, ...]] = Field(default_factory=dict)

    @classmethod
    def from_ldap(cls, raw: Mapping[str, Any]) -> DirectoryEntry:
        """Build an entry from the ``{"dn": ..., "attributes": ...}`` mapping of LDAPService.

        Attributes that were requested but are absent arrive as empty lists and are
        dropped. Values of unexpected types are kept as text rather than rejected, so
        an unusual syntax cannot make a whole search fail.
        """
        attributes: dict[str, tuple[AttributeValue, ...]] = {}
        for name, values in raw.get("attributes", {}).items():
            if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
                values = [values]
            normalized = tuple(_readable_value(value) for value in values)
            if normalized:
                attributes[name] = normalized
        return cls(dn=raw["dn"], attributes=attributes)

    def get(self, attribute: str) -> tuple[AttributeValue, ...]:
        """Return the attribute's values, matching the name case-insensitively."""
        wanted = attribute.casefold()
        for name, values in self.attributes.items():
            if name.casefold() == wanted:
                return values
        return ()

    def first(self, attribute: str) -> AttributeValue | None:
        values = self.get(attribute)
        return values[0] if values else None

    def has_object_class(self, name: str) -> bool:
        wanted = name.casefold()
        return any(str(value).casefold() == wanted for value in self.get("objectClass"))

    def redacted(self) -> DirectoryEntry:
        """Return a copy that is safe to display, log or send over an API."""
        return DirectoryEntry(
            dn=self.dn,
            attributes={
                name: tuple(REDACTED for _ in values) if is_sensitive(name) else values
                for name, values in self.attributes.items()
            },
        )


def _readable_value(value: Any) -> AttributeValue:
    if isinstance(value, (bool, int, float, str, bytes, datetime, timedelta)):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    return str(value)


class SearchSpec(DataLensModel):
    """A structured LDAP search: where to look, how deep, what to match, what to return."""

    # None means "the adapter's default base DN" (the connection profile's base).
    base_dn: str | None = None
    scope: SearchScope = SearchScope.SUBTREE
    filter: str = "(objectClass=*)"
    attributes: tuple[str, ...] = ("*",)
    # Stop after this many entries; the result reports whether more were available.
    size_limit: int | None = Field(default=None, ge=1)
    page_size: int = Field(default=500, ge=1)

    @field_validator("base_dn")
    @classmethod
    def _valid_base(cls, value: str | None) -> str | None:
        return normalize_dn(value) if value else None

    @field_validator("filter")
    @classmethod
    def _valid_filter(cls, value: str) -> str:
        validate_filter(value)
        return value.strip()

    @field_validator("attributes")
    @classmethod
    def _valid_attributes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("Request at least one attribute, or '*' for all user attributes.")
        for name in value:
            if name not in _ATTRIBUTE_SELECTORS:
                _valid_attribute_name(name)
        return value


class SearchResult(DataLensModel):
    backend: DirectoryBackend
    # The base DN that was actually searched, after applying the adapter default.
    base_dn: str
    spec: SearchSpec
    entries: tuple[DirectoryEntry, ...]
    # True when ``spec.size_limit`` stopped the search before every match was returned.
    truncated: bool = False


class AttributeChange(DataLensModel):
    """One modification of one attribute of an existing entry."""

    attribute: str
    operation: ChangeOperation
    # DELETE without values removes the whole attribute; REPLACE without values does too.
    values: tuple[WritableValue, ...] = ()

    @field_validator("attribute")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return _valid_attribute_name(value)

    @model_validator(mode="after")
    def _valid_values(self) -> AttributeChange:
        if self.operation is ChangeOperation.ADD and not self.values:
            raise ValueError(f"Adding to {self.attribute!r} requires at least one value.")
        _reject_empty_values(self.attribute, self.values)
        return self

    def redacted(self) -> AttributeChange:
        if not is_sensitive(self.attribute):
            return self
        return AttributeChange(
            attribute=self.attribute,
            operation=self.operation,
            values=tuple(REDACTED for _ in self.values),
        )


class CreatePlan(DataLensModel):
    """Add a new entry."""

    kind: Literal["create"] = "create"
    dn: str
    attributes: dict[str, tuple[WritableValue, ...]]

    @field_validator("dn")
    @classmethod
    def _valid_dn(cls, value: str) -> str:
        return normalize_dn(value)

    @field_validator("attributes", mode="before")
    @classmethod
    def _wrap_single_values(cls, value: Any) -> Any:
        # Accept {"cn": "Ada"} as shorthand for {"cn": ["Ada"]}.
        if isinstance(value, Mapping):
            return {
                name: (values,) if isinstance(values, (str, bytes, bool, int)) else values
                for name, values in value.items()
            }
        return value

    @model_validator(mode="after")
    def _valid_attributes(self) -> CreatePlan:
        seen: set[str] = set()
        for name, values in self.attributes.items():
            _valid_attribute_name(name)
            if name.casefold() in seen:
                raise ValueError(f"Attribute {name!r} is listed more than once.")
            seen.add(name.casefold())
            if not values:
                raise ValueError(f"Attribute {name!r} has no values.")
            _reject_empty_values(name, values)
        if "objectclass" not in seen:
            raise ValueError("A new entry needs at least one objectClass value.")
        return self

    def redacted(self) -> CreatePlan:
        return CreatePlan(
            dn=self.dn,
            attributes={
                name: tuple(REDACTED for _ in values) if is_sensitive(name) else values
                for name, values in self.attributes.items()
            },
        )


class UpdatePlan(DataLensModel):
    """Modify attributes of an existing entry. All changes are applied atomically."""

    kind: Literal["update"] = "update"
    dn: str
    changes: tuple[AttributeChange, ...] = Field(min_length=1)

    @field_validator("dn")
    @classmethod
    def _valid_dn(cls, value: str) -> str:
        return normalize_dn(value)

    def redacted(self) -> UpdatePlan:
        return UpdatePlan(dn=self.dn, changes=tuple(change.redacted() for change in self.changes))


class DeletePlan(DataLensModel):
    """Delete an entry, and its whole subtree only when ``recursive`` is set."""

    kind: Literal["delete"] = "delete"
    dn: str
    recursive: bool = False

    @field_validator("dn")
    @classmethod
    def _valid_dn(cls, value: str) -> str:
        return normalize_dn(value)

    def redacted(self) -> DeletePlan:
        return self


class MovePlan(DataLensModel):
    """Rename an entry (``new_rdn``), move it under another parent (``new_superior``), or both."""

    kind: Literal["move"] = "move"
    dn: str
    new_rdn: str | None = None
    new_superior: str | None = None
    # Whether the old naming value is removed from the entry when it is renamed.
    delete_old_rdn: bool = True

    @field_validator("dn", "new_superior")
    @classmethod
    def _valid_dn(cls, value: str | None) -> str | None:
        return normalize_dn(value) if value is not None else None

    @field_validator("new_rdn")
    @classmethod
    def _valid_rdn(cls, value: str | None) -> str | None:
        return normalize_rdn(value) if value is not None else None

    @model_validator(mode="after")
    def _has_destination(self) -> MovePlan:
        if self.new_rdn is None and self.new_superior is None:
            raise ValueError("A move needs a new relative name, a new parent, or both.")
        return self

    def redacted(self) -> MovePlan:
        return self


MutationPlan = Annotated[
    CreatePlan | UpdatePlan | DeletePlan | MovePlan,
    Field(discriminator="kind"),
]


class PlanIssue(DataLensModel):
    """A problem or caution found while checking a plan against the directory."""

    severity: IssueSeverity
    code: IssueCode
    message: str
    # The attribute the issue is about, when there is one.
    attribute: str | None = None


class DryRunResult(DataLensModel):
    """What a mutation would do, computed with read-only lookups and no writes.

    The preview is advisory. The directory may still refuse the change (access
    control, server-side constraints, or a concurrent change after the preview).
    """

    backend: DirectoryBackend
    plan: MutationPlan
    issues: tuple[PlanIssue, ...] = ()
    # The entry as it is now (absent for a create or when it was not found).
    before: DirectoryEntry | None = None
    # The entry as it is expected to be afterwards (absent for a delete).
    after: DirectoryEntry | None = None
    # Every DN that would be written, moved or removed, in the order of processing.
    affected_dns: tuple[str, ...] = ()
    # The DN of the entry once the change is applied (absent for a delete).
    resulting_dn: str | None = None
    summary: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_apply(self) -> bool:
        """False when any issue is an ERROR; such a plan is never sent to the directory."""
        return not any(issue.severity is IssueSeverity.ERROR for issue in self.issues)


class MutationResult(DataLensModel):
    """Outcome of a typed mutation call."""

    # True when the call was a preview only.
    dry_run: bool
    # True only when the directory was changed.
    applied: bool
    preview: DryRunResult


class ClarificationOption(DataLensModel):
    value: str
    label: str


class ClarificationPrompt(DataLensModel):
    """A question to put to the person when a request cannot be carried out as given."""

    reason: ClarificationReason
    question: str = Field(min_length=1)
    # The request field the answer fills in, such as "new_superior" or an attribute name.
    field: str | None = None
    options: tuple[ClarificationOption, ...] = ()


class AttributeResolution(DataLensModel):
    """The directory attributes an everyday term stands for on this server.

    More than one attribute means the term is broad ("phone"): a search may match
    any of them, but a change needs the person to choose one.
    """

    term: str
    # Registry concept the term belongs to; None when it named an attribute directly.
    concept: str | None = None
    # Attribute names as the server spells them, most usual first.
    attributes: tuple[str, ...] = Field(min_length=1)
    # True when the values are DNs of other entries (manager, member).
    references_entry: bool = False


class SearchCondition(DataLensModel):
    """One condition of a search, in everyday terms: "email contains example.com"."""

    term: str = Field(min_length=1)
    operator: MatchOperator = MatchOperator.EQUALS
    value: str | None = None
    negate: bool = False

    @model_validator(mode="after")
    def _value_matches_operator(self) -> SearchCondition:
        if self.operator is MatchOperator.PRESENT:
            if self.value is not None:
                raise ValueError("A 'present' condition takes no value.")
        elif not self.value:
            raise ValueError(f"A '{self.operator.value}' condition needs a value.")
        return self


class RequestedChange(DataLensModel):
    """One requested change to an entry, in everyday terms: "set department to Sales"."""

    term: str = Field(min_length=1)
    operation: ChangeOperation = ChangeOperation.REPLACE
    values: tuple[WritableValue, ...] = ()

    @field_validator("values", mode="before")
    @classmethod
    def _wrap_single_value(cls, value: Any) -> Any:
        return (value,) if isinstance(value, (str, bytes, bool, int)) else value


class PreparedSearch(DataLensModel):
    """A search translated from everyday terms, or the questions that block it."""

    clarifications: tuple[ClarificationPrompt, ...] = ()
    spec: SearchSpec | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        return self.spec is not None and not self.clarifications


class PreparedMutation(DataLensModel):
    """A change translated from everyday terms, with its preview, or what blocks it.

    ``plan`` is what an adapter executes and may hold a password, so it is left out
    of ``repr`` and of serialized output. ``preview`` is the display-safe view.
    """

    clarifications: tuple[ClarificationPrompt, ...] = ()
    plan: MutationPlan | None = Field(default=None, exclude=True, repr=False)
    preview: DryRunResult | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """True when nothing needs answering and the preview found no blocking issue."""
        return (
            self.plan is not None
            and not self.clarifications
            and self.preview is not None
            and self.preview.can_apply
        )


class AccountStatus(DataLensModel):
    """Whether an account can sign in, as far as the entry's attributes show.

    ``None`` means the entry did not carry the attributes needed to tell.
    """

    enabled: bool | None = None
    locked: bool | None = None
    # Backend-specific detail, for example decoded userAccountControl flag names.
    flags: tuple[str, ...] = ()
