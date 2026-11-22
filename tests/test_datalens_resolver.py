"""Alias registry and resolver behaviour (Data Lens phase 2).

Registry tests need no directory. Resolver tests run against the in-memory mock
directory from ``datalens_support.py``, once per backend where the behaviour is shared.
"""

from __future__ import annotations

import pytest
from datalens_support import ADA, errors, warnings

from datasifter.datalens import (
    AccountState,
    AliasRegistry,
    AttributeConcept,
    AttributeResolution,
    ClarificationPrompt,
    ClarificationReason,
    CreatePlan,
    DirectoryBackend,
    DirectoryEntry,
    DirectoryResolver,
    EntryKind,
    IssueCode,
    MovePlan,
    RequestedChange,
    SearchCondition,
    UpdatePlan,
)
from datasifter.datalens.aliases import normalize_term
from datasifter.directory.filters import ATTRIBUTE_ALIASES, ATTRIBUTE_COMMAND_ALIASES
from datasifter.directory.service import LDAPOperationError

STANDARD = DirectoryBackend.STANDARD_LDAP
AD = DirectoryBackend.ACTIVE_DIRECTORY
GRACE = "cn=Grace Hopper,ou=People,dc=test"


def asked(value, reason: ClarificationReason) -> ClarificationPrompt:
    """Assert that the engine asked a question for ``reason`` and return it."""
    assert isinstance(value, ClarificationPrompt), value
    assert value.reason is reason, value
    return value


def attributes_of(value) -> tuple[str, ...]:
    assert isinstance(value, AttributeResolution), value
    return value.attributes


def reasons(prepared) -> list[ClarificationReason]:
    return [prompt.reason for prompt in prepared.clarifications]


def add_grace(directory) -> None:
    directory.add(
        GRACE,
        {**directory.person("Grace Hopper"), "mobile": ["+1 555 0100"], "manager": [ADA],
         "title": ["Admiral"]},
    )


def add_group(directory, name: str = "Engineers") -> str:
    dn = f"cn={name},ou=Staff,dc=test"
    if directory.backend is AD:
        directory.add(dn, {"objectClass": ["group"], "sAMAccountName": name.lower(),
                           "member": [ADA]})
    else:
        directory.add(dn, {"objectClass": ["groupOfUniqueNames"], "uniqueMember": [ADA]})
    return dn


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
def test_terms_are_normalized_before_lookup():
    assert normalize_term("E-mail Address") == normalize_term("the email_address") == "emailaddress"
    registry = AliasRegistry()
    assert registry.concept_for("Job Title").key == "title"
    assert registry.concept_for("their manager's").key == "manager"
    assert registry.concept_for("favourite colour") is None


@pytest.mark.parametrize("table", [ATTRIBUTE_ALIASES, ATTRIBUTE_COMMAND_ALIASES])
def test_registry_agrees_with_the_existing_filter_tables(table):
    registry = AliasRegistry()
    disagreements = {
        word: attribute
        for word, attribute in table.items()
        if (concept := registry.concept_for(word)) is None
        or concept.candidates(STANDARD)[0] != attribute
    }
    assert disagreements == {}


def test_concepts_differ_by_backend():
    registry = AliasRegistry()

    def candidates(term, backend):
        return registry.concept_for(term).candidates(backend)

    assert candidates("login", STANDARD) == ("uid",)
    assert candidates("login", AD) == ("sAMAccountName", "userPrincipalName")
    assert candidates("team", STANDARD) == ("departmentNumber", "ou")
    assert candidates("team", AD) == ("department",)
    assert (candidates("password", STANDARD), candidates("password", AD)) == (
        ("userPassword",),
        ("unicodePwd",),
    )
    assert candidates("reports to", AD) == candidates("manager", STANDARD) == ("manager",)
    assert candidates("upn", STANDARD) == ()
    assert registry.label_for("telephoneNumber", STANDARD) == "work phone"
    # No single concept means exactly this attribute, so it has no everyday label.
    assert registry.label_for("departmentNumber", STANDARD) is None
    assert registry.account_state_for("Locked out") is AccountState.LOCKED
    assert registry.account_state_for("deactivated") is AccountState.DISABLED
    assert registry.account_state_for("email") is None


