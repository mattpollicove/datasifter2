"""Validation rules of the Data Lens models and DN helpers (no directory needed)."""

import pytest
from pydantic import ValidationError

from datasifter.datalens.dn import (
    InvalidDnError,
    is_descendant,
    join_dn,
    normalize_dn,
    normalize_rdn,
    rdn_pairs,
    same_dn,
    split_dn,
)
from datasifter.datalens.models import (
    REDACTED,
    AttributeChange,
    CreatePlan,
    DirectoryBackend,
    DirectoryEntry,
    DryRunResult,
    IssueCode,
    IssueSeverity,
    MovePlan,
    PlanIssue,
    SearchSpec,
)


def test_dn_split_respects_escaped_commas_and_multivalued_rdns():
    assert split_dn(r"cn=Lane\, Ada,ou=People,dc=example,dc=com") == (
        r"cn=Lane\, Ada",
        "ou=People,dc=example,dc=com",
    )
    assert split_dn("cn=a+uid=b,dc=x") == ("cn=a+uid=b", "dc=x")
    assert split_dn("dc=com") == ("dc=com", "")
    assert rdn_pairs(r"cn=Lane\, Ada\2B,dc=x") == (("cn", "Lane, Ada+"),)


def test_dn_normalization_drops_spacing_but_keeps_case():
    assert normalize_dn("CN=Ada Lane, OU=People ,DC=x") == "CN=Ada Lane,OU=People,DC=x"
    assert join_dn("cn=New", "ou=People, dc=x") == "cn=New,ou=People,dc=x"


@pytest.mark.parametrize("text", ["", "   ", "no-equals", "cn=,dc=x", "cn=a,,dc=x"])
def test_invalid_dns_are_rejected(text):
    with pytest.raises(InvalidDnError):
        normalize_dn(text)


def test_rdn_must_be_a_single_component():
    assert normalize_rdn("cn=Ada Lane") == "cn=Ada Lane"
    with pytest.raises(InvalidDnError):
        normalize_rdn("cn=Ada,ou=People")


def test_dn_comparison_ignores_case_spacing_and_escape_style():
    assert same_dn("CN=Ada,OU=People,DC=X", "cn=ada, ou=people, dc=x")
    assert same_dn(r"cn=a\2Cb,dc=x", r"cn=a\,b,dc=x")
    assert is_descendant("cn=Ada,OU=People,dc=x", "ou=people,DC=x")
    assert not is_descendant("ou=People,dc=x", "ou=People,dc=x")
    # A shared suffix of characters is not a shared parent.
    assert not is_descendant("cn=Ada,ou=NotPeople,dc=x", "ou=People,dc=x")


def test_search_spec_validates_filter_base_and_attributes():
    spec = SearchSpec(base_dn="ou=People, dc=x", filter=" (uid=ada) ", attributes=["cn", "+"])
    assert (spec.base_dn, spec.filter) == ("ou=People,dc=x", "(uid=ada)")
    # Active Directory bitwise matching rules are valid filters.
    SearchSpec(filter="(userAccountControl:1.2.840.113556.1.4.803:=2)")
    for invalid in (
        {"filter": "uid=ada"},
        {"base_dn": "not a dn"},
        {"attributes": []},
        {"attributes": ["bad name"]},
        {"size_limit": 0},
        {"unknown_field": 1},
    ):
        with pytest.raises(ValidationError):
            SearchSpec(**invalid)


def test_create_plan_accepts_single_values_and_requires_object_class():
    plan = CreatePlan(dn="cn=Ada,dc=x", attributes={"objectClass": "person", "sn": ["Lane"]})
    assert plan.attributes == {"objectClass": ("person",), "sn": ("Lane",)}
    for attributes in (
        {"sn": ["Lane"]},
        {"objectClass": ["person"], "sn": []},
        {"objectClass": ["person"], "sn": [""]},
        {"objectClass": ["person"], "SN": ["a"], "sn": ["b"]},
        {"objectClass": ["person"], "bad name": ["a"]},
    ):
        with pytest.raises(ValidationError):
            CreatePlan(dn="cn=Ada,dc=x", attributes=attributes)


def test_attribute_change_requires_values_only_when_adding():
    assert AttributeChange(attribute="mail", operation="delete").values == ()
    assert AttributeChange(attribute="mail", operation="replace").values == ()
    with pytest.raises(ValidationError):
        AttributeChange(attribute="mail", operation="add")


def test_move_plan_needs_a_destination_and_a_single_rdn():
    assert MovePlan(dn="cn=a,dc=x", new_rdn="cn=b").new_superior is None
    with pytest.raises(ValidationError):
        MovePlan(dn="cn=a,dc=x")
    with pytest.raises(ValidationError):
        MovePlan(dn="cn=a,dc=x", new_rdn="cn=b,ou=People")


def test_password_values_are_redacted_and_absent_from_validation_errors():
    plan = CreatePlan(
        dn="cn=Ada,dc=x",
        attributes={"objectClass": ["person"], "userPassword": ["hunter2"], "sn": ["Lane"]},
    )
    assert plan.redacted().attributes["userPassword"] == (REDACTED,)
    assert plan.redacted().attributes["sn"] == ("Lane",)
    change = AttributeChange(attribute="unicodePwd", operation="replace", values=["hunter2"])
    assert change.redacted().values == (REDACTED,)
    entry = DirectoryEntry(dn="cn=Ada,dc=x", attributes={"userPassword;binary": (b"hunter2",)})
    assert entry.redacted().attributes == {"userPassword;binary": (REDACTED,)}

    with pytest.raises(ValidationError) as rejected:
        CreatePlan(dn="cn=Ada,dc=x", attributes={"userPassword": ["hunter2"]})
    assert "hunter2" not in str(rejected.value)


def test_entry_from_ldap_drops_empty_attributes_and_keeps_typed_values():
    entry = DirectoryEntry.from_ldap(
        {
            "dn": "CN=Ada,DC=x",
            "attributes": {
                "userAccountControl": [512],
                "userPrincipalName": [],
                "objectClass": ["top", "user"],
                "odd": [bytearray(b"\x01"), object],
            },
        }
    )
    assert entry.get("USERACCOUNTCONTROL") == (512,)
    assert "userPrincipalName" not in entry.attributes
    assert entry.has_object_class("USER")
    assert entry.first("missing") is None
    assert entry.attributes["odd"] == (b"\x01", str(object))


def test_dry_run_result_blocks_on_errors_and_survives_serialization():
    plan = MovePlan(dn="cn=a,dc=x", new_rdn="cn=b")
    warning = PlanIssue(severity=IssueSeverity.WARNING, code=IssueCode.HAS_CHILDREN, message="w")
    error = PlanIssue(severity=IssueSeverity.ERROR, code=IssueCode.ENTRY_EXISTS, message="e")
    allowed = DryRunResult(
        backend=DirectoryBackend.STANDARD_LDAP, plan=plan, issues=(warning,), summary="s"
    )
    blocked = allowed.model_copy(update={"issues": (warning, error)})
    assert allowed.can_apply and not blocked.can_apply

    data = blocked.model_dump(mode="json")
    assert data["can_apply"] is False
    assert data["plan"]["kind"] == "move"
    del data["can_apply"]
    assert DryRunResult.model_validate(data) == blocked
