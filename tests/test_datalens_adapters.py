"""Directory adapter behaviour against the in-memory mock directory.

See ``datalens_support.py`` for how the mock directory is built; the fixtures are
in ``conftest.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from datalens_support import (
    ADA,
    BASE_DN,
    BIND_DN,
    change,
    errors,
    warnings,
)

from datasifter.datalens import (
    ActiveDirectoryAdapter,
    ChangeOperation,
    ConfirmationRequiredError,
    DirectoryBackend,
    DirectoryEntry,
    IssueCode,
    MutationRejectedError,
    ReadOnlyDirectoryError,
    SearchSpec,
    StandardLdapAdapter,
    detect_backend,
)
from datasifter.datalens.adapters.active_directory import encode_unicode_pwd
from datasifter.directory.service import LDAPOperationError

STANDARD = DirectoryBackend.STANDARD_LDAP
AD = DirectoryBackend.ACTIVE_DIRECTORY


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------
def test_search_uses_profile_base_and_reports_truncation(directory):
    adapter = directory.adapter()
    units = SearchSpec(filter="(objectClass=organizationalUnit)", attributes=["ou"])

    everything = adapter.search_entries(units)
    assert everything.base_dn == BASE_DN and everything.backend is directory.backend
    assert sorted(entry.first("ou") for entry in everything.entries) == ["People", "Staff", "Team"]
    assert not everything.truncated

    limited = adapter.search_entries(units.model_copy(update={"size_limit": 2}))
    assert len(limited.entries) == 2 and limited.truncated
    exact = adapter.search_entries(units.model_copy(update={"size_limit": 3, "page_size": 1}))
    assert len(exact.entries) == 3 and not exact.truncated

    scoped = adapter.search_entries(
        SearchSpec(base_dn="ou=People,dc=test", scope="base", attributes=["ou"])
    )
    assert [entry.dn for entry in scoped.entries] == ["ou=People,dc=test"]


def test_get_entry_distinguishes_missing_entries_from_failures(directory, monkeypatch):
    adapter = directory.adapter()
    assert adapter.get_entry("cn=nobody,ou=People,dc=test") is None
    assert adapter.get_entry(ADA).first("mail") == "ada@example.com"

    def unreachable(*_args, **_kwargs):
        raise LDAPOperationError("LDAP operation failed: connection refused")

    monkeypatch.setattr(adapter._service, "search", unreachable)
    with pytest.raises(LDAPOperationError):
        adapter.get_entry(ADA)


def test_user_filter_matches_identity_attributes_and_escapes_input(directory):
    adapter = directory.adapter()
    found = adapter.search_entries(SearchSpec(filter=adapter.user_filter("Ada"), attributes=["cn"]))
    assert [entry.dn for entry in found.entries] == [ADA]
    assert "(cn=a\\29\\28objectClass=\\2a)" in adapter.user_filter("a)(objectClass=*")
    # The escaped text is still one valid filter.
    SearchSpec(filter=adapter.user_filter("a)(objectClass=*"))


# ----------------------------------------------------------------------
# The dry-run and confirmation gate
# ----------------------------------------------------------------------
def test_create_is_a_preview_until_confirmed(directory):
    adapter = directory.adapter()
    dn = "cn=Grace,ou=People,dc=test"

    preview = adapter.create_entry(dn, directory.person("Grace"))
    assert preview.dry_run and not preview.applied and preview.preview.can_apply
    assert preview.preview.after.get("cn") == ("Grace",)
    assert directory.read(dn) is None

    with pytest.raises(ConfirmationRequiredError) as unconfirmed:
        adapter.create_entry(dn, directory.person("Grace"), dry_run=False)
    assert unconfirmed.value.preview.resulting_dn == dn
    assert directory.read(dn) is None

    applied = adapter.create_entry(dn, directory.person("Grace"), dry_run=False, confirmed=True)
    assert applied.applied and not applied.dry_run
    # The naming value from the DN was written although the caller did not list it.
    assert directory.read(dn)["cn"] == [b"Grace"]


def test_create_reports_problems_and_writes_nothing(directory):
    adapter = directory.adapter()
    person = directory.person("Grace")

    assert errors(adapter.create_entry(ADA, person)) == {IssueCode.ENTRY_EXISTS}
    assert errors(adapter.create_entry("cn=Grace,ou=Nowhere,dc=test", person)) == {
        IssueCode.PARENT_NOT_FOUND
    }
    assert IssueCode.OUTSIDE_BASE_DN in errors(adapter.create_entry("cn=Grace,dc=other", person))
    assert errors(
        adapter.create_entry("cn=Grace,ou=People,dc=test", {**person, "cn": ["Someone Else"]})
    ) == {IssueCode.RDN_ATTRIBUTE}
    unknown = adapter.create_entry(
        "cn=Grace,ou=People,dc=test",
        {**person, "objectClass": [*person["objectClass"], "noSuchClass"], "noSuchAttribute": "1"},
    )
    assert errors(unknown) == {IssueCode.UNKNOWN_OBJECT_CLASS, IssueCode.UNKNOWN_ATTRIBUTE}
    too_many = adapter.create_entry(
        "cn=Grace,ou=People,dc=test", {**person, "displayName": ["one", "two"]}
    )
    assert errors(too_many) == {IssueCode.SINGLE_VALUED_ATTRIBUTE}

    with pytest.raises(MutationRejectedError) as rejected:
        adapter.create_entry(
            "cn=Grace,ou=Nowhere,dc=test", person, dry_run=False, confirmed=True
        )
    assert "ou=Nowhere,dc=test does not exist" in str(rejected.value)
    assert directory.read("cn=Grace,ou=Nowhere,dc=test") is None


def test_previews_continue_without_a_readable_schema(directory, monkeypatch):
    adapter = directory.adapter()

    def no_schema(_cancelled=None):
        raise LDAPOperationError("The directory did not provide LDAP schema information.")

    monkeypatch.setattr(adapter._service, "schema_options", no_schema)
    result = adapter.create_entry(
        "cn=Grace,ou=People,dc=test", {**directory.person("Grace"), "noSuchAttribute": "1"}
    )
    assert warnings(result) >= {IssueCode.SCHEMA_UNAVAILABLE}
    assert IssueCode.UNKNOWN_ATTRIBUTE not in errors(result)


@pytest.mark.parametrize("profile_read_only", [True, False])
def test_read_only_blocks_changes_before_connecting(directory, profile_read_only):
    # Read-only can come from the profile or be forced on the adapter.
    adapter = (
        directory.adapter(profile_read_only=True)
        if profile_read_only
        else directory.adapter(read_only=True)
    )
    attempts = [
        lambda **gate: adapter.create_entry("cn=Grace,ou=People,dc=test",
                                            directory.person("Grace"), **gate),
        lambda **gate: adapter.update_entry(ADA, [change("mail", "delete")], **gate),
        lambda **gate: adapter.delete_entry(ADA, **gate),
        lambda **gate: adapter.move_entry(ADA, new_rdn="cn=Ada Byron", **gate),
    ]
    opened = directory.connections
    for attempt in attempts:
        with pytest.raises(ReadOnlyDirectoryError):
            attempt(dry_run=False, confirmed=True)
    assert directory.connections == opened

    # A preview is still allowed, and says why the change cannot be applied.
    for attempt in attempts:
        assert IssueCode.READ_ONLY in errors(attempt())
    assert directory.read(ADA)["mail"] == [b"ada@example.com"]


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------
def test_update_previews_then_applies_all_changes(directory):
    adapter = directory.adapter()
    changes = [
        change("description", "add", "first", "second"),
        change("mail", "replace", "ada.lane@example.com"),
        {"attribute": "displayName", "operation": "add", "values": ["Ada Lane"]},
    ]

    preview = adapter.update_entry(ADA, changes)
    assert preview.preview.can_apply and not preview.preview.issues
    assert preview.preview.before.get("mail") == ("ada@example.com",)
    assert preview.preview.after.get("mail") == ("ada.lane@example.com",)
    assert preview.preview.after.get("description") == ("first", "second")
    assert directory.read(ADA)["mail"] == [b"ada@example.com"]

    adapter.update_entry(ADA, changes, dry_run=False, confirmed=True)
    stored = directory.read(ADA)
    assert stored["mail"] == [b"ada.lane@example.com"]
    assert sorted(stored["description"]) == [b"first", b"second"]

    adapter.update_entry(
        ADA,
        [change("description", "delete", "first"), change("displayName", "delete")],
        dry_run=False,
        confirmed=True,
    )
    stored = directory.read(ADA)
    assert stored["description"] == [b"second"] and "displayName" not in stored


def test_update_refuses_changes_the_directory_would_reject(directory):
    adapter = directory.adapter()

    assert errors(adapter.update_entry("cn=nobody,ou=People,dc=test", [change("mail", "delete")])) == {
        IssueCode.ENTRY_NOT_FOUND
    }
    blocked = adapter.update_entry(
        ADA,
        [
            change("memberOf", "add", "cn=Admins,dc=test"),
            change("cn", "replace", "Renamed"),
            change("noSuchAttribute", "add", "x"),
            change("displayName", "add", "one", "two"),
        ],
    )
    assert errors(blocked) == {
        IssueCode.SYSTEM_ATTRIBUTE,
        IssueCode.RDN_ATTRIBUTE,
        IssueCode.UNKNOWN_ATTRIBUTE,
        IssueCode.SINGLE_VALUED_ATTRIBUTE,
    }
    with pytest.raises(MutationRejectedError):
        adapter.update_entry(ADA, [change("cn", "replace", "Renamed")], dry_run=False, confirmed=True)
    assert directory.read(ADA)["cn"] == [b"Ada"]


def test_update_warns_about_redundant_changes(directory):
    adapter = directory.adapter()

    duplicate = adapter.update_entry(ADA, [change("mail", "add", "ADA@example.com")])
    assert duplicate.preview.can_apply
    assert warnings(duplicate) == {IssueCode.VALUE_CONFLICT, IssueCode.NO_CHANGE}
    missing = adapter.update_entry(ADA, [change("description", "delete", "absent")])
    assert IssueCode.VALUE_CONFLICT in warnings(missing)
    same = adapter.update_entry(ADA, [change("mail", "replace", "ada@example.com")])
    assert warnings(same) == {IssueCode.NO_CHANGE}


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------
def test_delete_needs_recursion_for_a_subtree(directory):
    adapter = directory.adapter()
    people = "ou=People,dc=test"

    assert errors(adapter.delete_entry(people)) == {IssueCode.HAS_CHILDREN}
    with pytest.raises(MutationRejectedError):
        adapter.delete_entry(people, dry_run=False, confirmed=True)
    assert directory.read(ADA) is not None

    preview = adapter.delete_entry(people, recursive=True).preview
    assert preview.can_apply and preview.after is None and preview.resulting_dn is None
    # Deepest entries first, the requested entry last.
    assert preview.affected_dns[0] == "cn=Deep,ou=Team,ou=People,dc=test"
    assert preview.affected_dns[-1] == people
    assert set(preview.affected_dns[1:-1]) == {"ou=Team,ou=People,dc=test", ADA}
    assert "3 subordinate entries" in preview.summary

    adapter.delete_entry(people, recursive=True, dry_run=False, confirmed=True)
    assert all(directory.read(dn) is None for dn in preview.affected_dns)
    assert directory.read("ou=Staff,dc=test") is not None


def test_delete_leaf_and_missing_entry(directory):
    adapter = directory.adapter()
    assert errors(adapter.delete_entry("cn=nobody,ou=People,dc=test")) == {
        IssueCode.ENTRY_NOT_FOUND
    }
    result = adapter.delete_entry(ADA, dry_run=False, confirmed=True)
    assert result.applied and result.preview.before.get("mail") == ("ada@example.com",)
    assert directory.read(ADA) is None


def test_delete_protects_the_base_and_the_bind_account(directory):
    adapter = directory.adapter()
    assert IssueCode.PROTECTED_ENTRY in errors(adapter.delete_entry(BASE_DN, recursive=True))
    assert errors(adapter.delete_entry(BIND_DN)) == {IssueCode.PROTECTED_ENTRY}
    assert IssueCode.OUTSIDE_BASE_DN in errors(adapter.delete_entry("cn=x,dc=elsewhere"))


# ----------------------------------------------------------------------
# Move and rename
# ----------------------------------------------------------------------
def test_move_renames_then_reparents(directory):
    adapter = directory.adapter()

    rename = adapter.move_entry(ADA, new_rdn="cn=Ada Byron", dry_run=False, confirmed=True)
    renamed = "cn=Ada Byron,ou=People,dc=test"
    assert rename.preview.resulting_dn == renamed
    assert rename.preview.after.get("cn") == ("Ada Byron",)
    assert rename.preview.summary == f"Rename {ADA} to cn=Ada Byron."
    assert directory.read(ADA) is None
    assert directory.read(renamed)["cn"] == [b"Ada Byron"]

    move = adapter.move_entry(renamed, new_superior="ou=Staff,dc=test", dry_run=False,
                              confirmed=True)
    assert move.preview.resulting_dn == "cn=Ada Byron,ou=Staff,dc=test"
    assert directory.read("cn=Ada Byron,ou=Staff,dc=test")["mail"] == [b"ada@example.com"]

    both = adapter.move_entry(
        "ou=Team,ou=People,dc=test", new_rdn="ou=Crew", new_superior="ou=Staff,dc=test"
    )
    assert both.preview.resulting_dn == "ou=Crew,ou=Staff,dc=test"
    # Subordinate entries are announced, not refused.
    assert both.preview.can_apply and warnings(both) == {IssueCode.HAS_CHILDREN}


def test_move_refuses_impossible_destinations(directory):
    adapter = directory.adapter()
    people = "ou=People,dc=test"

    assert IssueCode.INVALID_TARGET in errors(
        adapter.move_entry(people, new_superior="ou=Team,ou=People,dc=test")
    )
    assert errors(adapter.move_entry(ADA, new_superior="ou=Nowhere,dc=test")) == {
        IssueCode.PARENT_NOT_FOUND
    }
    directory.add("cn=Ada,ou=Staff,dc=test", directory.person("Ada2"))
    assert errors(adapter.move_entry(ADA, new_superior="ou=Staff,dc=test")) == {
        IssueCode.ENTRY_EXISTS
    }
    assert errors(adapter.move_entry(ADA, new_rdn="cn=Ada", new_superior=people)) == {
        IssueCode.NO_CHANGE
    }
    assert errors(adapter.move_entry("cn=nobody,ou=People,dc=test", new_rdn="cn=x")) == {
        IssueCode.ENTRY_NOT_FOUND
    }
    assert IssueCode.OUTSIDE_BASE_DN in errors(adapter.move_entry(ADA, new_superior="dc=other"))
    assert IssueCode.PROTECTED_ENTRY in errors(adapter.move_entry(BASE_DN, new_rdn="dc=renamed"))
    # Moving the bind account is allowed but flagged.
    assert IssueCode.PROTECTED_ENTRY in warnings(adapter.move_entry(BIND_DN, new_rdn="cn=root"))
    assert directory.read(ADA) is not None


# ----------------------------------------------------------------------
# Secrets
# ----------------------------------------------------------------------
def test_results_and_errors_never_carry_password_values(standard):
    adapter = standard.adapter()
    dn = "cn=Grace,ou=People,dc=test"
    attributes = {**standard.person("Grace"), "userPassword": "open-sesame"}

    with pytest.raises(ConfirmationRequiredError) as unconfirmed:
        adapter.create_entry(dn, attributes, dry_run=False)
    created = adapter.create_entry(dn, attributes, dry_run=False, confirmed=True)
    updated = adapter.update_entry(
        dn, [change("userPassword", "replace", "new-sesame")], dry_run=False, confirmed=True
    )

    for result in (unconfirmed.value.preview, created, updated):
        text = result.model_dump_json()
        assert "sesame" not in text and "<redacted>" in text
    assert standard.read(dn)["userPassword"] == [b"new-sesame"]


# ----------------------------------------------------------------------
# Standard LDAP
# ----------------------------------------------------------------------
def test_standard_create_requires_attributes_the_schema_demands(standard):
    adapter = standard.adapter()
    result = adapter.create_entry("cn=Grace,ou=People,dc=test", {"objectClass": "inetOrgPerson"})
    assert errors(result) == {IssueCode.MISSING_REQUIRED_ATTRIBUTE}
    assert [issue.attribute for issue in result.preview.issues] == ["sn"]

    removing = adapter.update_entry(ADA, [change("sn", "delete")])
    assert errors(removing) == {IssueCode.MISSING_REQUIRED_ATTRIBUTE}


def test_standard_group_members_and_account_status(standard):
    adapter = standard.adapter()
    assert isinstance(adapter, StandardLdapAdapter)
    group = DirectoryEntry(dn="cn=g,dc=test", attributes={"objectClass": ("groupOfUniqueNames",)})
    posix = DirectoryEntry(dn="cn=p,dc=test", attributes={"objectClass": ("posixGroup",)})
    names = DirectoryEntry(dn="cn=n,dc=test", attributes={"objectClass": ("groupOfNames",)})
    assert [adapter.member_attribute(entry) for entry in (group, posix, names)] == [
        "uniqueMember",
        "memberUid",
        "member",
    ]

    def status(**attributes):
        entry = DirectoryEntry(
            dn=ADA, attributes={name: (value,) for name, value in attributes.items()}
        )
        return adapter.account_status(entry)

    # Without evidence nothing is claimed.
    assert (status().enabled, status().locked) == (None, None)
    assert status(nsAccountLock="TRUE").enabled is False
    assert status(**{"ds-pwp-account-disabled": False}).enabled is True
    assert status(pwdAccountLockedTime="20260101000000Z").locked is True


# ----------------------------------------------------------------------
# Active Directory
# ----------------------------------------------------------------------
def test_ad_new_user_without_account_name_or_password_is_allowed_with_warnings(active_directory):
    adapter = active_directory.adapter()
    result = adapter.create_entry("CN=Grace Hopper,ou=People,dc=test", {"objectClass": "user"})
    # The schema marks instanceType, objectCategory and others as required, but the
    # domain controller supplies them, so they are not reported as missing.
    assert result.preview.can_apply
    assert warnings(result) == {IssueCode.MISSING_REQUIRED_ATTRIBUTE, IssueCode.ACCOUNT_STATE}


def test_ad_rejects_requests_the_domain_controller_refuses(active_directory):
    adapter = active_directory.adapter()
    dn = "CN=Grace Hopper,ou=People,dc=test"
    user = {"objectClass": "user", "sAMAccountName": "ghopper"}

    def create(dn=dn, **extra):
        return adapter.create_entry(dn, {**user, **extra})

    assert errors(create(sAMAccountName="g" * 21)) == {IssueCode.INVALID_VALUE}
    assert not errors(create(sAMAccountName="g" * 20))
    assert errors(create(userAccountControl=512)) == {IssueCode.ACCOUNT_STATE}
    assert not errors(create(userAccountControl=514))
    assert errors(create(userPassword="pw")) == {IssueCode.UNSUPPORTED}
    assert errors(create(unicodePwd="pw")) == {IssueCode.INSECURE_TRANSPORT}
    # Named by uid, the entry also lacks the cn that a user must have.
    assert errors(create(dn="uid=ghopper,ou=People,dc=test")) == {
        IssueCode.RDN_ATTRIBUTE,
        IssueCode.MISSING_REQUIRED_ATTRIBUTE,
    }
    assert errors(
        adapter.create_entry(
            "cn=Unit,dc=test", {"objectClass": "organizationalUnit", "ou": "Unit"}
        )
    ) == {IssueCode.RDN_ATTRIBUTE}
    assert errors(adapter.update_entry(ADA, [change("unicodePwd", "replace", "pw")])) == {
        IssueCode.INSECURE_TRANSPORT
    }


def test_ad_writes_passwords_encoded_over_tls_and_redacts_them(active_directory):
    adapter = active_directory.adapter(transport="ldaps")
    dn = "CN=Grace Hopper,ou=People,dc=test"
    result = adapter.create_entry(
        dn,
        {"objectClass": "user", "sAMAccountName": "ghopper", "userAccountControl": 512,
         "unicodePwd": "Tr1cky-Pass"},
        dry_run=False,
        confirmed=True,
    )
    assert not result.preview.issues
    assert "Tr1cky-Pass" not in result.model_dump_json()
    stored = active_directory.read(dn)
    assert stored["unicodePwd"] == [encode_unicode_pwd("Tr1cky-Pass")]
    assert stored["unicodePwd"][0].decode("utf-16-le") == '"Tr1cky-Pass"'
    assert stored["userAccountControl"] == [b"512"]


def test_ad_protects_critical_system_objects(active_directory):
    adapter = active_directory.adapter()
    active_directory.add(
        "cn=Krbtgt,ou=Staff,dc=test",
        {**active_directory.person("krbtgt"), "isCriticalSystemObject": "TRUE"},
    )
    assert errors(adapter.delete_entry("cn=Krbtgt,ou=Staff,dc=test")) == {
        IssueCode.PROTECTED_ENTRY
    }
    whole_unit = adapter.delete_entry("ou=Staff,dc=test", recursive=True)
    assert errors(whole_unit) == {IssueCode.PROTECTED_ENTRY}
    assert "cn=Krbtgt,ou=Staff,dc=test" in whole_unit.preview.issues[0].message
    assert errors(adapter.move_entry("cn=Krbtgt,ou=Staff,dc=test", new_rdn="cn=Other")) == {
        IssueCode.PROTECTED_ENTRY
    }


def test_ad_rename_must_keep_naming_attribute_and_drop_old_name(active_directory):
    adapter = active_directory.adapter()
    assert errors(adapter.move_entry(ADA, new_rdn="uid=ada")) == {IssueCode.RDN_ATTRIBUTE}
    assert errors(adapter.move_entry(ADA, new_rdn="cn=Ada Byron", delete_old_rdn=False)) == {
        IssueCode.UNSUPPORTED
    }
    # Keeping the old RDN is irrelevant when the entry is only moved.
    assert not errors(
        adapter.move_entry(ADA, new_superior="ou=Staff,dc=test", delete_old_rdn=False)
    )


def test_ad_account_status_and_disable_change(active_directory):
    adapter = active_directory.adapter()
    assert isinstance(adapter, ActiveDirectoryAdapter)

    def user(**attributes):
        return DirectoryEntry(
            dn=ADA, attributes={name: (value,) for name, value in attributes.items()}
        )

    active = adapter.account_status(user(userAccountControl=66048))
    assert active.enabled is True and active.locked is None
    assert active.flags == ("NORMAL_ACCOUNT", "DONT_EXPIRE_PASSWORD")
    assert adapter.account_status(user(userAccountControl="514")).enabled is False
    assert adapter.account_status(user()).enabled is None

    never_locked = datetime(1601, 1, 1, tzinfo=timezone.utc)
    locked_at = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert adapter.account_status(user(lockoutTime=never_locked)).locked is False
    assert adapter.account_status(user(lockoutTime=locked_at)).locked is True
    assert adapter.account_status(user(lockoutTime=0)).locked is False

    disable = adapter.account_control_change(user(userAccountControl=66048), disabled=True)
    assert (disable.operation, disable.values) == (ChangeOperation.REPLACE, (66050,))
    enable = adapter.account_control_change(user(userAccountControl=66050), disabled=False)
    assert enable.values == (66048,)
    with pytest.raises(ValueError, match="userAccountControl"):
        adapter.account_control_change(user(), disabled=True)

    # The change goes through the normal update path.
    active_directory.add("cn=Linus,ou=Staff,dc=test",
                         {**active_directory.person("linus"), "userAccountControl": "512"})
    linus = adapter.get_entry("cn=Linus,ou=Staff,dc=test", adapter.account_status_attributes)
    adapter.update_entry(
        linus.dn, [adapter.account_control_change(linus, disabled=True)],
        dry_run=False, confirmed=True,
    )
    assert active_directory.read(linus.dn)["userAccountControl"] == [b"514"]


# ----------------------------------------------------------------------
# Backend detection
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("root_dse", "expected"),
    [
        ({"supportedCapabilities": ["1.2.840.113556.1.4.800", "1.2.840.113556.1.4.1670"]}, AD),
        ({"SupportedCapabilities": ["1.2.840.113556.1.4.1851"]}, AD),
        ({"vendorName": ["Ping Identity Corporation"], "supportedCapabilities": []}, STANDARD),
        ({}, STANDARD),
    ],
)
def test_detect_backend_reads_root_dse_capabilities(standard, monkeypatch, root_dse, expected):
    service = standard.adapter()._service
    requests = []

    def root_search(base_dn, _filter, **options):
        requests.append((base_dn, options["scope"]))
        return [{"dn": "", "attributes": root_dse}]

    monkeypatch.setattr(service, "search", root_search)
    assert detect_backend(service) is expected
    assert requests == [("", "base")]