def test_site_specific_concepts_can_be_registered_without_silent_collisions():
    registry = AliasRegistry()
    badge = AttributeConcept(
        key="badge", label="badge number", phrases=("badge", "badge number"),
        standard_ldap=("employeeNumber",), active_directory=("employeeID",),
    )
    registry.register(badge)
    assert registry.concept_for("Badge Number") == badge

    # "email" already means something; taking it over has to be explicit.
    intruder = AttributeConcept(key="pager_mail", label="pager", phrases=("email", "beeper"))
    with pytest.raises(ValueError, match="email"):
        registry.register(intruder)
    assert registry.concept_for("beeper") is None
    with pytest.raises(ValueError, match="already registered"):
        registry.register(badge)

    registry.register(intruder, replace=True)
    assert registry.concept_for("email").key == "pager_mail"
    # Replacing a concept drops the phrases it no longer lists.
    registry.register(badge.model_copy(update={"phrases": ("badge",)}), replace=True)
    assert registry.concept_for("badge number") is None
    # The built-in table is not shared between registries.
    assert AliasRegistry().concept_for("email").key == "email"


# ----------------------------------------------------------------------
# Attribute terms
# ----------------------------------------------------------------------
def test_everyday_terms_and_attribute_names_resolve_to_schema_spelling(directory):
    resolver = DirectoryResolver(directory.adapter())

    assert attributes_of(resolver.resolve_attribute("E-mail address")) == ("mail",)
    assert attributes_of(resolver.resolve_attribute("job title", for_write=True)) == ("title",)
    # A broad term keeps every candidate for a search.
    assert attributes_of(resolver.resolve_attribute("phone")) == (
        "telephoneNumber",
        "mobile",
        "homePhone",
    )
    # An attribute named outright, in any case, comes back as the server spells it.
    assert attributes_of(resolver.resolve_attribute("GIVENNAME")) == ("givenName",)
    assert attributes_of(resolver.resolve_attribute("postalAddress")) == ("postalAddress",)

    manager = resolver.resolve_attribute("reports to")
    assert manager.attributes == ("manager",) and manager.references_entry
    assert manager.concept == "manager"
    assert not resolver.resolve_attribute("email").references_entry


def test_a_broad_term_must_be_chosen_before_a_change(directory):
    resolver = DirectoryResolver(directory.adapter())
    prompt = asked(
        resolver.resolve_attribute("phone", for_write=True),
        ClarificationReason.AMBIGUOUS_ATTRIBUTE,
    )
    assert prompt.field == "phone"
    assert [(option.value, option.label) for option in prompt.options] == [
        ("telephoneNumber", "work phone (telephoneNumber)"),
        ("mobile", "mobile phone (mobile)"),
        ("homePhone", "home phone (homePhone)"),
    ]
    # A specific phrase needs no question.
    assert attributes_of(resolver.resolve_attribute("cell phone", for_write=True)) == ("mobile",)


def test_backend_specific_meanings(standard, active_directory):
    plain = DirectoryResolver(standard.adapter())
    domain = DirectoryResolver(active_directory.adapter())

    assert attributes_of(plain.resolve_attribute("username", for_write=True)) == ("uid",)
    login = asked(
        domain.resolve_attribute("username", for_write=True),
        ClarificationReason.AMBIGUOUS_ATTRIBUTE,
    )
    assert [option.value for option in login.options] == ["sAMAccountName", "userPrincipalName"]
    assert attributes_of(domain.resolve_attribute("username")) == (
        "sAMAccountName",
        "userPrincipalName",
    )

    assert attributes_of(domain.resolve_attribute("department", for_write=True)) == (
        "department",
    )
    asked(plain.resolve_attribute("department", for_write=True),
          ClarificationReason.AMBIGUOUS_ATTRIBUTE)
    assert attributes_of(plain.resolve_attribute("company")) == ("o",)
    assert attributes_of(domain.resolve_attribute("company")) == ("company",)
    assert attributes_of(domain.resolve_attribute("password", for_write=True)) == ("unicodePwd",)


def test_candidates_missing_from_the_server_schema_are_dropped(standard):
    resolver = DirectoryResolver(standard.adapter())
    # The mock OpenLDAP schema defines memberOf but not isMemberOf.
    assert attributes_of(resolver.resolve_attribute("member of")) == ("memberOf",)
    missing = asked(
        resolver.resolve_attribute("proxy addresses"), ClarificationReason.UNKNOWN_TERM
    )
    assert "additional email addresses" in missing.question
    # Active Directory's login-name attribute does not exist here.
    asked(resolver.resolve_attribute("sAMAccountName"), ClarificationReason.UNKNOWN_TERM)


