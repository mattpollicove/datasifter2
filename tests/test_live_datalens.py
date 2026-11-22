"""Live tests of the Data Lens adapters against both non-production directories.

``live_ldap.local.json`` names the standards-based directory and
``live_ad.local.json`` the Active Directory domain; a backend's tests are skipped
when its file is absent. Every write stays inside a uniquely named organizational
unit that is removed when the module finishes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from datasifter.config import DirectoryProfile
from datasifter.datalens import (
    AccountState,
    AttributeChange,
    AttributeResolution,
    ClarificationPrompt,
    ClarificationReason,
    DirectoryAdapter,
    DirectoryBackend,
    DirectoryResolver,
    EntryKind,
    IssueCode,
    IssueSeverity,
    MutationRejectedError,
    SearchSpec,
    create_adapter,
    detect_backend,
)
from datasifter.datalens.service import DataLensMutationService, LocalAuditLog
from datasifter.directory.service import LDAPOperationError, LDAPResultError, LDAPService

pytestmark = pytest.mark.live

STANDARD = DirectoryBackend.STANDARD_LDAP
AD = DirectoryBackend.ACTIVE_DIRECTORY


def _sandbox(settings: dict[str, Any]) -> Iterator[str]:
    """Create a disposable organizational unit and remove it, with its contents, afterwards."""
    profile = DirectoryProfile(
        name="live",
        host=settings["host"],
        port=settings["ldap_port"],
        authentication_mode="simple",
        bind_dn=settings["bind_dn"],
        base_dn=settings["base_dn"],
    )
    service = LDAPService(profile, settings["password"])
    name = f"datasifter-live-{uuid.uuid4().hex[:8]}"
    distinguished_name = f"ou={name},{settings['base_dn']}"
    service.add_entry(
        distinguished_name, {"objectClass": ["top", "organizationalUnit"], "ou": [name]}
    )
    try:
        yield distinguished_name
    finally:
        service.delete_entry(distinguished_name, recursive=True)


@pytest.fixture(scope="module")
def standard_sandbox(live_settings) -> Iterator[str]:
    yield from _sandbox(live_settings)


@pytest.fixture(scope="module")
def ad_sandbox(live_ad_settings) -> Iterator[str]:
    yield from _sandbox(live_ad_settings)


@dataclass
class LiveTarget:
    backend: DirectoryBackend
    settings: dict[str, Any]
    sandbox: str
    build_service: Callable[..., LDAPService]

    def adapter(self, transport: str = "plain") -> DirectoryAdapter:
        return create_adapter(self.build_service(self.settings, transport), self.backend)

    def child(self, name: str, parent: str | None = None) -> str:
        return f"cn={name},{parent or self.sandbox}"

    def person(self) -> dict[str, list[str]]:
        """Attributes of a valid person on this backend, without the naming value."""
        if self.backend is AD:
            # The account name must be unique in the whole domain.
            return {"objectClass": ["user"], "sAMAccountName": [f"dsl{uuid.uuid4().hex[:12]}"]}
        return {"objectClass": ["inetOrgPerson"], "sn": ["Live"]}

    def details(self) -> dict[str, str]:
        """What a new user needs beyond a name, in everyday terms."""
        if self.backend is AD:
            return {"sam account name": f"dsl{uuid.uuid4().hex[:12]}"}
        return {"last name": "Live"}

    def new_user(self, resolver: DirectoryResolver, name: str, container: str | None = None,
                 **attributes: str) -> str:
        """Create a user through the resolver and return its DN."""
        prepared = resolver.prepare_create(
            EntryKind.USER, name, container=container or self.sandbox,
            attributes={**self.details(), **attributes},
        )
        assert prepared.ready, prepared
        assert prepared.plan is not None
        resolver.adapter.execute(prepared.plan, dry_run=False, confirmed=True)
        return prepared.plan.dn


@pytest.fixture(params=[STANDARD, AD], ids=lambda backend: backend.value)
def target(request, build_live_service) -> LiveTarget:
    settings_fixture, sandbox_fixture = {
        STANDARD: ("live_settings", "standard_sandbox"),
        AD: ("live_ad_settings", "ad_sandbox"),
    }[request.param]
    return LiveTarget(
        backend=request.param,
        settings=request.getfixturevalue(settings_fixture),
        sandbox=request.getfixturevalue(sandbox_fixture),
        build_service=build_live_service,
    )


@pytest.fixture
def ad_target(live_ad_settings, ad_sandbox, build_live_service) -> LiveTarget:
    return LiveTarget(AD, live_ad_settings, ad_sandbox, build_live_service)


def errors(result) -> set[IssueCode]:
    return {i.code for i in result.preview.issues if i.severity is IssueSeverity.ERROR}


def warnings(result) -> set[IssueCode]:
    return {i.code for i in result.preview.issues if i.severity is IssueSeverity.WARNING}


def test_backend_is_detected_from_the_root_dse(target):
    assert detect_backend(target.build_service(target.settings)) is target.backend


def test_people_count_uses_live_backend_filter_and_starting_location(target):
    service = target.build_service(target.settings)
    adapter = target.adapter()
    before = service.count_entries(target.sandbox, adapter.user_filter())
    name = f"count-{uuid.uuid4().hex[:8]}"
    dn = target.child(name)
    service.add_entry(dn, {**target.person(), "cn": [name]})
    try:
        assert service.count_entries(target.sandbox, adapter.user_filter()) == before + 1
    finally:
        service.delete_entry(dn)


def test_monitor_read_when_available_on_live_ldap(live_settings, build_live_service):
    service = build_live_service(live_settings)
    try:
        entries = service.search(
            "cn=monitor", "(objectClass=*)", attributes=["*", "+"], max_entries=201
        )
    except LDAPResultError as error:
        if error.result_code in (32, 50):
            pytest.skip("The live LDAP target does not expose cn=monitor to this account.")
        raise
    assert entries, "cn=monitor returned no visible entries"


def test_entry_lifecycle_through_typed_operations(target):
    adapter = target.adapter()
    original = target.child("lens-lifecycle")
    destination = f"ou=lens-destination,{target.sandbox}"
    moved = target.child("lens-renamed", destination)

    # A dry run reports what would happen and writes nothing.
    preview = adapter.create_entry(original, target.person())
    assert preview.preview.can_apply and not preview.applied
    assert adapter.get_entry(original) is None

    adapter.create_entry(original, target.person(), dry_run=False, confirmed=True)
    assert adapter.get_entry(original).first("cn") == "lens-lifecycle"
    assert errors(adapter.create_entry(original, target.person())) == {IssueCode.ENTRY_EXISTS}

    adapter.update_entry(
        original,
        [
            AttributeChange(attribute="description", operation="add", values=("first",)),
            AttributeChange(attribute="displayName", operation="replace", values=("Lens",)),
        ],
        dry_run=False,
        confirmed=True,
    )
    adapter.update_entry(
        original,
        [AttributeChange(attribute="description", operation="replace", values=("second",))],
        dry_run=False,
        confirmed=True,
    )
    updated = adapter.get_entry(original)
    assert updated.get("description") == ("second",) and updated.first("displayName") == "Lens"

    adapter.create_entry(
        destination, {"objectClass": ["organizationalUnit"]}, dry_run=False, confirmed=True
    )
    result = adapter.move_entry(
        original,
        new_rdn="cn=lens-renamed",
        new_superior=destination,
        dry_run=False,
        confirmed=True,
    )
    assert result.preview.resulting_dn == moved
    assert adapter.get_entry(original) is None
    assert adapter.get_entry(moved).first("cn") == "lens-renamed"

    # The unit now has a child: refused here, exactly as the server would refuse it.
    with pytest.raises(MutationRejectedError):
        adapter.delete_entry(destination, dry_run=False, confirmed=True)
    with pytest.raises(LDAPOperationError):
        adapter._service.delete_entry(destination)
    assert adapter.get_entry(moved) is not None

    removal = adapter.delete_entry(destination, recursive=True, dry_run=False, confirmed=True)
    assert [dn.casefold() for dn in removal.preview.affected_dns] == [
        moved.casefold(),
        destination.casefold(),
    ]
    assert adapter.get_entry(destination) is None and adapter.get_entry(moved) is None


def test_previews_agree_with_the_real_schema(target):
    adapter = target.adapter()
    missing_parent = adapter.create_entry(
        target.child("lens-orphan", f"ou=missing,{target.sandbox}"), target.person()
    )
    assert errors(missing_parent) == {IssueCode.PARENT_NOT_FOUND}
    unknown = adapter.create_entry(
        target.child("lens-unknown"), {**target.person(), "noSuchAttribute": ["x"]}
    )
    assert errors(unknown) == {IssueCode.UNKNOWN_ATTRIBUTE}

    bare = target.child("lens-bare")
    if target.backend is AD:
        # The domain controller fills in what its schema calls required, so an
        # entry with only an object class is accepted, by the preview and by AD.
        result = adapter.create_entry(bare, {"objectClass": ["user"]})
        assert result.preview.can_apply
        adapter.create_entry(bare, {"objectClass": ["user"]}, dry_run=False, confirmed=True)
        assert adapter.get_entry(bare) is not None
    else:
        result = adapter.create_entry(bare, {"objectClass": ["inetOrgPerson"]})
        assert errors(result) == {IssueCode.MISSING_REQUIRED_ATTRIBUTE}
        with pytest.raises(LDAPOperationError):
            adapter._service.add_entry(bare, {"objectClass": ["inetOrgPerson"], "cn": ["x"]})


def test_search_stops_at_the_size_limit(target):
    adapter = target.adapter()
    for index in range(3):
        adapter.create_entry(
            target.child(f"lens-page-{index}"), target.person(), dry_run=False, confirmed=True
        )
    spec = SearchSpec(
        base_dn=target.sandbox, scope="level", filter="(cn=lens-page-*)", attributes=["cn"]
    )

    limited = adapter.search_entries(spec.model_copy(update={"size_limit": 2, "page_size": 1}))
    assert len(limited.entries) == 2 and limited.truncated
    everything = adapter.search_entries(spec.model_copy(update={"size_limit": 3}))
    assert not everything.truncated
    assert sorted(entry.first("cn") for entry in everything.entries) == [
        "lens-page-0",
        "lens-page-1",
        "lens-page-2",
    ]
    found = adapter.search_entries(
        SearchSpec(base_dn=target.sandbox, filter=adapter.user_filter("lens-page-1"))
    )
    assert [entry.first("cn") for entry in found.entries] == ["lens-page-1"]


def test_ad_account_without_password_is_created_disabled(ad_target):
    adapter = ad_target.adapter()
    dn = ad_target.child("lens-disabled")

    preview = adapter.create_entry(dn, ad_target.person())
    assert warnings(preview) == {IssueCode.ACCOUNT_STATE}
    adapter.create_entry(dn, ad_target.person(), dry_run=False, confirmed=True)
    entry = adapter.get_entry(dn, adapter.account_status_attributes)
    status = adapter.account_status(entry)
    assert status.enabled is False and "ACCOUNTDISABLE" in status.flags

    # Rules the preview enforces because the domain controller does.
    enabled = adapter.create_entry(
        ad_target.child("lens-enabled"), {**ad_target.person(), "userAccountControl": 512}
    )
    assert errors(enabled) == {IssueCode.ACCOUNT_STATE}
    plain_password = adapter.create_entry(
        ad_target.child("lens-plain"), {**ad_target.person(), "unicodePwd": "ignored"}
    )
    assert errors(plain_password) == {IssueCode.INSECURE_TRANSPORT}
    assert errors(adapter.move_entry(dn, new_rdn="cn=lens-kept", delete_old_rdn=False)) == {
        IssueCode.UNSUPPORTED
    }


def test_ad_preview_protects_domain_objects(live_ad_settings, build_live_service):
    # Previews only, on a read-only profile, so nothing here can change the domain.
    service = build_live_service(live_ad_settings, read_only=True)
    adapter = create_adapter(service, AD)
    base_dn = live_ad_settings["base_dn"]

    users = adapter.delete_entry(f"CN=Users,{base_dn}", recursive=True)
    assert IssueCode.PROTECTED_ENTRY in errors(users)
    assert any("Critical system objects" in issue.message for issue in users.preview.issues)
    assert IssueCode.PROTECTED_ENTRY in errors(
        adapter.move_entry(f"OU=Domain Controllers,{base_dn}", new_rdn="OU=Renamed")
    )
    assert IssueCode.SYSTEM_ATTRIBUTE in errors(
        adapter.update_entry(
            live_ad_settings["bind_dn"],
            [AttributeChange(attribute="memberOf", operation="delete")],
        )
    )


def test_ad_password_over_ldaps_creates_an_enabled_account(ad_target):
    if not ad_target.settings.get("ldaps_port"):
        pytest.skip("live_ad.local.json does not configure an LDAPS port.")
    adapter = ad_target.adapter("ldaps")
    dn = ad_target.child("lens-enabled-tls")
    # Generated for this run only; it meets the default complexity rules.
    password = f"Aa1!{uuid.uuid4().hex}"

    result = adapter.create_entry(
        dn,
        {**ad_target.person(), "userAccountControl": 512, "unicodePwd": password},
        dry_run=False,
        confirmed=True,
    )
    assert password not in result.model_dump_json()
    entry = adapter.get_entry(dn, adapter.account_status_attributes)
    assert adapter.account_status(entry).enabled is True

    adapter.update_entry(
        dn,
        [adapter.account_control_change(entry, disabled=True)],
        dry_run=False,
        confirmed=True,
    )
    disabled = adapter.get_entry(dn, adapter.account_status_attributes)
    assert adapter.account_status(disabled).enabled is False


def test_confirmed_service_deletes_only_after_review(target, tmp_path):
    transport = "ldaps" if target.backend is AD and target.settings.get("ldaps_port") else "plain"
    adapter = target.adapter(transport)
    resolver = DirectoryResolver(adapter)
    name = f"lens-confirmed-{uuid.uuid4().hex[:8]}"
    dn = target.new_user(resolver, name)
    prepared = resolver.prepare_delete(dn, kind=EntryKind.USER)
    assert prepared.ready
    assert adapter.get_entry(dn) is not None

    result = DataLensMutationService(adapter, LocalAuditLog(tmp_path / "audit.jsonl")).apply(
        prepared, confirmed=True
    )
    assert result.applied
    assert adapter.get_entry(dn) is None


# ----------------------------------------------------------------------
# Phase 2: everyday terms and names
# ----------------------------------------------------------------------
def test_terms_resolve_against_the_real_schema(target):
    resolver = DirectoryResolver(target.adapter())

    def attributes(term, **options):
        resolution = resolver.resolve_attribute(term, **options)
        assert isinstance(resolution, AttributeResolution), resolution
        return resolution.attributes

    assert attributes("E-mail address") == ("mail",)
    assert attributes("phone") == ("telephoneNumber", "mobile", "homePhone")
    assert attributes("job title", for_write=True) == ("title",)
    assert resolver.resolve_attribute("reports to").references_entry
    ambiguous_on_write = "username" if target.backend is AD else "department"
    choice = resolver.resolve_attribute(ambiguous_on_write, for_write=True)
    assert isinstance(choice, ClarificationPrompt)
    assert choice.reason is ClarificationReason.AMBIGUOUS_ATTRIBUTE
    if target.backend is AD:
        assert attributes("username") == ("sAMAccountName", "userPrincipalName")
        assert attributes("department", for_write=True) == ("department",)
        assert attributes("company") == ("company",)
        assert attributes("member of") == ("memberOf",)
    else:
        assert attributes("username", for_write=True) == ("uid",)
        assert attributes("department") == ("departmentNumber", "ou")
        assert attributes("company") == ("o",)
    unknown = resolver.resolve_attribute("favourite colour")
    assert isinstance(unknown, ClarificationPrompt)
    assert unknown.reason is ClarificationReason.UNKNOWN_TERM


def test_requests_by_name_are_resolved_planned_and_applied(target):
    adapter = target.adapter()
    resolver = DirectoryResolver(adapter)
    tag = uuid.uuid4().hex[:6]
    ada, grace = f"lens-ada-{tag}", f"lens-grace-{tag}"

    # A user needs more than a name; the engine says what, rather than inventing it.
    incomplete = resolver.prepare_create(EntryKind.USER, ada, container=target.sandbox)
    assert [prompt.field for prompt in incomplete.clarifications] == [
        "sAMAccountName" if target.backend is AD else "sn"
    ]
    ada_dn = target.new_user(resolver, ada, email=f"{ada}@lens.example")
    grace_dn = target.new_user(resolver, grace)

    found = resolver.resolve_entry(f"{ada}@lens.example", kind=EntryKind.USER)
    assert found.dn.casefold() == ada_dn.casefold()
    near = resolver.resolve_entry(f"ens-ada-{tag}", kind=EntryKind.USER)
    assert near.reason is ClarificationReason.NOT_FOUND
    assert [option.value.casefold() for option in near.options] == [ada_dn.casefold()]

    update = resolver.prepare_update(
        grace,
        [{"term": "reports to", "values": ada}, {"term": "job title", "values": "Engineer"}],
        kind=EntryKind.USER,
    )
    assert update.ready
    adapter.execute(update.plan, dry_run=False, confirmed=True)

    search = resolver.prepare_search(
        [{"term": "manager", "value": ada}, {"term": "title", "operator": "starts_with",
                                             "value": "Eng"}],
        kind=EntryKind.USER,
        container=target.sandbox,
        attributes=["job title", "manager"],
    )
    assert search.ready
    (report,) = adapter.search_entries(search.spec).entries
    assert report.dn.casefold() == grace_dn.casefold()
    assert report.first("title") == "Engineer"
    assert str(report.first("manager")).casefold() == ada_dn.casefold()

    # A second entry with the same name makes the name ambiguous.
    unit = resolver.prepare_create(EntryKind.CONTAINER, f"lens-unit-{tag}",
                                   container=target.sandbox)
    assert unit.ready
    adapter.execute(unit.plan, dry_run=False, confirmed=True)
    twin_dn = target.new_user(resolver, ada, container=unit.plan.dn)
    ambiguous = resolver.resolve_entry(ada, kind=EntryKind.USER)
    assert ambiguous.reason is ClarificationReason.AMBIGUOUS_TARGET
    assert {option.value.casefold() for option in ambiguous.options} == {
        ada_dn.casefold(),
        twin_dn.casefold(),
    }
    blocked = resolver.prepare_delete(ada, kind=EntryKind.USER)
    assert blocked.plan is None and not blocked.ready


def test_account_state_by_name(target):
    adapter = target.adapter()
    resolver = DirectoryResolver(adapter)
    name = f"lens-state-{uuid.uuid4().hex[:6]}"
    dn = target.new_user(resolver, name).casefold()

    def disabled_users() -> list[str]:
        prepared = resolver.prepare_search(
            [{"term": "disabled", "operator": "present"}],
            kind=EntryKind.USER,
            container=target.sandbox,
        )
        assert prepared.ready, prepared
        return [entry.dn.casefold() for entry in adapter.search_entries(prepared.spec).entries]

    def enabled() -> bool | None:
        entry = adapter.get_entry(
            dn, adapter.known_attributes(adapter.account_status_attributes)
        )
        return adapter.account_status(entry).enabled

    if target.backend is AD:
        # Created without a password, so the domain controller disabled it.
        assert dn in disabled_users() and enabled() is False
        again = resolver.prepare_account_state(name, AccountState.DISABLED)
        assert again.ready and warnings(again) == {IssueCode.NO_CHANGE}
        return

    assert dn not in disabled_users()
    disable = resolver.prepare_account_state(name, AccountState.DISABLED)
    assert disable.ready
    adapter.execute(disable.plan, dry_run=False, confirmed=True)
    assert dn in disabled_users() and enabled() is False

    enable = resolver.prepare_account_state(name, AccountState.ENABLED)
    assert enable.ready
    adapter.execute(enable.plan, dry_run=False, confirmed=True)
    assert dn not in disabled_users()


def test_group_members_by_name(target):
    adapter = target.adapter()
    resolver = DirectoryResolver(adapter)
    tag = uuid.uuid4().hex[:6]
    first = target.new_user(resolver, f"lens-member-a-{tag}")
    second = target.new_user(resolver, f"lens-member-b-{tag}")

    group = resolver.prepare_create(
        EntryKind.GROUP, f"lens-group-{tag}", container=target.sandbox,
        attributes={"members": [f"lens-member-a-{tag}"]},
    )
    assert group.ready, group
    adapter.execute(group.plan, dry_run=False, confirmed=True)

    add = resolver.prepare_update(
        f"lens-group-{tag}",
        [{"term": "members", "operation": "add", "values": [f"lens-member-b-{tag}"]}],
        kind=EntryKind.GROUP,
    )
    assert add.ready, add
    adapter.execute(add.plan, dry_run=False, confirmed=True)
    stored = adapter.get_entry(group.plan.dn, ["member"])
    assert sorted(str(value).casefold() for value in stored.get("member")) == sorted(
        [first.casefold(), second.casefold()]
    )
