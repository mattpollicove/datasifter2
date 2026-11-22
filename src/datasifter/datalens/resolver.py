"""Turns everyday terms and names into attributes, entries, searches and plans.

:class:`DirectoryResolver` sits between a request expressed the way people speak
("set Alex Smith's department to Sales") and the typed adapter operations. It
never guesses. Each step either produces a definite result or a
:class:`~datasifter.datalens.models.ClarificationPrompt` that says what is unclear
and, where possible, lists the choices:

* an everyday term is mapped to the attribute that holds it on this server;
* a name is mapped to exactly one entry;
* a request is checked for what it still leaves out.

The ``prepare_*`` methods combine these steps. They only read from the directory:
mutations are returned as a plan with a dry-run preview and are applied later
through :meth:`DirectoryAdapter.execute`, behind the confirmation gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import Event
from typing import Any

from ldap3.utils.dn import escape_rdn

from datasifter.datalens.adapters.base import DirectoryAdapter
from datasifter.datalens.aliases import ACCOUNT_STATE_TERMS, AliasRegistry, normalize_term
from datasifter.datalens.dn import InvalidDnError, normalize_dn, rdn_pairs
from datasifter.datalens.models import (
    AccountState,
    AttributeChange,
    AttributeResolution,
    ClarificationOption,
    ClarificationPrompt,
    ClarificationReason,
    CreatePlan,
    DeletePlan,
    DirectoryEntry,
    DryRunResult,
    EntryKind,
    IssueCode,
    IssueSeverity,
    MatchOperator,
    MovePlan,
    PreparedMutation,
    PreparedSearch,
    RequestedChange,
    SearchCondition,
    SearchSpec,
    UpdatePlan,
    WritableValue,
    base_attribute_name,
)
from datasifter.datalens.schema import DirectorySchema, references_entry_by_name
from datasifter.directory.filters import escape_filter_value

# Attributes read to describe an entry when it is offered as a choice.
_DESCRIPTIVE_ATTRIBUTES = ("cn", "displayName", "ou", "description", "objectClass")
_GROUP_CLASSES = ("groupOfNames", "groupOfUniqueNames", "posixGroup", "group")
_KIND_WORDS = {
    EntryKind.USER: "user",
    EntryKind.GROUP: "group",
    EntryKind.CONTAINER: "organizational unit",
    EntryKind.ANY: "entry",
}


def _prompt(
    reason: ClarificationReason,
    question: str,
    field: str | None = None,
    options: Sequence[ClarificationOption] = (),
) -> ClarificationPrompt:
    return ClarificationPrompt(
        reason=reason, question=question, field=field, options=tuple(options)
    )


class DirectoryResolver:
    """Resolves everyday terms, names and incomplete requests against one directory."""

    def __init__(
        self,
        adapter: DirectoryAdapter,
        registry: AliasRegistry | None = None,
        *,
        max_options: int = 10,
    ) -> None:
        """Wrap ``adapter``.

        ``registry`` defaults to the built-in concepts. ``max_options`` limits how
        many entries a clarification lists.
        """
        self.adapter = adapter
        self.registry = registry or AliasRegistry()
        self._max_options = max_options

    # ------------------------------------------------------------------
    # Attribute terms
    # ------------------------------------------------------------------
    def resolve_attribute(
        self, term: str, *, for_write: bool = False, cancelled: Event | None = None
    ) -> AttributeResolution | ClarificationPrompt:
        """Map an everyday term or attribute name to this server's attributes.

        Candidates the server's schema does not define are dropped. For a search
        (``for_write=False``) a broad term keeps all its candidates. For a change
        the result always names one attribute: a broad term, and an attribute the
        directory maintains itself, produce a clarification instead.
        """
        schema = self.adapter.schema(cancelled=cancelled)
        concept = self.registry.concept_for(term)
        candidates: list[str] = []
        if concept is not None:
            for attribute in concept.candidates(self.adapter.backend):
                name = attribute if schema is None else schema.attribute_name(attribute)
                if name is not None and name not in candidates:
                    candidates.append(name)
        literal = self._literal_attribute(term, schema)
        if not candidates:
            if literal is None:
                return self._unknown_term(term)
            candidates = [literal]
        elif literal is not None and literal.casefold() in {name.casefold() for name in candidates}:
            # The term is the exact name of one candidate ("postalAddress" within
            # the broader "address"), so that attribute is what was meant.
            candidates = [name for name in candidates if name.casefold() == literal.casefold()]

        if for_write:
            writable = [
                name
                for name in candidates
                if base_attribute_name(name) not in self.adapter.system_attributes
            ]
            if not writable:
                advice = (
                    " Change the group's member list instead."
                    if concept is not None and concept.key == "groups"
                    else ""
                )
                return _prompt(
                    ClarificationReason.UNSUPPORTED_REQUEST,
                    f"{candidates[0]} is maintained by the directory and cannot be set "
                    f"directly.{advice}",
                    field=term,
                )
            if len(writable) > 1:
                return _prompt(
                    ClarificationReason.AMBIGUOUS_ATTRIBUTE,
                    f"'{term}' can mean more than one attribute in this directory. "
                    "Which one should be changed?",
                    field=term,
                    options=[self._attribute_option(name) for name in writable],
                )
            candidates = writable

        return AttributeResolution(
            term=term,
            concept=concept.key if concept is not None else None,
            attributes=tuple(candidates),
            references_entry=any(self._references_entry(name, schema) for name in candidates),
        )

    @staticmethod
    def _literal_attribute(term: str, schema: DirectorySchema | None) -> str | None:
        """Return the attribute a term names outright ("sAMAccountName"), if any."""
        text = term.strip()
        if schema is None:
            # Nothing to check against: accept only what is shaped like an attribute name.
            return text if text and text.replace("-", "").isalnum() else None
        return schema.attribute_name(text) or schema.attribute_name(normalize_term(text))

    def _unknown_term(self, term: str) -> ClarificationPrompt:
        concept = self.registry.concept_for(term)
        if concept is not None:
            # A known idea that this server's schema has no attribute for.
            return _prompt(
                ClarificationReason.UNKNOWN_TERM,
                f"This directory has no attribute for {concept.label}.",
                field=term,
            )
        options = [
            ClarificationOption(value=concept.phrases[0], label=concept.label)
            for concept in self.registry.near_concepts(term)
        ]
        question = f"'{term}' is not a term or attribute this directory knows."
        if options:
            question += " Did you mean one of these?"
        return _prompt(ClarificationReason.UNKNOWN_TERM, question, field=term, options=options)

    def _attribute_option(self, attribute: str) -> ClarificationOption:
        label = self.registry.label_for(attribute, self.adapter.backend)
        return ClarificationOption(
            value=attribute, label=f"{label} ({attribute})" if label else attribute
        )

    @staticmethod
    def _references_entry(attribute: str, schema: DirectorySchema | None) -> bool:
        if schema is None:
            return references_entry_by_name(attribute)
        return schema.references_entry(attribute)

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------
    def resolve_entry(
        self,
        reference: str,
        *,
        kind: EntryKind = EntryKind.ANY,
        field: str | None = None,
        cancelled: Event | None = None,
    ) -> DirectoryEntry | ClarificationPrompt:
        """Find the one entry a DN or a name refers to.

        A DN is looked up directly. A name is matched exactly against the identity
        attributes of ``kind``. Only a single exact match is returned; several
        matches, and near matches when nothing matches exactly, are offered as
        choices and never picked automatically. The entry carries descriptive
        attributes only; read it again for more.
        """
        text = reference.strip() if reference else ""
        word = _KIND_WORDS[kind]
        if not text:
            return _prompt(
                ClarificationReason.MISSING_INFORMATION, f"Which {word} do you mean?", field=field
            )
        attributes = self.adapter.known_attributes(
            (*_DESCRIPTIVE_ATTRIBUTES, *self.adapter.identity_attributes), cancelled=cancelled
        )
        dn = self._as_dn(text)
        if dn is not None:
            entry = self.adapter.get_entry(dn, attributes, cancelled=cancelled)
            if entry is None:
                return _prompt(
                    ClarificationReason.NOT_FOUND, f"No entry exists at {dn}.", field=field
                )
            return entry

        def find(search_filter: str) -> tuple[tuple[DirectoryEntry, ...], bool]:
            result = self.adapter.search_entries(
                SearchSpec(
                    filter=search_filter, attributes=attributes, size_limit=self._max_options
                ),
                cancelled=cancelled,
            )
            return result.entries, result.truncated

        matches, more = find(self.adapter.entry_filter(kind, text))
        if len(matches) == 1 and not more:
            return matches[0]
        if matches:
            return _prompt(
                ClarificationReason.AMBIGUOUS_TARGET,
                f"More than one {word} matches '{text}'"
                + (f"; the first {len(matches)} are listed" if more else "")
                + ". Which one do you mean?",
                field=field,
                options=[self._entry_option(entry) for entry in matches],
            )
        near, _ = find(self._near_filter(kind, text))
        question = f"No {word} is named exactly '{text}'."
        if near:
            question += " Did you mean one of these?"
        return _prompt(
            ClarificationReason.NOT_FOUND,
            question,
            field=field,
            options=[self._entry_option(entry) for entry in near],
        )

    @staticmethod
    def _as_dn(text: str) -> str | None:
        if "=" not in text:
            return None
        try:
            return normalize_dn(text)
        except InvalidDnError:
            return None

    def _near_filter(self, kind: EntryKind, text: str) -> str:
        """Return a filter for entries of ``kind`` whose name contains ``text``."""
        value = escape_filter_value(text)
        adapter = self.adapter

        def containing(class_filter: str, attributes: Sequence[str]) -> str:
            tests = "".join(f"({attribute}=*{value}*)" for attribute in attributes)
            return f"(&{class_filter}(|{tests}))"

        users = containing(adapter.user_filter(), adapter.identity_attributes)
        groups = containing(adapter.group_filter(), adapter.group_identity_attributes)
        containers = "".join(
            f"(&(objectClass={object_class})({attribute}=*{value}*))"
            for object_class, attribute in adapter.container_classes
        )
        if kind is EntryKind.USER:
            return users
        if kind is EntryKind.GROUP:
            return groups
        if kind is EntryKind.CONTAINER:
            return f"(|{containers})"
        return f"(|{users}{groups}{containers})"

    @staticmethod
    def _entry_option(entry: DirectoryEntry) -> ClarificationOption:
        name = next(
            (
                str(value)
                for attribute in ("displayName", "cn", "ou", "uid", "sAMAccountName")
                if (value := entry.first(attribute)) is not None
            ),
            None,
        )
        return ClarificationOption(
            value=entry.dn, label=f"{name} - {entry.dn}" if name else entry.dn
        )

    # ------------------------------------------------------------------
    # Searches
    # ------------------------------------------------------------------
    def prepare_search(
        self,
        conditions: Sequence[SearchCondition | Mapping[str, Any]] = (),
        *,
        kind: EntryKind = EntryKind.ANY,
        match_all: bool = True,
        container: str | None = None,
        attributes: Sequence[str] = (),
        size_limit: int | None = None,
        cancelled: Event | None = None,
    ) -> PreparedSearch:
        """Translate conditions in everyday terms into a :class:`SearchSpec`.

        A broad term matches any of its attributes. A value for an attribute that
        refers to another entry ("manager is Jane Doe") is resolved to that entry.
        ``container`` narrows the search to one organizational unit, and
        ``attributes`` names what to return, also in everyday terms.
        """
        prompts: list[ClarificationPrompt] = []
        tests: list[str] = []
        for item in conditions:
            condition = SearchCondition.model_validate(item)
            test = self._condition_filter(condition, prompts, cancelled)
            if test is not None:
                tests.append(f"(!{test})" if condition.negate else test)

        parts: list[str] = []
        if kind is EntryKind.USER:
            parts.append(self.adapter.user_filter())
        elif kind is EntryKind.GROUP:
            parts.append(self.adapter.group_filter())
        elif kind is EntryKind.CONTAINER:
            parts.append(self.adapter.container_filter())
        if match_all or len(tests) == 1:
            parts.extend(tests)
        elif tests:
            parts.append(f"(|{''.join(tests)})")
        if not parts:
            search_filter = "(objectClass=*)"
        else:
            search_filter = parts[0] if len(parts) == 1 else f"(&{''.join(parts)})"

        base_dn: str | None = None
        if container:
            found = self.resolve_entry(
                container, kind=EntryKind.CONTAINER, field="container", cancelled=cancelled
            )
            if isinstance(found, ClarificationPrompt):
                prompts.append(found)
            else:
                base_dn = found.dn

        requested: list[str] = []
        for term in attributes:
            resolution = self.resolve_attribute(term, cancelled=cancelled)
            if isinstance(resolution, ClarificationPrompt):
                prompts.append(resolution)
            else:
                requested.extend(name for name in resolution.attributes if name not in requested)

        if prompts:
            return PreparedSearch(clarifications=tuple(prompts))
        return PreparedSearch(
            spec=SearchSpec(
                base_dn=base_dn,
                filter=search_filter,
                attributes=tuple(requested) or ("*",),
                size_limit=size_limit,
            )
        )

    def _condition_filter(
        self,
        condition: SearchCondition,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> str | None:
        """Return the filter for one condition, or None after adding a clarification."""
        # "disabled" on its own, or "status is disabled", is about account state.
        names_state = normalize_term(condition.term) in ACCOUNT_STATE_TERMS
        state: AccountState | None = None
        if condition.operator is MatchOperator.PRESENT:
            state = self.registry.account_state_for(condition.term)
        elif names_state and condition.operator is MatchOperator.EQUALS:
            state = self.registry.account_state_for(condition.value or "")
        if state is None and names_state:
            prompts.append(
                _prompt(
                    ClarificationReason.MISSING_INFORMATION,
                    "Which account status do you mean?",
                    field=condition.term,
                    options=[
                        ClarificationOption(value=item.value, label=item.value)
                        for item in AccountState
                    ],
                )
            )
            return None
        if state is not None:
            state_filter = self.adapter.account_state_filter(state, cancelled=cancelled)
            if state_filter is None:
                prompts.append(
                    _prompt(
                        ClarificationReason.UNSUPPORTED_REQUEST,
                        "This directory has no attribute that shows whether an account is "
                        f"{state.value}.",
                        field=condition.term,
                    )
                )
            return state_filter

        resolution = self.resolve_attribute(condition.term, cancelled=cancelled)
        if isinstance(resolution, ClarificationPrompt):
            prompts.append(resolution)
            return None
        if condition.operator is MatchOperator.PRESENT:
            tests = [f"({name}=*)" for name in resolution.attributes]
        else:
            value = condition.value or ""
            if resolution.references_entry:
                if condition.operator is not MatchOperator.EQUALS:
                    prompts.append(
                        _prompt(
                            ClarificationReason.UNSUPPORTED_REQUEST,
                            f"'{condition.term}' refers to another entry, so it can only "
                            "be matched against one whole entry.",
                            field=condition.term,
                        )
                    )
                    return None
                referenced = self.resolve_entry(
                    value,
                    kind=self._reference_kind(resolution.attributes[0]),
                    field=condition.term,
                    cancelled=cancelled,
                )
                if isinstance(referenced, ClarificationPrompt):
                    prompts.append(referenced)
                    return None
                value = referenced.dn
            escaped = escape_filter_value(value)
            pattern = {
                MatchOperator.EQUALS: escaped,
                MatchOperator.CONTAINS: f"*{escaped}*",
                MatchOperator.STARTS_WITH: f"{escaped}*",
                MatchOperator.ENDS_WITH: f"*{escaped}",
            }[condition.operator]
            tests = [f"({name}={pattern})" for name in resolution.attributes]
        return tests[0] if len(tests) == 1 else f"(|{''.join(tests)})"

    @staticmethod
    def _reference_kind(attribute: str) -> EntryKind:
        # A manager is a person; other references may point at any kind of entry.
        return EntryKind.USER if base_attribute_name(attribute) == "manager" else EntryKind.ANY

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def prepare_create(
        self,
        kind: EntryKind,
        name: str | None,
        *,
        container: str | None = None,
        attributes: Mapping[str, WritableValue | Sequence[WritableValue]] | None = None,
        cancelled: Event | None = None,
    ) -> PreparedMutation:
        """Plan a new user, group or organizational unit.

        ``name`` becomes the entry's naming value and ``container`` is where it is
        created; neither is assumed. ``attributes`` are keyed by everyday term.
        Attributes the schema requires but the request omits come back as questions.
        """
        prompts: list[ClarificationPrompt] = []
        if kind is EntryKind.ANY:
            prompts.append(
                _prompt(
                    ClarificationReason.MISSING_INFORMATION,
                    "What should be created?",
                    field="kind",
                    options=[
                        ClarificationOption(value=item.value, label=_KIND_WORDS[item])
                        for item in (EntryKind.USER, EntryKind.GROUP, EntryKind.CONTAINER)
                    ],
                )
            )
        word = _KIND_WORDS[kind]
        if not name or not name.strip():
            prompts.append(
                _prompt(
                    ClarificationReason.MISSING_INFORMATION,
                    f"What should the new {word} be called?",
                    field="name",
                )
            )
        parent = self._container(
            container, f"Where should the new {word} be created?", prompts, cancelled
        )
        if kind is EntryKind.CONTAINER:
            classes, naming = ("top", "organizationalUnit"), "ou"
        elif kind is EntryKind.GROUP:
            classes, naming = self.adapter.group_object_classes, "cn"
        else:
            classes, naming = self.adapter.user_object_classes, "cn"
        # The classes the new entry will have decide, for example, which attribute
        # lists a group's members.
        shape = DirectoryEntry(dn="cn=pending", attributes={"objectClass": classes})
        resolved = self._assignments(attributes or {}, shape, prompts, cancelled)
        if prompts or parent is None or not name:
            return PreparedMutation(clarifications=tuple(prompts))

        if not any(key.casefold() == "objectclass" for key in resolved):
            resolved = {"objectClass": classes, **resolved}
        plan = CreatePlan(dn=f"{naming}={escape_rdn(name.strip())},{parent}", attributes=resolved)
        preview = self.adapter.execute(plan, cancelled=cancelled).preview
        return PreparedMutation(
            clarifications=tuple(self._questions_for_missing(preview)),
            plan=plan,
            preview=preview,
        )

    def prepare_update(
        self,
        target: str,
        changes: Sequence[RequestedChange | Mapping[str, Any]],
        *,
        kind: EntryKind = EntryKind.ANY,
        cancelled: Event | None = None,
    ) -> PreparedMutation:
        """Plan attribute changes, given in everyday terms, to one existing entry."""
        prompts: list[ClarificationPrompt] = []
        entry = self._target(target, kind, prompts, cancelled)
        resolved: list[AttributeChange] = []
        for item in changes:
            change = RequestedChange.model_validate(item)
            attribute = self._attribute_to_change(change.term, entry, prompts, cancelled)
            if attribute is None:
                continue
            values = self._values(attribute, change.values, change.term, prompts, cancelled)
            if values is not None:
                resolved.append(
                    AttributeChange(attribute=attribute, operation=change.operation, values=values)
                )
        if prompts or entry is None:
            return PreparedMutation(clarifications=tuple(prompts))
        return self._previewed(UpdatePlan(dn=entry.dn, changes=tuple(resolved)), cancelled)

    def prepare_account_state(
        self, target: str, state: AccountState, *, cancelled: Event | None = None
    ) -> PreparedMutation:
        """Plan enabling or disabling an account, however this directory records that."""
        if state is AccountState.LOCKED:
            return PreparedMutation(
                clarifications=(
                    _prompt(
                        ClarificationReason.UNSUPPORTED_REQUEST,
                        "An account locks itself after failed sign-ins; it can be "
                        "disabled or enabled instead.",
                        field="state",
                    ),
                )
            )
        prompts: list[ClarificationPrompt] = []
        entry = self._target(target, EntryKind.USER, prompts, cancelled)
        if entry is None:
            return PreparedMutation(clarifications=tuple(prompts))
        adapter = self.adapter
        current = adapter.get_entry(
            entry.dn,
            adapter.known_attributes(adapter.account_status_attributes, cancelled=cancelled)
            or ("1.1",),
            cancelled=cancelled,
        )
        try:
            change = (
                adapter.account_state_change(
                    current, disabled=state is AccountState.DISABLED, cancelled=cancelled
                )
                if current is not None
                else None
            )
        except ValueError:
            # The entry lacks the attribute that holds the state, so it is not an account.
            change = None
        if change is None:
            return PreparedMutation(
                clarifications=(
                    _prompt(
                        ClarificationReason.UNSUPPORTED_REQUEST,
                        f"This directory offers no way to mark {entry.dn} as {state.value}.",
                        field="state",
                    ),
                )
            )
        return self._previewed(UpdatePlan(dn=entry.dn, changes=(change,)), cancelled)

    def prepare_delete(
        self,
        target: str,
        *,
        kind: EntryKind = EntryKind.ANY,
        recursive: bool = False,
        cancelled: Event | None = None,
    ) -> PreparedMutation:
        """Plan deleting one entry; a subtree is only included when asked for."""
        prompts: list[ClarificationPrompt] = []
        entry = self._target(target, kind, prompts, cancelled)
        if entry is None:
            return PreparedMutation(clarifications=tuple(prompts))
        prepared = self._previewed(DeletePlan(dn=entry.dn, recursive=recursive), cancelled)
        preview = prepared.preview
        if preview is not None and any(
            issue.code is IssueCode.HAS_CHILDREN and issue.severity is IssueSeverity.ERROR
            for issue in preview.issues
        ):
            question = _prompt(
                ClarificationReason.MISSING_INFORMATION,
                f"{entry.dn} has subordinate entries. Should they be deleted as well?",
                field="recursive",
                options=[
                    ClarificationOption(value="true", label="Yes, delete the whole subtree"),
                    ClarificationOption(value="false", label="No, leave everything as it is"),
                ],
            )
            return prepared.model_copy(update={"clarifications": (question,)})
        return prepared

    def prepare_move(
        self,
        target: str,
        *,
        kind: EntryKind = EntryKind.ANY,
        new_name: str | None = None,
        destination: str | None = None,
        cancelled: Event | None = None,
    ) -> PreparedMutation:
        """Plan renaming an entry (``new_name``), moving it (``destination``), or both."""
        prompts: list[ClarificationPrompt] = []
        entry = self._target(target, kind, prompts, cancelled)
        renaming = bool(new_name and new_name.strip())
        parent: str | None = None
        if destination or not renaming:
            parent = self._container(
                destination, "Where should it be moved, or what should it be renamed to?",
                prompts, cancelled,
            )
        if prompts or entry is None:
            return PreparedMutation(clarifications=tuple(prompts))
        new_rdn = None
        if renaming and new_name is not None:
            # A rename keeps the attribute the entry is already named by.
            naming = rdn_pairs(entry.dn)[0][0]
            new_rdn = f"{naming}={escape_rdn(new_name.strip())}"
        return self._previewed(
            MovePlan(dn=entry.dn, new_rdn=new_rdn, new_superior=parent), cancelled
        )

    # ------------------------------------------------------------------
    # Shared steps
    # ------------------------------------------------------------------
    def _previewed(
        self, plan: CreatePlan | UpdatePlan | DeletePlan | MovePlan, cancelled: Event | None
    ) -> PreparedMutation:
        preview = self.adapter.execute(plan, cancelled=cancelled).preview
        return PreparedMutation(plan=plan, preview=preview)

    def _target(
        self,
        reference: str,
        kind: EntryKind,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> DirectoryEntry | None:
        found = self.resolve_entry(reference, kind=kind, field="target", cancelled=cancelled)
        if isinstance(found, ClarificationPrompt):
            prompts.append(found)
            return None
        return found

    def _container(
        self,
        reference: str | None,
        question: str,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> str | None:
        """Return the DN of the named container, or ask where, listing those that exist."""
        if reference and reference.strip():
            found = self.resolve_entry(
                reference, kind=EntryKind.CONTAINER, field="container", cancelled=cancelled
            )
            if isinstance(found, ClarificationPrompt):
                prompts.append(found)
                return None
            return found.dn
        attributes = self.adapter.known_attributes(_DESCRIPTIVE_ATTRIBUTES, cancelled=cancelled)

        def existing(search_filter: str) -> tuple[DirectoryEntry, ...]:
            return self.adapter.search_entries(
                SearchSpec(
                    filter=search_filter, attributes=attributes, size_limit=self._max_options
                ),
                cancelled=cancelled,
            ).entries

        # Organizational units are where people put entries. Other containers are
        # mostly system ones, so they are offered only when there is no unit at all.
        choices = existing("(objectClass=organizationalUnit)") or existing(
            self.adapter.container_filter()
        )
        prompts.append(
            _prompt(
                ClarificationReason.MISSING_INFORMATION,
                question,
                field="container",
                options=[self._entry_option(entry) for entry in choices],
            )
        )
        return None

    def _attribute_to_change(
        self,
        term: str,
        entry: DirectoryEntry | None,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> str | None:
        concept = self.registry.concept_for(term)
        if (
            concept is not None
            and concept.key == "members"
            and entry is not None
            and any(entry.has_object_class(name) for name in _GROUP_CLASSES)
        ):
            # The group's own class decides which attribute lists its members.
            return self.adapter.member_attribute(entry)
        resolution = self.resolve_attribute(term, for_write=True, cancelled=cancelled)
        if isinstance(resolution, ClarificationPrompt):
            prompts.append(resolution)
            return None
        return resolution.attributes[0]

    def _values(
        self,
        attribute: str,
        values: Sequence[WritableValue],
        term: str,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> tuple[WritableValue, ...] | None:
        """Return the values to write; names of referenced entries become their DNs."""
        schema = self.adapter.schema(cancelled=cancelled)
        if not self._references_entry(attribute, schema):
            return tuple(values)
        resolved: list[WritableValue] = []
        complete = True
        for value in values:
            found = self.resolve_entry(
                str(value), kind=self._reference_kind(attribute), field=term, cancelled=cancelled
            )
            if isinstance(found, ClarificationPrompt):
                prompts.append(found)
                complete = False
            else:
                resolved.append(found.dn)
        return tuple(resolved) if complete else None

    def _assignments(
        self,
        attributes: Mapping[str, WritableValue | Sequence[WritableValue]],
        entry: DirectoryEntry,
        prompts: list[ClarificationPrompt],
        cancelled: Event | None,
    ) -> dict[str, tuple[WritableValue, ...]]:
        """Resolve ``{everyday term: value(s)}`` to ``{attribute: values}`` for ``entry``."""
        resolved: dict[str, tuple[WritableValue, ...]] = {}
        for term, given in attributes.items():
            values = (given,) if isinstance(given, (str, bytes, bool, int)) else tuple(given)
            attribute = self._attribute_to_change(term, entry, prompts, cancelled)
            if attribute is None:
                continue
            written = self._values(attribute, values, term, prompts, cancelled)
            if written is not None:
                # Two terms for the same attribute ("email" and "mail") are combined.
                resolved[attribute] = (*resolved.get(attribute, ()), *written)
        return resolved

    def _questions_for_missing(self, preview: DryRunResult) -> list[ClarificationPrompt]:
        """Turn "required attribute missing" findings of a preview into questions."""
        questions: list[ClarificationPrompt] = []
        for issue in preview.issues:
            if issue.code is not IssueCode.MISSING_REQUIRED_ATTRIBUTE or not issue.attribute:
                continue
            label = self.registry.label_for(issue.attribute, self.adapter.backend)
            wanted = f"the {label} ({issue.attribute})" if label else issue.attribute
            questions.append(
                _prompt(
                    ClarificationReason.MISSING_INFORMATION,
                    f"{issue.message} What should {wanted} be?",
                    field=issue.attribute,
                )
            )
        return questions