def test_unknown_terms_offer_near_matches_and_system_attributes_are_refused(directory):
    resolver = DirectoryResolver(directory.adapter())

    typo = asked(resolver.resolve_attribute("emial"), ClarificationReason.UNKNOWN_TERM)
    assert [(option.value, option.label) for option in typo.options] == [
        ("email", "email address")
    ]
    nonsense = asked(
        resolver.resolve_attribute("favourite colour"), ClarificationReason.UNKNOWN_TERM
    )
    assert nonsense.options == ()

    assert attributes_of(resolver.resolve_attribute("groups")) == ("memberOf",)
    refused = asked(
        resolver.resolve_attribute("groups", for_write=True),
        ClarificationReason.UNSUPPORTED_REQUEST,
    )
    assert "member list" in refused.question


def test_terms_still_resolve_when_the_schema_cannot_be_read(standard, monkeypatch):
    adapter = standard.adapter()

    def no_schema(_cancelled=None):
        raise LDAPOperationError("The directory did not provide LDAP schema information.")

    monkeypatch.setattr(adapter._service, "schema_options", no_schema)
    resolver = DirectoryResolver(adapter)
    # Candidates cannot be checked, so they are all kept.
    assert attributes_of(resolver.resolve_attribute("member of")) == ("memberOf", "isMemberOf")
    assert attributes_of(resolver.resolve_attribute("x-custom-attr")) == ("x-custom-attr",)
    assert resolver.resolve_attribute("manager").references_entry
    asked(resolver.resolve_attribute("two words"), ClarificationReason.UNKNOWN_TERM)


# ----------------------------------------------------------------------
# Entries
# ----------------------------------------------------------------------
def test_a_name_resolves_only_to_a_single_exact_match(directory):
    resolver = DirectoryResolver(directory.adapter())
    add_grace(directory)
    group = add_group(directory)

    ada = resolver.resolve_entry("Ada", kind=EntryKind.USER)
    assert isinstance(ada, DirectoryEntry) and ada.dn == ADA
    assert resolver.resolve_entry("  ada@example.com ", kind=EntryKind.USER).dn == ADA
    assert resolver.resolve_entry("Engineers", kind=EntryKind.GROUP).dn == group
    assert resolver.resolve_entry("Staff", kind=EntryKind.CONTAINER).dn == "ou=Staff,dc=test"
    assert resolver.resolve_entry("Staff").dn == "ou=Staff,dc=test"
    # A DN is looked up directly, however it is cased or spaced.
    assert resolver.resolve_entry("OU=people, DC=test").dn.casefold() == "ou=people,dc=test"

    # The kind matters: there is a user called Ada, but no group.
    asked(resolver.resolve_entry("Ada", kind=EntryKind.GROUP), ClarificationReason.NOT_FOUND)
    asked(resolver.resolve_entry("cn=nobody,dc=test"), ClarificationReason.NOT_FOUND)
    blank = asked(resolver.resolve_entry("  ", kind=EntryKind.USER),
                  ClarificationReason.MISSING_INFORMATION)
    assert blank.question == "Which user do you mean?"


def test_several_matches_and_near_matches_are_offered_never_chosen(directory):
    resolver = DirectoryResolver(directory.adapter())
    add_grace(directory)
    twin = "cn=Ada,ou=Staff,dc=test"
    directory.add(twin, directory.person("Ada2"))

    ambiguous = asked(
        resolver.resolve_entry("Ada", kind=EntryKind.USER, field="manager"),
        ClarificationReason.AMBIGUOUS_TARGET,
    )
    assert ambiguous.field == "manager"
    assert {option.value for option in ambiguous.options} == {ADA, twin}
    assert all(option.label.startswith("Ada - cn=Ada,") for option in ambiguous.options)

    # One near match is still only a suggestion.
    near = asked(resolver.resolve_entry("Grace", kind=EntryKind.USER),
                 ClarificationReason.NOT_FOUND)
    assert [option.value for option in near.options] == [GRACE]
    assert near.question == "No user is named exactly 'Grace'. Did you mean one of these?"

    none = asked(resolver.resolve_entry("Zebedee", kind=EntryKind.USER),
                 ClarificationReason.NOT_FOUND)
    assert none.options == () and none.question == "No user is named exactly 'Zebedee'."
    # Filter characters in a name are data, not syntax.
    asked(resolver.resolve_entry("a)(objectClass=*", kind=EntryKind.USER),
          ClarificationReason.NOT_FOUND)

    listed = DirectoryResolver(directory.adapter(), max_options=1)
    capped = asked(listed.resolve_entry("Ada", kind=EntryKind.USER),
                   ClarificationReason.AMBIGUOUS_TARGET)
    assert len(capped.options) == 1 and "the first 1 are listed" in capped.question


