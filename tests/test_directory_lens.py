"""Unit tests for :class:`datasifter.ui.directory_lens.DirectoryLens`.

The tests use a lightweight mock of :class:`datasifter.directory.service.LDAPService`
to verify that CRUD operations correctly start a :class:`DirectoryWorker` and
emit the expected signals.
"""

import pytest
from PyQt6.QtCore import QSignalSpy
from datasifter.ui.directory_lens import DirectoryLens
from datasifter.directory.service import LDAPService
from datasifter.directory.worker import DirectoryWorker
from datasifter.config import DirectoryProfile


class MockLDAPService(LDAPService):
    """A minimal mock that records calls and returns deterministic results."""

    def __init__(self, profile, password=None, client_key_passphrase=None, trust_store=None):
        super().__init__(profile, password, client_key_passphrase, trust_store)
        self.calls = []

    def add_entry(self, dn, attributes):
        self.calls.append(("add_entry", dn, attributes))
        return {"dn": dn, "attributes": attributes}

    def modify_entry(self, dn, changes):
        self.calls.append(("modify_entry", dn, changes))
        return {"dn": dn, "changes": changes}

    def delete_entry(self, dn, recursive=False):
        self.calls.append(("delete_entry", dn, recursive))
        return {"dn": dn, "recursive": recursive}

    def move_entry(self, dn, relative_dn, new_superior=None):
        self.calls.append(("move_entry", dn, relative_dn, new_superior))
        return {"dn": dn, "relative_dn": relative_dn, "new_superior": new_superior}

    def search_subtree(self, base_dn: str, filter: str, attributes: list[str] | None = None, recursive: bool = True):
        # Return an empty list to avoid network calls during tests.
        return []


def test_nlp_create_command(lens):
    from datasifter.ui.directory_lens_tab import DirectoryLensTab
    tab = DirectoryLensTab(lens.service)
    tab.cmd_input.setText('create entry cn=Test,dc=example,dc=com with attributes {"objectClass":["inetOrgPerson"],"cn":["Test"]}')
    tab._execute_command()
    assert lens.service.calls[-1][0] == "add_entry"


def test_nlp_update_command(lens):
    from datasifter.ui.directory_lens_tab import DirectoryLensTab
    tab = DirectoryLensTab(lens.service)
    tab.cmd_input.setText('update entry cn=Test,dc=example,dc=com with changes {"sn":[["replace","Updated"]]}')
    tab._execute_command()
    assert lens.service.calls[-1][0] == "modify_entry"


def test_nlp_delete_command(lens):
    from datasifter.ui.directory_lens_tab import DirectoryLensTab
    tab = DirectoryLensTab(lens.service)
    tab.cmd_input.setText('delete entry cn=Test,dc=example,dc=com')
    tab._execute_command()
    assert lens.service.calls[-1][0] == "delete_entry"


def test_nlp_move_command(lens):
    from datasifter.ui.directory_lens_tab import DirectoryLensTab
    tab = DirectoryLensTab(lens.service)
    tab.cmd_input.setText('move entry cn=Test,dc=example,dc=com to cn=NewName under ou=Users,dc=example,dc=com')
    tab._execute_command()
    assert lens.service.calls[-1][0] == "move_entry"


@pytest.fixture
def profile():
    return DirectoryProfile(
        name="test", host="ldap.test", port=389, transport="plain",
        authentication_mode="anonymous", bind_dn="", base_dn="dc=example,dc=com",
        ca_certificate="", client_certificate="", client_key="", read_only=False,
        profile_id="test"
    )


@pytest.fixture
def lens(profile):
    service = MockLDAPService(profile)
    return DirectoryLens(service, root_dn="dc=example,dc=com")


def test_create_entry_triggers_worker_and_succeeds(lens):
    spy = QSignalSpy(lens.succeeded)
    lens.create_entry("cn=NewUser,dc=example,dc=com", {"objectClass": ["inetOrgPerson"]})
    # Wait for the worker to finish
    assert spy.wait(1000)
    assert spy[0][0] == ("add", "cn=NewUser,dc=example,dc=com")


def test_update_entry_triggers_worker_and_succeeds(lens):
    spy = QSignalSpy(lens.succeeded)
    lens.update_entry("cn=Existing,dc=example,dc=com", {"sn": [("replace", ["Updated"])]})
    assert spy.wait(1000)
    assert spy[0][0] == ("modify", "cn=Existing,dc=example,dc=com")


def test_delete_entry_triggers_worker_and_succeeds(lens):
    spy = QSignalSpy(lens.succeeded)
    lens.delete_entry("cn=ToDelete,dc=example,dc=com", recursive=True)
    assert spy.wait(1000)
    assert spy[0][0] == ("delete", "cn=ToDelete,dc=example,dc=com")


def test_move_entry_triggers_worker_and_succeeds(lens):
    spy = QSignalSpy(lens.succeeded)
    lens.move_entry("cn=MoveMe,dc=example,dc=com", "cn=NewName", new_superior="ou=Users,dc=example,dc=com")
    assert spy.wait(1000)
    assert spy[0][0] == ("move", "cn=MoveMe,dc=example,dc=com")
