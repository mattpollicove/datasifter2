"""An in-memory directory for Data Lens tests, built on ldap3's mock strategy.

Every connection that ``LDAPService`` opens is replaced by an ldap3 ``MOCK_SYNC``
connection over one shared set of entries, loaded with ldap3's offline OpenLDAP or
Active Directory schema. The service and adapter code under test is unchanged.

The mock does not enforce directory rules such as "the parent must exist" or "an
entry with children cannot be deleted", so tests built on it show that the engine
catches them before anything is sent.
"""

from __future__ import annotations

from typing import ClassVar

from ldap3 import BASE, MOCK_SYNC, OFFLINE_AD_2012_R2, OFFLINE_SLAPD_2_4, Connection, Server

from datasifter.config import DirectoryProfile
from datasifter.datalens import (
    AttributeChange,
    DirectoryBackend,
    IssueCode,
    IssueSeverity,
    create_adapter,
)
from datasifter.directory.service import LDAPService
from datasifter.security.certificates import CertificateTrustStore

BASE_DN = "dc=test"
BIND_DN = "cn=admin,dc=test"
BIND_PASSWORD = "bind-secret"
ADA = "cn=Ada,ou=People,dc=test"
STANDARD = DirectoryBackend.STANDARD_LDAP
AD = DirectoryBackend.ACTIVE_DIRECTORY
SCHEMAS = {STANDARD: OFFLINE_SLAPD_2_4, AD: OFFLINE_AD_2012_R2}


class MockDirectory:
    """One in-memory directory shared by every connection the service opens.

    A test may use a directory of each backend at once: the patched connection
    factory picks the directory from the host name of the profile.
    """

    _by_host: ClassVar[dict[str, MockDirectory]] = {}

    def __init__(self, backend: DirectoryBackend, monkeypatch, tmp_path) -> None:
        self.backend = backend
        self.host = f"mock-{backend.value.replace('_', '-')}"
        self.connections = 0
        self._tmp_path = tmp_path
        self._server = Server("mock", get_info=SCHEMAS[backend])
        self._seed = Connection(
            self._server, user=BIND_DN, password=BIND_PASSWORD, client_strategy=MOCK_SYNC
        )
        self.add(BASE_DN, {"objectClass": ["domain"], "dc": "test"})
        self.add(BIND_DN, {"objectClass": ["person"], "cn": "admin", "sn": "admin",
                           "userPassword": BIND_PASSWORD})
        self.add("ou=People,dc=test", {"objectClass": ["organizationalUnit"], "ou": "People"})
        self.add("ou=Staff,dc=test", {"objectClass": ["organizationalUnit"], "ou": "Staff"})
        self.add("ou=Team,ou=People,dc=test", {"objectClass": ["organizationalUnit"], "ou": "Team"})
        self.add("cn=Deep,ou=Team,ou=People,dc=test", self.person("Deep"))
        self.add(ADA, {**self.person("Ada"), "mail": ["ada@example.com"]})
        self._seed.bind()
        monkeypatch.setitem(MockDirectory._by_host, self.host, self)
        monkeypatch.setattr("datasifter.directory.service.Connection", MockDirectory._dispatch)

    @staticmethod
    def _dispatch(server, **options):
        return MockDirectory._by_host[server.host]._connect(server, **options)

    def _connect(self, _server, **options):
        self.connections += 1
        connection = Connection(
            self._server,
            user=options.get("user"),
            password=options.get("password"),
            client_strategy=MOCK_SYNC,
            raise_exceptions=options.get("raise_exceptions", False),
        )
        connection.strategy.entries = self._seed.strategy.entries
        return connection

    def person(self, name: str) -> dict[str, list]:
        """Attributes of a valid person entry on this backend, without the naming value."""
        if self.backend is AD:
            return {"objectClass": ["user"], "sAMAccountName": [name.lower()]}
        return {"objectClass": ["inetOrgPerson"], "sn": [name]}

    def add(self, dn: str, attributes: dict) -> None:
        naming = dn.split(",", 1)[0].split("=", 1)
        stored = {naming[0]: naming[1], **attributes}
        if self.backend is AD and "user" in stored["objectClass"]:
            # A domain controller sets this itself; the mock has to be told.
            stored.setdefault("objectCategory", "person")
        self._seed.strategy.add_entry(dn, stored)

    def read(self, dn: str) -> dict | None:
        """Return the stored attributes of an entry, or None when it does not exist."""
        if not self._seed.search(dn, "(objectClass=*)", search_scope=BASE, attributes=["*"]):
            return None
        return self._seed.response[0]["raw_attributes"]

    def adapter(self, *, profile_read_only: bool = False, transport: str = "plain", **options):
        profile = DirectoryProfile(
            name="mock",
            host=self.host,
            port=636 if transport == "ldaps" else 389,
            transport=transport,
            authentication_mode="simple",
            bind_dn=BIND_DN,
            base_dn=BASE_DN,
            read_only=profile_read_only,
        )
        service = LDAPService(
            profile, BIND_PASSWORD, trust_store=CertificateTrustStore(self._tmp_path / "trust")
        )
        return create_adapter(service, self.backend, **options)


def errors(result) -> set[IssueCode]:
    return {i.code for i in result.preview.issues if i.severity is IssueSeverity.ERROR}


def warnings(result) -> set[IssueCode]:
    return {i.code for i in result.preview.issues if i.severity is IssueSeverity.WARNING}


def change(attribute: str, operation: str, *values) -> AttributeChange:
    return AttributeChange(attribute=attribute, operation=operation, values=values)
