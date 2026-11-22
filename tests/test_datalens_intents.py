import pytest
from datalens_support import ADA
from pydantic import ValidationError

from datasifter.datalens import (
    ClarifyIntent,
    CreateProposal,
    DeleteProposal,
    DirectoryResolver,
    EntryKind,
    ProposedMutationIntent,
    SearchIntent,
    prepare_intent,
)
from datasifter.datalens.intents import parse_intent_json


def test_search_intent_json_is_validated_and_fenced_json_is_repaired():
    intent = parse_intent_json(
        '```json\n{"intent":"search","conditions":[{"term":"email",'
        '"operator":"contains","value":"example.com"}],"kind":"user"}\n```'
    )

    assert isinstance(intent, SearchIntent)
    assert intent.conditions[0].term == "email"
    assert intent.size_limit == 100


def test_intent_parser_rejects_unknown_fields_and_non_json_commands():
    with pytest.raises(ValidationError):
        parse_intent_json('{"intent":"search","filter":"(objectClass=*)"}')
    with pytest.raises(ValidationError):
        parse_intent_json("delete entry cn=Ada,dc=test")


def test_mutation_intent_resolves_to_preview_without_changing_directory(directory):
    resolver = DirectoryResolver(directory.adapter())
    intent = ProposedMutationIntent(
        intent="proposed_mutation",
        mutation=DeleteProposal(
            operation="delete",
            target=ADA,
            kind=EntryKind.USER,
        ),
    )

    prepared = prepare_intent(intent, resolver)

    assert prepared.preview is not None
    assert prepared.preview.summary
    assert directory.read(ADA) is not None


def test_credential_mutation_proposal_becomes_clarification_without_plan(directory):
    resolver = DirectoryResolver(directory.adapter())
    intent = ProposedMutationIntent(
        intent="proposed_mutation",
        mutation=CreateProposal(
            operation="create",
            kind=EntryKind.USER,
            name="New User",
            container="People",
            attributes={"userPassword": "must-not-be-planned"},
        ),
    )

    prepared = prepare_intent(intent, resolver)

    assert isinstance(prepared, ClarifyIntent)
    assert "explicit entry form" in prepared.question


def test_empty_search_requires_explicit_all_entries_intent(directory):
    resolver = DirectoryResolver(directory.adapter())
    intent = SearchIntent(intent="search")

    prepared = prepare_intent(intent, resolver)

    assert isinstance(prepared, ClarifyIntent)
    assert "explicitly" in prepared.question


def test_search_cannot_request_credential_attributes(directory):
    resolver = DirectoryResolver(directory.adapter())
    intent = SearchIntent(
        intent="search",
        conditions=({"term": "username", "value": "ada"},),
        attributes=("userPassword",),
    )

    prepared = prepare_intent(intent, resolver)

    assert isinstance(prepared, ClarifyIntent)
    assert "Credential attributes" in prepared.question


def test_search_cannot_filter_by_credential_attributes(directory):
    resolver = DirectoryResolver(directory.adapter())
    intent = SearchIntent(
        intent="search",
        conditions=({"term": "userPassword", "value": "should-not-be-sent"},),
    )

    prepared = prepare_intent(intent, resolver)

    assert isinstance(prepared, ClarifyIntent)
    assert "cannot be searched" in prepared.question
