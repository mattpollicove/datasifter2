"""Validated natural-language intents and their preparation through the resolver."""

from __future__ import annotations

from threading import Event
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from datasifter.datalens.models import (
    ClarificationReason,
    DataLensModel,
    EntryKind,
    PreparedMutation,
    PreparedSearch,
    RequestedChange,
    SearchCondition,
    WritableValue,
    is_sensitive,
)
from datasifter.datalens.resolver import DirectoryResolver


class SearchIntent(DataLensModel):
    """A search expressed as structured, everyday-language conditions."""

    intent: Literal["search"]
    conditions: tuple[SearchCondition, ...] = ()
    kind: EntryKind = EntryKind.ANY
    match_all: bool = True
    container: str | None = None
    attributes: tuple[str, ...] = ()
    all_entries: bool = False
    size_limit: int = Field(default=100, ge=1, le=5000)


class CreateProposal(DataLensModel):
    operation: Literal["create"]
    kind: EntryKind = EntryKind.ANY
    name: str | None = None
    container: str | None = None
    attributes: dict[str, WritableValue | tuple[WritableValue, ...]] = Field(
        default_factory=dict
    )


class UpdateProposal(DataLensModel):
    operation: Literal["update"]
    target: str | None = None
    kind: EntryKind = EntryKind.ANY
    changes: tuple[RequestedChange, ...] = ()


class DeleteProposal(DataLensModel):
    operation: Literal["delete"]
    target: str | None = None
    kind: EntryKind = EntryKind.ANY
    recursive: bool = False


class MoveProposal(DataLensModel):
    operation: Literal["move"]
    target: str | None = None
    kind: EntryKind = EntryKind.ANY
    new_name: str | None = None
    destination: str | None = None


MutationProposal = Annotated[
    CreateProposal | UpdateProposal | DeleteProposal | MoveProposal,
    Field(discriminator="operation"),
]


class ProposedMutationIntent(DataLensModel):
    """A typed mutation proposal. It is never executed by this intent layer."""

    intent: Literal["proposed_mutation"]
    mutation: MutationProposal


class ClarifyIntent(DataLensModel):
    """A request that the model cannot safely translate without user input."""

    intent: Literal["clarify"]
    question: str = Field(min_length=1, max_length=500)
    reason: ClarificationReason = ClarificationReason.MISSING_INFORMATION
    field: str | None = Field(default=None, max_length=100)


DirectoryIntent = Annotated[
    SearchIntent | ProposedMutationIntent | ClarifyIntent,
    Field(discriminator="intent"),
]

INTENT_ADAPTER = TypeAdapter(DirectoryIntent)
PreparedIntent = PreparedSearch | PreparedMutation | ClarifyIntent

_SECRET_TERMS = frozenset(
    {
        "credential",
        "credentials",
        "passphrase",
        "passphrases",
        "password",
        "passwords",
        "secret",
        "secrets",
    }
)


def _is_secret_term(term: str) -> bool:
    normalized = " ".join(term.casefold().replace("_", " ").split())
    return is_sensitive(term) or normalized in _SECRET_TERMS


def _contains_secret_attribute(proposal: MutationProposal) -> bool:
    if isinstance(proposal, CreateProposal):
        return any(_is_secret_term(attribute) for attribute in proposal.attributes)
    if isinstance(proposal, UpdateProposal):
        return any(_is_secret_term(change.term) for change in proposal.changes)
    return False


def prepare_intent(
    intent: DirectoryIntent,
    resolver: DirectoryResolver,
    *,
    cancelled: Event | None = None,
) -> PreparedIntent:
    """Resolve an intent into a search, a mutation dry-run, or a clarification.

    Mutation proposals are only passed to the resolver's preview methods; this
    function has no path that applies a plan to the directory.
    """
    if isinstance(intent, ClarifyIntent):
        return intent
    if isinstance(intent, SearchIntent):
        if any(_is_secret_term(condition.term) for condition in intent.conditions) or any(
            _is_secret_term(attribute) for attribute in intent.attributes
        ):
            return ClarifyIntent(
                intent="clarify",
                question=(
                    "Credential attributes cannot be searched through Data Lens. "
                    "Use a non-sensitive attribute instead."
                ),
                reason=ClarificationReason.UNSUPPORTED_REQUEST,
            )
        if not intent.conditions and not intent.all_entries:
            return ClarifyIntent(
                intent="clarify",
                question=(
                    "What should I search for? If you want all matching entries, state that "
                    "explicitly."
                ),
                reason=ClarificationReason.MISSING_INFORMATION,
            )
        return resolver.prepare_search(
            intent.conditions,
            kind=intent.kind,
            match_all=intent.match_all,
            container=intent.container,
            attributes=intent.attributes,
            size_limit=intent.size_limit,
            cancelled=cancelled,
        )

    proposal = intent.mutation
    if _contains_secret_attribute(proposal):
        return ClarifyIntent(
            intent="clarify",
            question=(
                "Data Lens cannot generate password or credential changes. "
                "Use the explicit entry form to handle credentials."
            ),
            reason=ClarificationReason.UNSUPPORTED_REQUEST,
            field="attributes",
        )
    if isinstance(proposal, CreateProposal):
        return resolver.prepare_create(
            proposal.kind,
            proposal.name,
            container=proposal.container,
            attributes=proposal.attributes,
            cancelled=cancelled,
        )
    if isinstance(proposal, UpdateProposal):
        if not proposal.target or not proposal.changes:
            return ClarifyIntent(
                intent="clarify",
                question="Which entry should be updated, and what attribute should change?",
                reason=ClarificationReason.MISSING_INFORMATION,
                field="target",
            )
        return resolver.prepare_update(
            proposal.target,
            proposal.changes,
            kind=proposal.kind,
            cancelled=cancelled,
        )
    if isinstance(proposal, DeleteProposal):
        if not proposal.target:
            return ClarifyIntent(
                intent="clarify",
                question="Which entry should be deleted?",
                reason=ClarificationReason.MISSING_INFORMATION,
                field="target",
            )
        return resolver.prepare_delete(
            proposal.target,
            kind=proposal.kind,
            recursive=proposal.recursive,
            cancelled=cancelled,
        )
    if isinstance(proposal, MoveProposal):
        if not proposal.target:
            return ClarifyIntent(
                intent="clarify",
                question="Which entry should be moved or renamed?",
                reason=ClarificationReason.MISSING_INFORMATION,
                field="target",
            )
        return resolver.prepare_move(
            proposal.target,
            kind=proposal.kind,
            new_name=proposal.new_name,
            destination=proposal.destination,
            cancelled=cancelled,
        )
    raise TypeError(f"Unsupported mutation proposal: {type(proposal).__name__}")


def parse_intent_json(response: str) -> DirectoryIntent:
    """Parse one JSON object, tolerating a surrounding Markdown JSON fence."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return INTENT_ADAPTER.validate_json(text)
