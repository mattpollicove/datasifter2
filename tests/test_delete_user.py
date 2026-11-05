import os

from datasifter.directory.service import LDAPService
from datasifter.ui.directory_lens_tab import DirectoryLensTab


class FakeProfile:
    def __init__(self, base_dn: str, read_only: bool = False):
        self.base_dn = base_dn
        self.read_only = read_only


class FakeService(LDAPService):
    def __init__(self):
        # Use a minimal profile
        self.profile = FakeProfile(base_dn="dc=example,dc=com")
        self.deleted = []

    def search(self, base_dn, filter, attributes=None, scope="subtree"):
        return []

    def delete_entry(self, dn: str, recursive: bool = False, cancelled=None):
        # Record the call for verification
        self.deleted.append((dn, recursive))
        return True


def test_delete_user_calls_service(qtbot, monkeypatch):
    """Verify that a delete command correctly invokes the LDAP service.

    The test creates a :class:`DirectoryLensTab`, injects a fake service that
    records delete calls, and then executes a natural‑language delete command.
    It asserts that the service was called with the expected DN and that the
    UI logged a success message.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tab = DirectoryLensTab()
    qtbot.addWidget(tab)
    fake_service = FakeService()
    tab.set_service(fake_service)

    # Ensure the command button is enabled
    assert tab.cmd_execute.isEnabled()

    # Bypass the confirmation dialog
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *_, **__: QMessageBox.StandardButton.Yes)

    # Execute the delete command
    tab.cmd_input.setText("delete entry dn=cn=mattp")
    tab._execute_command()
    # Wait for the worker thread to finish
    qtbot.waitUntil(lambda: len(fake_service.deleted) > 0, timeout=2000)
    qtbot.waitUntil(lambda: "Success" in tab.log.toPlainText(), timeout=2000)

    # The fake service should have recorded one delete call
    assert fake_service.deleted == [("cn=mattp", False)]
    # The log should contain a success message
    assert "Success" in tab.log.toPlainText()