# ----------------------------------------------------------------------
# Searches
# ----------------------------------------------------------------------
def test_search_conditions_become_a_runnable_spec(directory):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)
    add_grace(directory)

    prepared = resolver.prepare_search(
        [{"term": "email", "operator": "contains", "value": "example.com"}],
        kind=EntryKind.USER,
        container="People",
        attributes=["name", "e-mail"],
        size_limit=5,
    )
    assert prepared.ready and prepared.clarifications == ()
    assert prepared.spec.base_dn == "ou=People,dc=test"
    assert prepared.spec.attributes == ("cn", "displayName", "mail")
    assert prepared.spec.size_limit == 5
    assert [entry.dn for entry in adapter.search_entries(prepared.spec).entries] == [ADA]

    def found(conditions, **options) -> list[str]:
        spec = resolver.prepare_search(conditions, **options).spec
        return sorted(entry.dn for entry in adapter.search_entries(spec).entries)

    # "phone" matches any phone attribute; only Grace has one, in mobile.
    phone = SearchCondition(term="phone", operator="starts_with", value="+1 555")
    assert found([phone]) == [GRACE]
    title = {"term": "job title", "value": "Admiral"}
    mail = {"term": "email", "value": "ada@example.com"}
    assert found([title, mail], kind=EntryKind.USER) == []
    assert found([title, mail], kind=EntryKind.USER, match_all=False) == sorted([ADA, GRACE])
    assert found([{**mail, "negate": True}, {"term": "title", "operator": "present"}]) == [GRACE]
    # A manager given by name is looked up and matched by DN.
    assert found([{"term": "reports to", "value": "Ada"}]) == [GRACE]
    assert resolver.prepare_search().spec.filter == "(objectClass=*)"


def test_search_asks_instead_of_guessing(directory):
    resolver = DirectoryResolver(directory.adapter())

    prepared = resolver.prepare_search(
        [
            {"term": "favourite colour", "value": "blue"},
            {"term": "manager", "operator": "contains", "value": "Ada"},
            {"term": "manager", "value": "Zebedee"},
            {"term": "status", "value": "resting"},
        ],
        container="Nowhere",
        attributes=["emial"],
    )
    assert not prepared.ready and prepared.spec is None
    assert reasons(prepared) == [
        ClarificationReason.UNKNOWN_TERM,
        ClarificationReason.UNSUPPORTED_REQUEST,
        ClarificationReason.NOT_FOUND,
        ClarificationReason.MISSING_INFORMATION,
        ClarificationReason.NOT_FOUND,
        ClarificationReason.UNKNOWN_TERM,
    ]
    status = prepared.clarifications[3]
    assert [option.value for option in status.options] == ["enabled", "disabled", "locked"]
    assert prepared.clarifications[4].field == "container"


