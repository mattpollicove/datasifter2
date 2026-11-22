"""Live tests against the non-production LDAP target named in ``live_ldap.local.json``.

They are skipped when that git-ignored file is absent. Every write stays inside a
uniquely named organizational unit that is removed when the module finishes. The
target settings and service factory are shared fixtures in ``conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest

from datasifter.config import ConfigurationStore, DirectoryProfile, LocalConfiguration
from datasifter.directory.service import LDAPOperationError, LDAPService
from datasifter.ui.main_window import MainWindow

pytestmark = pytest.mark.live


@pytest.fixture
def live_service(live_settings, build_live_service):
    return lambda transport="plain": build_live_service(live_settings, transport)


@pytest.fixture(scope="module")
def sandbox(live_settings):
    profile = DirectoryProfile(
        name="live",
        host=live_settings["host"],
        port=live_settings["ldap_port"],
        authentication_mode="simple",
        bind_dn=live_settings["bind_dn"],
        base_dn=live_settings["base_dn"],
    )
    service = LDAPService(profile, live_settings["password"])
    name = f"datasifter-live-{uuid.uuid4().hex[:8]}"
    distinguished_name = f"ou={name},{live_settings['base_dn']}"
    service.add_entry(
        distinguished_name, {"objectClass": ["top", "organizationalUnit"], "ou": [name]}
    )
    try:
        yield distinguished_name
    finally:
        service.delete_entry(distinguished_name, recursive=True)


def _person(common_name: str) -> dict[str, list[str]]:
    return {
        "objectClass": ["top", "person", "organizationalPerson", "inetOrgPerson"],
        "cn": [common_name],
        "sn": ["Live"],
    }


@pytest.mark.parametrize("transport", ["plain", "starttls", "ldaps"])
def test_bind_and_base_dn_discovery(live_service, live_settings, transport):
    service = live_service(transport)

    assert "succeeded" in service.test_authentication()
    contexts = [context.casefold() for context in service.discover_base_dns()]
    assert live_settings["base_dn"].casefold() in contexts


def test_paged_search_returns_continuation_cookie(live_service, sandbox):
    service = live_service()
    for index in range(3):
        service.add_entry(f"cn=page-{index},{sandbox}", _person(f"page-{index}"))

    first, cookie = service.search_page(
        sandbox, "(cn=page-*)", attributes=["cn"], scope="level", page_size=2
    )
    second, last_cookie = service.search_page(
        sandbox, "(cn=page-*)", attributes=["cn"], scope="level", page_size=2, page_cookie=cookie
    )

    assert len(first) == 2
    assert cookie
    assert last_cookie is None
    names = sorted(entry["attributes"]["cn"][0] for entry in first + second)
    assert names == ["page-0", "page-1", "page-2"]


def test_entry_lifecycle_add_modify_rename_delete(live_service, sandbox):
    service = live_service()
    original = f"cn=lifecycle,{sandbox}"
    renamed = f"cn=lifecycle-renamed,{sandbox}"

    service.add_entry(original, _person("lifecycle"))
    assert service.read_entry(original)["attributes"]["sn"] == ["Live"]

    service.modify_entry(original, {"description": [("add", ["first"])]})
    service.modify_entry(original, {"description": [("replace", ["second"])]})
    assert service.read_entry(original)["attributes"]["description"] == ["second"]

    service.move_entry(original, "cn=lifecycle-renamed")
    assert service.read_entry(renamed)["attributes"]["cn"] == ["lifecycle-renamed"]
    with pytest.raises(LDAPOperationError):
        service.read_entry(original)

    service.delete_entry(renamed)
    with pytest.raises(LDAPOperationError):
        service.read_entry(renamed)


def test_data_lens_session_commands_connect_and_disconnect(
    qtbot, tmp_path, monkeypatch, live_settings
):
    class Vault:
        def get_password(self, _profile):
            return live_settings["password"]

        def get_client_key_passphrase(self, _profile):
            return None

    profile = DirectoryProfile(
        name="live",
        host=live_settings["host"],
        port=live_settings["ldap_port"],
        authentication_mode="simple",
        bind_dn=live_settings["bind_dn"],
        base_dn=live_settings["base_dn"],
        read_only=True,
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))
    window = MainWindow(store=store, vault=Vault())
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    def run(request: str) -> str:
        window.ai_request.setPlainText(request)
        assert window.ai_generate.isEnabled()
        window.ai_generate.click()
        return window.ai_output.toPlainText()

    assert run("connect to live").startswith("Connecting to live")
    qtbot.waitUntil(lambda: bool(errors) or not window._workers, timeout=30000)

    assert errors == []
    assert not window._connecting
    assert window.tree.topLevelItemCount() > 0
    assert run("current target").startswith("Connected to live")
    assert store.load().last_connected_profile_id == profile.profile_id

    assert run("disconnect") == "Disconnected from live."
    assert window.tree.topLevelItemCount() == 0
    window.close()
