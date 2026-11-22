from typing import get_args

from datasifter.datalens import AliasRegistry, AttributeConcept, get_help_catalog
from datasifter.datalens.help import format_help_catalog
from datasifter.datalens.intents import (
    ClarifyIntent,
    CreateProposal,
    DeleteProposal,
    MoveProposal,
    ProposedMutationIntent,
    SearchIntent,
    UpdateProposal,
)


def test_catalog_exposes_supported_intents_and_operations():
    catalog = get_help_catalog()
    intents = {
        get_args(cls.model_fields["intent"].annotation)[0]
        for cls in (SearchIntent, ProposedMutationIntent, ClarifyIntent)
    }
    operations = {
        get_args(cls.model_fields["operation"].annotation)[0]
        for cls in (CreateProposal, UpdateProposal, DeleteProposal, MoveProposal)
    }
    assert {item.intent for item in catalog.intents} == intents
    assert {item.operation for item in catalog.intents if item.intent == "proposed_mutation"} == operations
    assert all(item.example for item in catalog.intents)
    assert {item.backend.value for item in catalog.backends} == {"standard_ldap", "active_directory"}
    assert catalog.safety_constraints
    assert "Attribute aliases" in format_help_catalog(catalog)
    assert {item.key for item in catalog.directory_information} == {
        "current_target", "count_people", "monitor"
    }
    assert "Directory information (no model required)" in format_help_catalog(catalog)
    assert catalog.model_dump(mode="json")["version"] == 1


def test_catalog_reflects_effective_custom_alias_registry():
    registry = AliasRegistry()
    registry.register(
        AttributeConcept(
            key="site_email", label="site email", phrases=("email", "work inbox"),
            standard_ldap=("mail",), active_directory=("mail",),
        ),
        replace=True,
    )
    catalog = get_help_catalog(registry)
    matches = [concept for concept in catalog.aliases if "email" in concept.phrases]
    assert [concept.key for concept in matches] == ["site_email"]
    assert "work inbox" in format_help_catalog(catalog)