def test_account_state_conditions_use_each_backends_mechanism(standard, active_directory):
    domain = DirectoryResolver(active_directory.adapter())
    disabled = domain.prepare_search([{"term": "disabled", "operator": "present"}],
                                     kind=EntryKind.USER)
    assert disabled.spec.filter == (
        "(&(&(objectCategory=person)(objectClass=user))"
        "(userAccountControl:1.2.840.113556.1.4.803:=2))"
    )
    active = domain.prepare_search([{"term": "account status", "value": "Active"}])
    assert active.spec.filter == "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
    assert domain.prepare_search([{"term": "locked", "operator": "present"}]).spec.filter == (
        "(lockoutTime>=1)"
    )

    # The mock OpenLDAP schema has a lockout attribute but nothing that disables.
    adapter = standard.adapter()
    plain = DirectoryResolver(adapter)
    standard.add("cn=Locked,ou=Staff,dc=test",
                 {**standard.person("Locked"), "pwdAccountLockedTime": "20260101000000Z"})
    locked = plain.prepare_search([{"term": "locked", "operator": "present"}])
    assert [entry.dn for entry in adapter.search_entries(locked.spec).entries] == [
        "cn=Locked,ou=Staff,dc=test"
    ]
    unsupported = plain.prepare_search([{"term": "disabled", "operator": "present"}])
    assert reasons(unsupported) == [ClarificationReason.UNSUPPORTED_REQUEST]
    assert reasons(plain.prepare_account_state("Ada", AccountState.DISABLED)) == [
        ClarificationReason.UNSUPPORTED_REQUEST
    ]
    # Only the status attributes this server defines may be requested.
    assert adapter.known_attributes(adapter.account_status_attributes) == (
        "pwdAccountLockedTime",
    )


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
def test_create_asks_for_what_cannot_be_assumed(directory):
    resolver = DirectoryResolver(directory.adapter())

    nothing = resolver.prepare_create(EntryKind.ANY, None)
    assert not nothing.ready and nothing.plan is None and nothing.preview is None
    assert [(prompt.reason, prompt.field) for prompt in nothing.clarifications] == [
        (ClarificationReason.MISSING_INFORMATION, "kind"),
        (ClarificationReason.MISSING_INFORMATION, "name"),
        (ClarificationReason.MISSING_INFORMATION, "container"),
    ]
    # The existing organizational units are offered as places to create it.
    assert {option.value for option in nothing.clarifications[2].options} == {
        "ou=People,dc=test",
        "ou=Staff,dc=test",
        "ou=Team,ou=People,dc=test",
    }

    unclear = resolver.prepare_create(
        EntryKind.USER, "Grace Hopper", container="Nowhere",
        attributes={"phone": "+1 555 0100", "favourite colour": "blue", "manager": "Zebedee"},
    )
    assert unclear.plan is None
    assert [(prompt.reason, prompt.field) for prompt in unclear.clarifications] == [
        (ClarificationReason.NOT_FOUND, "container"),
        (ClarificationReason.AMBIGUOUS_ATTRIBUTE, "phone"),
        (ClarificationReason.UNKNOWN_TERM, "favourite colour"),
        (ClarificationReason.NOT_FOUND, "manager"),
    ]


def test_create_standard_user_asks_for_required_attributes_then_applies(standard):
    adapter = standard.adapter()
    resolver = DirectoryResolver(adapter)
    details = {"E-mail": "grace@example.com", "mail": "g.hopper@example.com",
               "reports to": "Ada", "password": "open-sesame"}

    incomplete = resolver.prepare_create(
        EntryKind.USER, "Hopper, Grace", container="People", attributes=details
    )
    assert not incomplete.ready
    (question,) = incomplete.clarifications
    assert (question.reason, question.field) == (ClarificationReason.MISSING_INFORMATION, "sn")
    assert question.question.endswith("What should the last name (sn) be?")
    assert errors(incomplete) == {IssueCode.MISSING_REQUIRED_ATTRIBUTE}

    prepared = resolver.prepare_create(
        EntryKind.USER, "Hopper, Grace", container="People",
        attributes={**details, "last name": "Hopper"},
    )
    assert prepared.ready and isinstance(prepared.plan, CreatePlan)
    # The comma in the name is escaped, and two terms for one attribute are combined.
    assert prepared.plan.dn == r"cn=Hopper\, Grace,ou=People,dc=test"
    assert prepared.plan.attributes["mail"] == ("grace@example.com", "g.hopper@example.com")
    assert prepared.plan.attributes["manager"] == (ADA,)
    assert prepared.plan.attributes["objectClass"] == adapter.user_object_classes
    # The plan carries the password; nothing that is displayed or serialized does.
    assert prepared.plan.attributes["userPassword"] == ("open-sesame",)
    assert "open-sesame" not in prepared.model_dump_json() + repr(prepared)
    assert "plan" not in prepared.model_dump()

    result = adapter.execute(prepared.plan, dry_run=False, confirmed=True)
    assert result.applied
    stored = standard.read(prepared.plan.dn)
    assert stored["sn"] == [b"Hopper"] and stored["manager"] == [ADA.encode()]


def test_create_standard_group_uses_the_member_attribute_of_its_class(standard):
    resolver = DirectoryResolver(standard.adapter())
    # groupOfNames must have a member, and lists members in "member" although the
    # schema also defines uniqueMember and memberUid.
    empty = resolver.prepare_create(EntryKind.GROUP, "Engineers", container="Staff")
    assert [prompt.field for prompt in empty.clarifications] == ["member"]

    group = resolver.prepare_create(
        EntryKind.GROUP, "Engineers", container="Staff", attributes={"members": ["Ada"]}
    )
    assert group.ready
    assert group.plan.attributes["objectClass"] == ("top", "groupOfNames")
    assert group.plan.attributes["member"] == (ADA,)


