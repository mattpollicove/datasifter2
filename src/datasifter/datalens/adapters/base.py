"""Backend-neutral directory operations for the Data Lens engine.

:class:`DirectoryAdapter` exposes one search method and four typed mutation
methods. Transport (TLS, certificate pinning, bind, paging, redacted protocol
logging, cancellation) is delegated to the existing
:class:`~datasifter.directory.service.LDAPService`, so that security code exists
once. The adapter adds what the service does not have: validated models, checks
against the live directory before a write, previews, and the confirmation gate.

Every mutation method follows the same steps:

1. Build and validate a plan from the arguments (no directory contact).
2. Refuse immediately when a real change is requested in read-only mode.
3. Build a preview with read-only lookups: does the target exist, is the parent
   there, does the schema allow it, and so on. Nothing is written.
4. ``dry_run=True`` (the default) returns the preview.
5. Otherwise the change is sent only if the preview has no ERROR issue *and* the
   caller passed ``confirmed=True``.

Subclasses supply the directory-specific knowledge through the ``_validate_*``
hooks and the class attributes below.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from threading import Event
from typing import Any, ClassVar

from ldap3.core.exceptions import LDAPNoSuchObjectResult

from datasifter.datalens.dn import (
    InvalidDnError,
    dn_depth,
    is_same_or_descendant,
    join_dn,
    rdn_pairs,
    same_dn,
    split_dn,
)
from datasifter.datalens.errors import (
    ConfirmationRequiredError,
    MutationRejectedError,
    PreviewChangedError,
    ReadOnlyDirectoryError,
)
from datasifter.datalens.models import (
    AccountState,
    AccountStatus,
    AttributeChange,
    AttributeValue,
    ChangeOperation,
    CreatePlan,
    DeletePlan,
    DirectoryBackend,
    DirectoryEntry,
    DryRunResult,
    EntryKind,
    IssueCode,
    IssueSeverity,
    MovePlan,
    MutationResult,
    PlanIssue,
    SearchResult,
    SearchSpec,
    UpdatePlan,
    WritableValue,
    base_attribute_name,
    is_sensitive,
)
from datasifter.datalens.schema import DirectorySchema
from datasifter.directory.filters import escape_filter_value
from datasifter.directory.service import LDAPOperationError, LDAPService

# Attribute selector meaning "return the entry without any attributes".
_NO_ATTRIBUTES = ("1.1",)


class IssueCollector:
    """Collects the problems found while a preview is built."""

    def __init__(self) -> None:
        self.items: list[PlanIssue] = []

    def error(self, code: IssueCode, message: str, attribute: str | None = None) -> None:
        self.items.append(
            PlanIssue(
                severity=IssueSeverity.ERROR, code=code, message=message, attribute=attribute
            )
        )

    def warning(self, code: IssueCode, message: str, attribute: str | None = None) -> None:
        self.items.append(
            PlanIssue(
                severity=IssueSeverity.WARNING, code=code, message=message, attribute=attribute
            )
        )


def _comparable(value: AttributeValue) -> str | bytes:
    """Reduce a value to a form in which "the same value" can be recognised.

    Most directory strings match case-insensitively, and a number read as ``512``
    equals one written as ``"512"``.
    """
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").casefold()
        except UnicodeDecodeError:
            return value
    return str(value).casefold()


def _contains(values: Sequence[AttributeValue], value: AttributeValue) -> bool:
    wanted = _comparable(value)
    return any(_comparable(candidate) == wanted for candidate in values)


def _same_entry(first: str, second: str) -> bool:
    """Compare DNs, tolerating server-returned DNs that the strict parser rejects."""
    try:
        return same_dn(first, second)
    except InvalidDnError:
        return first.casefold() == second.casefold()


def _find_attribute(attributes: Mapping[str, Any], attribute: str) -> str | None:
    """Return the key under which ``attribute`` is stored, ignoring case."""
    wanted = attribute.casefold()
    return next((name for name in attributes if name.casefold() == wanted), None)


class DirectoryAdapter(ABC):
    """Typed search and mutation operations for one directory connection profile."""

    backend: ClassVar[DirectoryBackend]
    # Object classes a new user or group entry normally carries on this backend.
    user_object_classes: ClassVar[tuple[str, ...]]
    group_object_classes: ClassVar[tuple[str, ...]]
    # Attributes that identify a person, in the order they are usually tried.
    identity_attributes: ClassVar[tuple[str, ...]]
    # Attributes that identify a group by name.
    group_identity_attributes: ClassVar[tuple[str, ...]] = ("cn",)
    # The group-side attribute that lists members.
    group_member_attribute: ClassVar[str]
    # (object class, naming attribute) of the entries that hold other entries.
    container_classes: ClassVar[tuple[tuple[str, str], ...]] = (("organizationalUnit", "ou"),)
    # Attributes :meth:`account_status` looks at. Request them through
    # :meth:`known_attributes`, because a server defines only some of them.
    account_status_attributes: ClassVar[tuple[str, ...]]
    # Lower-cased attributes the directory maintains itself; clients cannot write them.
    system_attributes: ClassVar[frozenset[str]]
    # Lower-cased attributes the schema marks as required but the server fills in.
    server_supplied_attributes: ClassVar[frozenset[str]] = frozenset()
    # Attributes read for each subordinate entry before a recursive delete.
    _descendant_attributes: ClassVar[tuple[str, ...]] = _NO_ATTRIBUTES

    def __init__(self, service: LDAPService, *, read_only: bool = False) -> None:
        """Wrap ``service``.

        ``read_only=True`` blocks changes even when the profile itself is writable,
        for callers that must only ever read (for example a search-only API).
        """
        self._service = service
        self._read_only = read_only
        self._schema_cache: DirectorySchema | None = None

    # ------------------------------------------------------------------
    # Facts about the connection
    # ------------------------------------------------------------------
    @property
    def base_dn(self) -> str:
        """The profile's base DN: the default search base and the boundary for changes."""
        return self._service.profile.base_dn

    @property
    def read_only(self) -> bool:
        return self._read_only or self._service.profile.read_only

    # ------------------------------------------------------------------
    # Backend knowledge
    # ------------------------------------------------------------------
    @abstractmethod
    def _user_class_filter(self) -> str:
        """Return a filter that matches person accounts on this backend."""

    @abstractmethod
    def account_status(self, entry: DirectoryEntry) -> AccountStatus:
        """Report whether the account is enabled and locked, from the entry's attributes.

        Read the entry with :attr:`account_status_attributes` first.
        """

    def user_filter(self, identifier: str | None = None) -> str:
        """Return a filter for person accounts, optionally matching one identifier exactly.

        The identifier is compared with every attribute in :attr:`identity_attributes`
        and is escaped, so it cannot change the structure of the filter.
        """
        people = self._user_class_filter()
        if identifier is None:
            return people
        value = escape_filter_value(identifier)
        identity = "".join(f"({attribute}={value})" for attribute in self.identity_attributes)
        return f"(&{people}(|{identity}))"

    @abstractmethod
    def _group_class_filter(self) -> str:
        """Return a filter that matches group entries on this backend."""

    @abstractmethod
    def account_state_filter(
        self, state: AccountState, *, cancelled: Event | None = None
    ) -> str | None:
        """Return a filter for accounts in ``state``.

        None means this directory offers no attribute from which to tell.
        """

    @abstractmethod
    def account_state_change(
        self, entry: DirectoryEntry, *, disabled: bool, cancelled: Event | None = None
    ) -> AttributeChange | None:
        """Return the change that disables or enables the account.

        Read the entry with :attr:`account_status_attributes` first. None means
        this directory offers no attribute with which to do it.
        """

    def group_filter(self, identifier: str | None = None) -> str:
        """Return a filter for groups, optionally matching one name exactly."""
        groups = self._group_class_filter()
        if identifier is None:
            return groups
        value = escape_filter_value(identifier)
        names = "".join(f"({attribute}={value})" for attribute in self.group_identity_attributes)
        return f"(&{groups}(|{names}))"

    def container_filter(self, name: str | None = None) -> str:
        """Return a filter for organizational units and containers, optionally by name."""
        if name is None:
            alternatives = [f"(objectClass={object_class})" for object_class, _ in self.container_classes]
        else:
            value = escape_filter_value(name)
            alternatives = [
                f"(&(objectClass={object_class})({attribute}={value}))"
                for object_class, attribute in self.container_classes
            ]
        return alternatives[0] if len(alternatives) == 1 else f"(|{''.join(alternatives)})"

    def entry_filter(self, kind: EntryKind, identifier: str) -> str:
        """Return a filter that matches entries of ``kind`` identified exactly by ``identifier``."""
        if kind is EntryKind.USER:
            return self.user_filter(identifier)
        if kind is EntryKind.GROUP:
            return self.group_filter(identifier)
        if kind is EntryKind.CONTAINER:
            return self.container_filter(identifier)
        return (
            f"(|{self.user_filter(identifier)}{self.group_filter(identifier)}"
            f"{self.container_filter(identifier)})"
        )

    def member_attribute(self, group: DirectoryEntry) -> str:
        """Return the attribute that lists the members of ``group``."""
        return self.group_member_attribute

    def known_attributes(
        self, attributes: Sequence[str], *, cancelled: Event | None = None
    ) -> tuple[str, ...]:
        """Return the attributes the server schema defines, in the given order.

        A search may only request attributes the server knows, so lists such as
        :attr:`account_status_attributes`, which name attributes of several server
        families, must be passed through this first. Without a readable schema
        the list is returned unchanged.
        """
        schema = self.schema(cancelled=cancelled)
        if schema is None:
            return tuple(attributes)
        return tuple(name for name in attributes if schema.knows_attribute(name))

    def schema(self, *, cancelled: Event | None = None) -> DirectorySchema | None:
        """Return the server schema, read once per adapter, or None when it cannot be read."""
        if self._schema_cache is None:
            try:
                self._schema_cache = DirectorySchema.from_options(
                    self._service.schema_options(cancelled)
                )
            except LDAPOperationError:
                return None
        return self._schema_cache

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search_entries(
        self, spec: SearchSpec, *, cancelled: Event | None = None
    ) -> SearchResult:
        """Run a structured search and return typed entries.

        Results are paged. When ``spec.size_limit`` is set, the search stops early
        and ``truncated`` tells whether more entries matched.
        """
        base_dn = spec.base_dn or self.base_dn
        if not base_dn:
            raise ValueError("The search has no base DN and the profile does not define one.")
        limit = spec.size_limit
        raw_entries = self._service.search(
            base_dn,
            spec.filter,
            attributes=list(spec.attributes),
            scope=spec.scope.value,
            page_size=spec.page_size if limit is None else min(spec.page_size, limit + 1),
            cancelled=cancelled,
            # One more than the limit shows whether the result was cut short.
            max_entries=None if limit is None else limit + 1,
        )
        truncated = limit is not None and len(raw_entries) > limit
        return SearchResult(
            backend=self.backend,
            base_dn=base_dn,
            spec=spec,
            entries=tuple(DirectoryEntry.from_ldap(raw) for raw in raw_entries[:limit]),
            truncated=truncated,
        )

    def get_entry(
        self,
        dn: str,
        attributes: Sequence[str] = ("*",),
        *,
        cancelled: Event | None = None,
    ) -> DirectoryEntry | None:
        """Read one entry by DN, or return None when it does not exist."""
        try:
            raw_entries = self._service.search(
                dn,
                "(objectClass=*)",
                attributes=list(attributes),
                scope="base",
                cancelled=cancelled,
            )
        except LDAPOperationError as error:
            # The service wraps ldap3 errors; "no such object" is an answer, not a failure.
            if isinstance(error.__cause__, LDAPNoSuchObjectResult):
                return None
            raise
        return DirectoryEntry.from_ldap(raw_entries[0]) if raw_entries else None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def create_entry(
        self,
        dn: str,
        attributes: Mapping[str, Sequence[WritableValue] | WritableValue],
        *,
        dry_run: bool = True,
        confirmed: bool = False,
        cancelled: Event | None = None,
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        """Add a new entry at ``dn`` with the given attributes.

        The naming value from the DN is added to the attributes when it is missing.
        Returns a preview unless ``dry_run=False`` and ``confirmed=True`` are both given.
        """
        plan = CreatePlan(dn=dn, attributes=attributes)
        self._refuse_if_read_only(dry_run)
        issues = IssueCollector()
        self._check_target(plan.dn, issues)
        attributes_to_write = self._attributes_with_naming_value(plan, issues)

        if self.get_entry(plan.dn, _NO_ATTRIBUTES, cancelled=cancelled) is not None:
            issues.error(IssueCode.ENTRY_EXISTS, f"An entry already exists at {plan.dn}.")
        parent = split_dn(plan.dn)[1]
        if not parent:
            issues.error(IssueCode.INVALID_TARGET, "A top-level entry cannot be created here.")
        elif self.get_entry(parent, _NO_ATTRIBUTES, cancelled=cancelled) is None:
            issues.error(IssueCode.PARENT_NOT_FOUND, f"The parent entry {parent} does not exist.")

        self._check_schema_for_create(attributes_to_write, issues, cancelled)
        self._validate_create(plan, attributes_to_write, issues)

        entry = DirectoryEntry(dn=plan.dn, attributes=attributes_to_write)
        classes = ", ".join(str(value) for value in entry.get("objectClass"))
        preview = DryRunResult(
            backend=self.backend,
            plan=CreatePlan(dn=plan.dn, attributes=attributes_to_write).redacted(),
            issues=tuple(issues.items),
            after=entry.redacted(),
            affected_dns=(plan.dn,),
            resulting_dn=plan.dn,
            summary=f"Create {plan.dn} ({classes}) with {len(attributes_to_write)} attributes.",
        )
        return self._finish(
            preview,
            dry_run,
            confirmed,
            lambda: self._service.add_entry(
                plan.dn,
                {
                    name: self._wire_values(name, values)
                    for name, values in attributes_to_write.items()
                },
                cancelled,
            ),
            expected_preview,
        )

    def update_entry(
        self,
        dn: str,
        changes: Sequence[AttributeChange | Mapping[str, Any]],
        *,
        dry_run: bool = True,
        confirmed: bool = False,
        cancelled: Event | None = None,
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        """Apply attribute changes to an existing entry as one atomic modification.

        Renaming is not an update: changing the naming attribute is refused, use
        :meth:`move_entry`.
        """
        plan = UpdatePlan(dn=dn, changes=tuple(changes))
        self._refuse_if_read_only(dry_run)
        issues = IssueCollector()
        self._check_target(plan.dn, issues)

        before = self.get_entry(plan.dn, cancelled=cancelled)
        after: DirectoryEntry | None = None
        if before is None:
            issues.error(IssueCode.ENTRY_NOT_FOUND, f"No entry exists at {plan.dn}.")
        else:
            after = self._simulate_update(plan, before, issues, cancelled)
            self._validate_update(plan, before, after, issues)

        described = "; ".join(
            f"{change.operation.value} {change.attribute}" for change in plan.changes
        )
        preview = DryRunResult(
            backend=self.backend,
            plan=plan.redacted(),
            issues=tuple(issues.items),
            before=before.redacted() if before else None,
            after=after.redacted() if after else None,
            affected_dns=(plan.dn,),
            resulting_dn=plan.dn,
            summary=f"Update {plan.dn}: {described}.",
        )

        def apply() -> None:
            # One modify request, so either every change is applied or none is.
            wire: dict[str, list[tuple[str, list[Any]]]] = {}
            for change in plan.changes:
                wire.setdefault(change.attribute, []).append(
                    (change.operation.value, self._wire_values(change.attribute, change.values))
                )
            self._service.modify_entry(plan.dn, wire, cancelled)

        return self._finish(preview, dry_run, confirmed, apply, expected_preview)

    def delete_entry(
        self,
        dn: str,
        *,
        recursive: bool = False,
        dry_run: bool = True,
        confirmed: bool = False,
        cancelled: Event | None = None,
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        """Delete an entry.

        An entry with subordinate entries is refused unless ``recursive=True``; the
        preview then lists every DN that would be removed, deepest first.
        """
        plan = DeletePlan(dn=dn, recursive=recursive)
        self._refuse_if_read_only(dry_run)
        issues = IssueCollector()
        self._check_target(plan.dn, issues)
        self._check_anchor_entries(plan.dn, issues, removing=True)

        before = self.get_entry(plan.dn, cancelled=cancelled)
        affected: tuple[str, ...] = (plan.dn,)
        if before is None:
            issues.error(IssueCode.ENTRY_NOT_FOUND, f"No entry exists at {plan.dn}.")
        else:
            descendants = self._descendants(plan.dn, cancelled, only_first=not plan.recursive)
            if descendants and not plan.recursive:
                issues.error(
                    IssueCode.HAS_CHILDREN,
                    f"{plan.dn} has subordinate entries. Delete them first, or request a "
                    "recursive delete to remove the whole subtree.",
                )
            elif descendants:
                deepest_first = sorted(
                    (entry.dn for entry in descendants), key=dn_depth, reverse=True
                )
                affected = (*deepest_first, plan.dn)
            self._validate_delete(plan, before, descendants, issues)

        subordinate_count = len(affected) - 1
        summary = f"Delete {plan.dn}"
        if subordinate_count:
            summary += f" and {subordinate_count} subordinate entries"
        preview = DryRunResult(
            backend=self.backend,
            plan=plan,
            issues=tuple(issues.items),
            before=before.redacted() if before else None,
            affected_dns=affected,
            summary=summary + ".",
        )

        def apply_delete() -> None:
            if expected_preview is not None and plan.recursive:
                for affected_dn in preview.affected_dns:
                    self._service.delete_entry(affected_dn, cancelled=cancelled)
            else:
                self._service.delete_entry(plan.dn, recursive=plan.recursive, cancelled=cancelled)

        return self._finish(
            preview,
            dry_run,
            confirmed,
            apply_delete,
            expected_preview,
        )

    def move_entry(
        self,
        dn: str,
        *,
        new_rdn: str | None = None,
        new_superior: str | None = None,
        delete_old_rdn: bool = True,
        dry_run: bool = True,
        confirmed: bool = False,
        cancelled: Event | None = None,
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        """Rename an entry, move it beneath another parent, or both.

        ``new_rdn`` is the new relative name (``cn=New Name``); ``new_superior`` is
        the DN of the new parent. Subordinate entries move with the entry.
        """
        plan = MovePlan(
            dn=dn, new_rdn=new_rdn, new_superior=new_superior, delete_old_rdn=delete_old_rdn
        )
        self._refuse_if_read_only(dry_run)
        issues = IssueCollector()
        current_rdn, current_parent = split_dn(plan.dn)
        target_rdn = plan.new_rdn or current_rdn
        target_parent = plan.new_superior or current_parent
        target_dn = join_dn(target_rdn, target_parent) if target_parent else target_rdn
        # A change of case only ("cn=ada" -> "cn=Ada") is still a rename.
        renamed = target_rdn != current_rdn
        reparented = bool(current_parent) and not same_dn(target_parent, current_parent)

        self._check_target(plan.dn, issues)
        if reparented:
            self._check_target(target_dn, issues)
        self._check_anchor_entries(plan.dn, issues, removing=False)
        if not current_parent:
            issues.error(IssueCode.INVALID_TARGET, "A top-level entry cannot be moved.")
        elif not renamed and not reparented:
            issues.error(
                IssueCode.NO_CHANGE, f"{plan.dn} already has that name and location."
            )
        if reparented and is_same_or_descendant(target_parent, plan.dn):
            issues.error(
                IssueCode.INVALID_TARGET, f"{plan.dn} cannot be moved beneath itself."
            )

        before = self.get_entry(plan.dn, cancelled=cancelled)
        after: DirectoryEntry | None = None
        if before is None:
            issues.error(IssueCode.ENTRY_NOT_FOUND, f"No entry exists at {plan.dn}.")
        else:
            if reparented and (
                self.get_entry(target_parent, _NO_ATTRIBUTES, cancelled=cancelled) is None
            ):
                issues.error(
                    IssueCode.PARENT_NOT_FOUND,
                    f"The destination parent {target_parent} does not exist.",
                )
            if not same_dn(target_dn, plan.dn) and (
                self.get_entry(target_dn, _NO_ATTRIBUTES, cancelled=cancelled) is not None
            ):
                issues.error(IssueCode.ENTRY_EXISTS, f"An entry already exists at {target_dn}.")
            if self._descendants(plan.dn, cancelled, only_first=True):
                issues.warning(
                    IssueCode.HAS_CHILDREN,
                    f"{plan.dn} has subordinate entries; they move with it.",
                )
            after = self._entry_after_move(plan, before, current_rdn, target_rdn, target_dn)
            self._validate_move(plan, before, current_rdn, target_rdn, issues)

        if renamed and reparented:
            summary = f"Move {plan.dn} to {target_dn}."
        elif reparented:
            summary = f"Move {plan.dn} under {target_parent}."
        else:
            summary = f"Rename {plan.dn} to {target_rdn}."
        preview = DryRunResult(
            backend=self.backend,
            plan=plan,
            issues=tuple(issues.items),
            before=before.redacted() if before else None,
            after=after.redacted() if after else None,
            affected_dns=(plan.dn,),
            resulting_dn=target_dn,
            summary=summary,
        )
        return self._finish(
            preview,
            dry_run,
            confirmed,
            lambda: self._service.move_entry(
                plan.dn,
                target_rdn,
                new_superior=target_parent if reparented else None,
                delete_old_rdn=plan.delete_old_rdn,
                cancelled=cancelled,
            ),
            expected_preview,
        )

    def execute(
        self,
        plan: CreatePlan | UpdatePlan | DeletePlan | MovePlan,
        *,
        dry_run: bool = True,
        confirmed: bool = False,
        cancelled: Event | None = None,
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        """Run a plan through the matching typed operation, with the same gate."""
        gate = {
            "dry_run": dry_run,
            "confirmed": confirmed,
            "cancelled": cancelled,
            "expected_preview": expected_preview,
        }
        if isinstance(plan, CreatePlan):
            return self.create_entry(plan.dn, plan.attributes, **gate)
        if isinstance(plan, UpdatePlan):
            return self.update_entry(plan.dn, plan.changes, **gate)
        if isinstance(plan, DeletePlan):
            return self.delete_entry(plan.dn, recursive=plan.recursive, **gate)
        return self.move_entry(
            plan.dn,
            new_rdn=plan.new_rdn,
            new_superior=plan.new_superior,
            delete_old_rdn=plan.delete_old_rdn,
            **gate,
        )

    # ------------------------------------------------------------------
    # Hooks for backend-specific rules. The base implementations accept everything.
    # ------------------------------------------------------------------
    def _validate_create(
        self,
        plan: CreatePlan,
        attributes: Mapping[str, tuple[WritableValue, ...]],
        issues: IssueCollector,
    ) -> None:
        """Add backend-specific issues for a create. ``attributes`` is what will be written."""

    def _validate_update(
        self, plan: UpdatePlan, before: DirectoryEntry, after: DirectoryEntry, issues: IssueCollector
    ) -> None:
        """Add backend-specific issues for an update."""

    def _validate_delete(
        self,
        plan: DeletePlan,
        before: DirectoryEntry,
        descendants: Sequence[DirectoryEntry],
        issues: IssueCollector,
    ) -> None:
        """Add backend-specific issues for a delete."""

    def _validate_move(
        self,
        plan: MovePlan,
        before: DirectoryEntry,
        current_rdn: str,
        target_rdn: str,
        issues: IssueCollector,
    ) -> None:
        """Add backend-specific issues for a rename or move."""

    def _wire_values(self, attribute: str, values: Sequence[WritableValue]) -> list[Any]:
        """Convert values to the form sent to the directory."""
        return [_wire_value(value) for value in values]

    # ------------------------------------------------------------------
    # Shared machinery
    # ------------------------------------------------------------------
    def _refuse_if_read_only(self, dry_run: bool) -> None:
        # Checked before any lookup, so a read-only profile opens no connection
        # for a change that could never be applied.
        if not dry_run and self.read_only:
            raise ReadOnlyDirectoryError(
                "This directory connection is read-only; changes are blocked."
            )

    @staticmethod
    def _finish(
        preview: DryRunResult,
        dry_run: bool,
        confirmed: bool,
        apply: Callable[[], None],
        expected_preview: DryRunResult | None = None,
    ) -> MutationResult:
        if dry_run:
            return MutationResult(dry_run=True, applied=False, preview=preview)
        if not preview.can_apply:
            raise MutationRejectedError(preview)
        if not confirmed:
            raise ConfirmationRequiredError(preview)
        if expected_preview is not None and preview != expected_preview:
            raise PreviewChangedError()
        apply()
        return MutationResult(dry_run=False, applied=True, preview=preview)

    def _check_target(self, dn: str, issues: IssueCollector) -> None:
        """Checks that apply to every entry a mutation writes to."""
        if self.read_only and not any(
            issue.code is IssueCode.READ_ONLY for issue in issues.items
        ):
            issues.error(
                IssueCode.READ_ONLY,
                "This directory connection is read-only; the change cannot be applied.",
            )
        if self.base_dn and not is_same_or_descendant(dn, self.base_dn):
            issues.error(
                IssueCode.OUTSIDE_BASE_DN,
                f"{dn} is outside the profile's base DN {self.base_dn}.",
            )

    def _check_anchor_entries(self, dn: str, issues: IssueCollector, *, removing: bool) -> None:
        """Protect the entries the connection itself depends on."""
        action = "deleted" if removing else "moved"
        if self.base_dn and is_same_or_descendant(self.base_dn, dn):
            issues.error(
                IssueCode.PROTECTED_ENTRY,
                f"{dn} is or contains the profile's base DN and cannot be {action}.",
            )
        bind_dn = self._service.profile.bind_dn
        try:
            holds_bind_entry = bool(bind_dn) and is_same_or_descendant(bind_dn, dn)
        except InvalidDnError:
            # Some directories accept a bind name that is not a DN, such as a UPN.
            holds_bind_entry = False
        if holds_bind_entry and removing:
            issues.error(
                IssueCode.PROTECTED_ENTRY,
                f"{dn} is or contains the account this connection signs in with.",
            )
        elif holds_bind_entry:
            issues.warning(
                IssueCode.PROTECTED_ENTRY,
                f"{dn} is or contains the account this connection signs in with; "
                "the profile's bind DN must be updated afterwards.",
            )

    def _descendants(
        self, dn: str, cancelled: Event | None, *, only_first: bool
    ) -> list[DirectoryEntry]:
        """Return the entries beneath ``dn``; with ``only_first`` just enough to know any exist."""
        if only_first:
            raw_entries = self._service.search(
                dn,
                "(objectClass=*)",
                attributes=list(_NO_ATTRIBUTES),
                scope="level",
                cancelled=cancelled,
                # Two, not one: the entry itself is dropped below, and ldap3's mock
                # directory wrongly includes it in a one-level search.
                max_entries=2,
            )
        else:
            raw_entries = self._service.search(
                dn,
                "(objectClass=*)",
                attributes=list(self._descendant_attributes),
                scope="subtree",
                cancelled=cancelled,
            )
        return [
            DirectoryEntry.from_ldap(raw)
            for raw in raw_entries
            if not _same_entry(raw["dn"], dn)
        ]

    def _schema(self, issues: IssueCollector, cancelled: Event | None) -> DirectorySchema | None:
        """Return the server schema, noting in ``issues`` when it is unavailable."""
        schema = self.schema(cancelled=cancelled)
        if schema is None:
            issues.warning(
                IssueCode.SCHEMA_UNAVAILABLE,
                "The directory schema could not be read, so attribute and object class "
                "checks were skipped.",
            )
        return schema

    def _attributes_with_naming_value(
        self, plan: CreatePlan, issues: IssueCollector
    ) -> dict[str, tuple[WritableValue, ...]]:
        """Return the attributes to write, including the naming value taken from the DN.

        Directories require the entry to contain the value it is named by. Adding
        it here follows the DN the caller gave and is not a guess.
        """
        attributes = dict(plan.attributes)
        for attribute, value in rdn_pairs(plan.dn):
            name = _find_attribute(attributes, attribute)
            if name is None:
                attributes[attribute] = (value,)
            elif not _contains(attributes[name], value):
                issues.error(
                    IssueCode.RDN_ATTRIBUTE,
                    f"The entry is named {attribute}={value}, but its {name} attribute "
                    "does not include that value.",
                    attribute=name,
                )
        return attributes

    def _check_schema_for_create(
        self,
        attributes: Mapping[str, tuple[WritableValue, ...]],
        issues: IssueCollector,
        cancelled: Event | None,
    ) -> None:
        schema = self._schema(issues, cancelled)
        if schema is None:
            return
        class_attribute = _find_attribute(attributes, "objectClass")
        classes = [str(value) for value in attributes[class_attribute]] if class_attribute else []
        for name in classes:
            if not schema.knows_class(name):
                issues.error(
                    IssueCode.UNKNOWN_OBJECT_CLASS,
                    f"The directory schema has no object class named {name}.",
                    attribute="objectClass",
                )
        present = set()
        for name, values in attributes.items():
            present.add(schema.attribute_id(name))
            if not schema.knows_attribute(name):
                issues.error(
                    IssueCode.UNKNOWN_ATTRIBUTE,
                    f"The directory schema has no attribute named {name}.",
                    attribute=name,
                )
            elif len(values) > 1 and schema.is_single_valued(name):
                issues.error(
                    IssueCode.SINGLE_VALUED_ATTRIBUTE,
                    f"{name} accepts a single value, but {len(values)} were given.",
                    attribute=name,
                )
        for identifier, name in schema.required_attributes(classes).items():
            if identifier not in present and name.casefold() not in self.server_supplied_attributes:
                issues.error(
                    IssueCode.MISSING_REQUIRED_ATTRIBUTE,
                    f"{name} is required by the entry's object classes.",
                    attribute=name,
                )

    def _simulate_update(
        self,
        plan: UpdatePlan,
        before: DirectoryEntry,
        issues: IssueCollector,
        cancelled: Event | None,
    ) -> DirectoryEntry:
        """Work out the entry after the changes, and report changes that cannot succeed."""
        schema = self._schema(issues, cancelled)
        # Lower-cased attribute name -> (name as stored or given, current values).
        state: dict[str, tuple[str, list[AttributeValue]]] = {
            name.casefold(): (name, list(values)) for name, values in before.attributes.items()
        }
        touched: set[str] = set()
        for change in plan.changes:
            attribute = change.attribute
            if base_attribute_name(attribute) in self.system_attributes:
                issues.error(
                    IssueCode.SYSTEM_ATTRIBUTE,
                    f"{attribute} is maintained by the directory and cannot be changed directly.",
                    attribute=attribute,
                )
                continue
            if schema is not None and not schema.knows_attribute(attribute):
                issues.error(
                    IssueCode.UNKNOWN_ATTRIBUTE,
                    f"The directory schema has no attribute named {attribute}.",
                    attribute=attribute,
                )
                continue
            key = attribute.casefold()
            name, values = state.get(key, (attribute, []))
            # Stored passwords are hashed, so comparing them with new values means nothing.
            comparable = not is_sensitive(attribute)
            if change.operation is ChangeOperation.REPLACE:
                values = list(change.values)
            elif change.operation is ChangeOperation.ADD:
                for value in change.values:
                    if comparable and _contains(values, value):
                        issues.warning(
                            IssueCode.VALUE_CONFLICT,
                            f"{name} already has one of the values being added; the "
                            "directory may refuse the change.",
                            attribute=attribute,
                        )
                    else:
                        values.append(value)
            elif not change.values:
                if not values:
                    issues.warning(
                        IssueCode.VALUE_CONFLICT,
                        f"{name} has no values to remove; the directory may refuse the change.",
                        attribute=attribute,
                    )
                values = []
            else:
                for value in change.values:
                    if comparable and not _contains(values, value):
                        issues.warning(
                            IssueCode.VALUE_CONFLICT,
                            f"{name} does not have one of the values being removed; the "
                            "directory may refuse the change.",
                            attribute=attribute,
                        )
                    wanted = _comparable(value)
                    values = [kept for kept in values if _comparable(kept) != wanted]
            state[key] = (name, values)
            touched.add(key)

        after = DirectoryEntry(
            dn=before.dn,
            attributes={name: tuple(values) for name, values in state.values() if values},
        )
        required = (
            schema.required_attributes([str(value) for value in after.get("objectClass")])
            if schema is not None
            else {}
        )
        naming = {attribute.casefold(): value for attribute, value in rdn_pairs(plan.dn)}
        for key in sorted(touched):
            name, values = state[key]
            if key in naming and not _contains(values, naming[key]):
                issues.error(
                    IssueCode.RDN_ATTRIBUTE,
                    f"{name} names the entry; its value {naming[key]!r} cannot be removed. "
                    "Rename the entry instead.",
                    attribute=name,
                )
            elif not values and schema is not None and schema.attribute_id(name) in required:
                issues.error(
                    IssueCode.MISSING_REQUIRED_ATTRIBUTE,
                    f"{name} is required by the entry's object classes and cannot be removed.",
                    attribute=name,
                )
            if len(values) > 1 and schema is not None and schema.is_single_valued(name):
                issues.error(
                    IssueCode.SINGLE_VALUED_ATTRIBUTE,
                    f"{name} accepts a single value, but the change would leave {len(values)}.",
                    attribute=name,
                )
        if touched and _same_attributes(before, after):
            issues.warning(IssueCode.NO_CHANGE, "The entry already has these values.")
        return after

    @staticmethod
    def _entry_after_move(
        plan: MovePlan,
        before: DirectoryEntry,
        current_rdn: str,
        target_rdn: str,
        target_dn: str,
    ) -> DirectoryEntry:
        """Predict the entry after a rename: the naming values change with the RDN."""
        attributes: dict[str, list[AttributeValue]] = {
            name: list(values) for name, values in before.attributes.items()
        }
        if target_rdn != current_rdn:
            new_pairs = rdn_pairs(target_rdn)
            if plan.delete_old_rdn:
                for attribute, value in rdn_pairs(current_rdn):
                    name = _find_attribute(attributes, attribute)
                    if name is not None:
                        wanted = _comparable(value)
                        attributes[name] = [
                            kept for kept in attributes[name] if _comparable(kept) != wanted
                        ]
            for attribute, value in new_pairs:
                name = _find_attribute(attributes, attribute) or attribute
                values = attributes.setdefault(name, [])
                # A change of case replaces the stored value rather than adding one.
                wanted = _comparable(value)
                attributes[name] = [kept for kept in values if _comparable(kept) != wanted]
                attributes[name].append(value)
        return DirectoryEntry(
            dn=target_dn,
            attributes={name: tuple(values) for name, values in attributes.items() if values},
        )


def _wire_value(value: WritableValue) -> str | bytes:
    """Convert a typed value to LDAP's string form (booleans are ``TRUE``/``FALSE``)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return value


def _same_attributes(first: DirectoryEntry, second: DirectoryEntry) -> bool:
    def normalized(entry: DirectoryEntry) -> dict[str, list[str | bytes]]:
        return {
            name.casefold(): sorted((_comparable(value) for value in values), key=repr)
            for name, values in entry.attributes.items()
        }

    return normalized(first) == normalized(second)
