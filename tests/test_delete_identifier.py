import os

from datasifter.directory.service import LDAPService
from datasifter.ui.directory_lens_tab import DirectoryLensTab


class FakeProfile:
    def __init__(self, base_dn: str, read_only: bool = False):
        self.base_dn = base_dn
        self.read_only = read_only


class FakeService(LDAPService):
    def __init__(self):
        self.profile = FakeProfile(base_dn="dc=example,dc=com")
        self.deleted = []

    def search(self, base_dn: str, filter: str, attributes=None, scope="subtree"):
        # Return a single match for identifier "mattp"
        if "mattp" in filter:
            return [{"dn": "cn=mattp,ou=people,dc=example,dc=com"}]
        return []

    def delete_entry(self, dn: str, recursive: bool = False, cancelled=None):
        self.deleted.append((dn, recursive))
        return True


def test_delete_user_by_identifier(qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tab = DirectoryLensTab()
    qtbot.addWidget(tab)
    fake_service = FakeService()
    tab.set_service(fake_service)
    tab.cmd_input.setText("delete entry mattp")
    tab._execute_command()
    assert fake_service.deleted == [("cn=mattp,ou=people,dc=example,dc=com", False)]