def test_create_active_directory_user_and_group(active_directory):
    adapter = active_directory.adapter()
    resolver = DirectoryResolver(adapter)

    no_login = resolver.prepare_create(EntryKind.USER, "Grace Hopper", container="People")
    assert [prompt.field for prompt in no_login.clarifications] == ["sAMAccountName"]
    assert "pre-Windows 2000 logon name" in no_login.clarifications[0].question
    # The preview itself is allowed; the question is about a generated login name.
    assert no_login.preview.can_apply and not no_login.ready

    vague = resolver.prepare_create(
        EntryKind.USER, "Grace Hopper", container="People", attributes={"username": "ghopper"}
    )
    assert reasons(vague) == [ClarificationReason.AMBIGUOUS_ATTRIBUTE]

    user = resolver.prepare_create(
        EntryKind.USER, "Grace Hopper", container="People",
        attributes={"sam account name": "ghopper", "department": "Navy"},
    )
    assert user.ready and warnings(user) == {IssueCode.ACCOUNT_STATE}
    assert user.plan.attributes["department"] == ("Navy",)

    group = resolver.prepare_create(
        EntryKind.GROUP, "Engineers", container="Staff", attributes={"members": ["Ada"]}
    )
    assert group.ready and group.plan.dn == "cn=Engineers,ou=Staff,dc=test"
    assert group.plan.attributes["objectClass"] == ("top", "group")
    assert group.plan.attributes["member"] == (ADA,)

    unit = resolver.prepare_create(EntryKind.CONTAINER, "Labs", container="dc=test")
    assert unit.ready and unit.plan.dn == "ou=Labs,dc=test"
    adapter.execute(unit.plan, dry_run=False, confirmed=True)
    assert active_directory.read("ou=Labs,dc=test")["ou"] == [b"Labs"]


# ----------------------------------------------------------------------
# Update, account state, delete, move
# ----------------------------------------------------------------------
def test_update_resolves_target_terms_and_references(directory):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)
    add_grace(directory)

    prepared = resolver.prepare_update(
        "Grace Hopper",
        [
            RequestedChange(term="job title", values="Rear Admiral"),
            {"term": "description", "operation": "add", "values": ["Pioneer"]},
            {"term": "reports to", "operation": "delete"},
        ],
        kind=EntryKind.USER,
    )
    assert prepared.ready and isinstance(prepared.plan, UpdatePlan)
    assert prepared.plan.dn == GRACE
    assert [(c.attribute, c.operation.value, c.values) for c in prepared.plan.changes] == [
        ("title", "replace", ("Rear Admiral",)),
        ("description", "add", ("Pioneer",)),
        ("manager", "delete", ()),
    ]
    assert prepared.preview.after.get("title") == ("Rear Admiral",)

    adapter.execute(prepared.plan, dry_run=False, confirmed=True)
    stored = directory.read(GRACE)
    assert stored["title"] == [b"Rear Admiral"] and "manager" not in stored

    by_name = resolver.prepare_update("Ada", [{"term": "manager", "values": "Grace Hopper"}])
    assert by_name.plan.changes[0].values == (GRACE,)


def test_update_collects_every_open_question(directory):
    resolver = DirectoryResolver(directory.adapter())
    directory.add("cn=Ada,ou=Staff,dc=test", directory.person("Ada2"))

    prepared = resolver.prepare_update(
        "Ada",
        [{"term": "phone", "values": "1"}, {"term": "groups", "operation": "add", "values": "x"}],
        kind=EntryKind.USER,
    )
    assert prepared.plan is None and not prepared.ready
    assert [(prompt.reason, prompt.field) for prompt in prepared.clarifications] == [
        (ClarificationReason.AMBIGUOUS_TARGET, "target"),
        (ClarificationReason.AMBIGUOUS_ATTRIBUTE, "phone"),
        (ClarificationReason.UNSUPPORTED_REQUEST, "groups"),
    ]


def test_group_members_use_the_groups_own_member_attribute(directory):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)
    add_grace(directory)
    group = add_group(directory)

    prepared = resolver.prepare_update(
        "Engineers", [{"term": "members", "operation": "add", "values": ["Grace Hopper"]}],
        kind=EntryKind.GROUP,
    )
    expected = "member" if directory.backend is AD else "uniqueMember"
    assert prepared.ready
    assert [(c.attribute, c.values) for c in prepared.plan.changes] == [(expected, (GRACE,))]
    adapter.execute(prepared.plan, dry_run=False, confirmed=True)
    assert sorted(directory.read(group)[expected]) == sorted([ADA.encode(), GRACE.encode()])


def test_active_directory_account_is_disabled_through_account_control(active_directory):
    adapter = active_directory.adapter()
    resolver = DirectoryResolver(adapter)
    active_directory.add("cn=Linus,ou=Staff,dc=test",
                         {**active_directory.person("Linus"), "userAccountControl": "66048"})

    prepared = resolver.prepare_account_state("Linus", AccountState.DISABLED)
    assert prepared.ready
    assert [(c.attribute, c.values) for c in prepared.plan.changes] == [
        ("userAccountControl", (66050,))
    ]
    adapter.execute(prepared.plan, dry_run=False, confirmed=True)
    assert active_directory.read("cn=Linus,ou=Staff,dc=test")["userAccountControl"] == [b"66050"]

    assert reasons(resolver.prepare_account_state("Linus", AccountState.LOCKED)) == [
        ClarificationReason.UNSUPPORTED_REQUEST
    ]
    assert reasons(resolver.prepare_account_state("Zebedee", AccountState.DISABLED)) == [
        ClarificationReason.NOT_FOUND
    ]
    # Ada was seeded without userAccountControl, so there is nothing to toggle.
    assert reasons(resolver.prepare_account_state("Ada", AccountState.ENABLED)) == [
        ClarificationReason.UNSUPPORTED_REQUEST
    ]


def test_delete_asks_before_removing_a_subtree(directory):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)

    leaf = resolver.prepare_delete("Ada", kind=EntryKind.USER)
    assert leaf.ready and leaf.plan.dn == ADA and not leaf.plan.recursive

    unit = resolver.prepare_delete("People", kind=EntryKind.CONTAINER)
    assert not unit.ready
    (question,) = unit.clarifications
    assert (question.reason, question.field) == (
        ClarificationReason.MISSING_INFORMATION,
        "recursive",
    )
    assert [option.value for option in question.options] == ["true", "false"]

    whole = resolver.prepare_delete("People", kind=EntryKind.CONTAINER, recursive=True)
    assert whole.ready and len(whole.preview.affected_dns) == 4
    assert reasons(resolver.prepare_delete("Zebedee")) == [ClarificationReason.NOT_FOUND]
    # Preparing a delete never deletes.
    assert directory.read(ADA) is not None


def test_move_and_rename_by_name(directory):
    adapter = directory.adapter()
    resolver = DirectoryResolver(adapter)

    nowhere = resolver.prepare_move("Ada", kind=EntryKind.USER)
    assert [(prompt.reason, prompt.field) for prompt in nowhere.clarifications] == [
        (ClarificationReason.MISSING_INFORMATION, "container")
    ]
    assert "ou=Staff,dc=test" in {option.value for option in nowhere.clarifications[0].options}

    rename = resolver.prepare_move("Ada", kind=EntryKind.USER, new_name="Lovelace, Ada")
    assert rename.ready and isinstance(rename.plan, MovePlan)
    assert (rename.plan.new_rdn, rename.plan.new_superior) == (r"cn=Lovelace\, Ada", None)

    both = resolver.prepare_move("Ada", new_name="Ada Byron", destination="Staff")
    assert both.ready and both.preview.resulting_dn == "cn=Ada Byron,ou=Staff,dc=test"
    # ldap3's mock ignores the new name when a new parent is also given, so only
    # the move is executed here; the live tests cover both in one operation.
    moved = resolver.prepare_move("Ada", kind=EntryKind.USER, destination="Staff")
    adapter.execute(moved.plan, dry_run=False, confirmed=True)
    assert directory.read(ADA) is None
    assert directory.read("cn=Ada,ou=Staff,dc=test") is not None

    unit = resolver.prepare_move("Team", kind=EntryKind.CONTAINER, new_name="Crew")
    # A rename keeps the attribute the entry is already named by.
    assert unit.plan.new_rdn == "ou=Crew"
    assert reasons(resolver.prepare_move("Ada", destination="Nowhere")) == [
        ClarificationReason.NOT_FOUND
    ]
