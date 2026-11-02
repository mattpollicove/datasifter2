import socket
from contextlib import contextmanager
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from datasifter.config import DirectoryProfile
from datasifter.directory.service import (
    MODIFY_REPLACE,
    LDAPOperationError,
    LDAPService,
    _dn_depth,
    _log_protocol_request,
    _redact_filter_values,
)


def test_dn_depth_ignores_escaped_commas():
    assert _dn_depth(r"cn=Last\, First,ou=People,dc=example") == 3


def test_read_only_profile_blocks_mutations_before_connecting(monkeypatch):
    profile = DirectoryProfile(name="readonly", host="ldap.test", read_only=True)
    service = LDAPService(profile, None)

    @contextmanager
    def unexpected_connection():
        raise AssertionError("read-only guard should run before connecting")
        yield

    monkeypatch.setattr(service, "_connection", unexpected_connection)
    mutations = [
        lambda: service.add_entry("cn=one,dc=test", {"objectClass": ["top"]}),
        lambda: service.modify_entry("cn=one,dc=test", {"cn": [("replace", ["two"])]}),
        lambda: service.delete_entry("cn=one,dc=test"),
        lambda: service.move_entry("cn=one,dc=test", "cn=two"),
        lambda: service.change_password("cn=one,dc=test", "new-secret"),
    ]

    for mutation in mutations:
        with pytest.raises(LDAPOperationError, match="read-only"):
            mutation()


def test_modify_entry_maps_named_operations_to_ldap_constants(monkeypatch):
    profile = DirectoryProfile(name="write", host="ldap.test")
    service = LDAPService(profile, None)

    class FakeConnection:
        def __init__(self):
            self.result = {"message": ""}

        def modify(self, _dn, changes):
            self.changes = changes
            return True

    fake_connection = FakeConnection()

    @contextmanager
    def fake_context(*_args, **_kwargs):
        yield fake_connection

    monkeypatch.setattr(service, "_connection", fake_context)
    service.modify_entry("cn=one,dc=test", {"cn": [("replace", ["two"])]})

    assert fake_connection.changes == {"cn": [(MODIFY_REPLACE, ["two"])]}


def test_modify_protocol_log_redacts_attribute_values(monkeypatch, caplog):
    profile = DirectoryProfile(name="write", host="ldap.test")
    service = LDAPService(profile, None)

    class FakeConnection:
        def modify(self, _dn, _changes):
            self.request = {
                "type": "modifyRequest",
                "entry": _dn,
                "changes": [
                    {
                        "operation": 2,
                        "attribute": {
                            "type": "userPassword",
                            "value": ["sensitive-value"],
                        },
                    }
                ],
                "controls": None,
            }
            return True

    @contextmanager
    def fake_context(*_args, **_kwargs):
        yield FakeConnection()

    monkeypatch.setattr(service, "_connection", fake_context)
    with caplog.at_level("INFO", logger="datasifter.protocol"):
        service.modify_entry(
            "cn=private,dc=test",
            {"userPassword": [("replace", ["sensitive-value"])]},
        )

    assert "modifyRequest" in caplog.text
    assert "userPassword" in caplog.text
    assert "<redacted>" in caplog.text
    assert "'type': 'modifyRequest'" in caplog.text
    assert "sensitive-value" not in caplog.text


@pytest.mark.parametrize(
    "operation,request_data",
    [
        (
            "bindRequest",
            {
                "type": "bindRequest",
                "name": "cn=private,dc=test",
                "authentication": {"simple": "bind-secret", "sasl": None},
                "controls": None,
            },
        ),
        (
            "addRequest",
            {
                "type": "addRequest",
                "entry": "cn=private,dc=test",
                "attributes": {"userPassword": ["add-secret"], "cn": ["private"]},
                "controls": None,
            },
        ),
        (
            "passwordModifyRequest",
            {
                "type": "extendedReq",
                "name": "1.3.6.1.4.1.4203.1.11.1",
                "value": b"password-modify-secret",
                "controls": None,
            },
        ),
    ],
)
def test_protocol_log_redacts_decoded_sensitive_request_values(
    operation, request_data, caplog
):
    with caplog.at_level("INFO", logger="datasifter.protocol"):
        _log_protocol_request(operation, SimpleNamespace(request=request_data))

    assert "<redacted>" in caplog.text
    for secret in ("bind-secret", "add-secret", "password-modify-secret"):
        assert secret not in caplog.text


def test_search_protocol_log_redacts_assertion_values(monkeypatch, caplog):
    profile = DirectoryProfile(name="search", host="ldap.test")
    service = LDAPService(profile, None)

    class FakeConnection:
        def __init__(self):
            self.entries = []
            self.result = {"controls": {}}

        def search(self, **_kwargs):
            self.request = {
                "type": "searchRequest",
                "base": "dc=test",
                "scope": 2,
                "filter": "(&(uid=private-user)(cn~=Confidential Name))",
                "attributes": ["*"],
                "controls": None,
            }
            return True

    @contextmanager
    def fake_context(*_args, **_kwargs):
        yield FakeConnection()

    monkeypatch.setattr(service, "_connection", fake_context)
    with caplog.at_level("INFO", logger="datasifter.protocol"):
        service.search(
            "dc=test",
            "(&(uid=private-user)(cn~=Confidential Name))",
        )

    assert "searchRequest" in caplog.text
    assert "'type': 'searchRequest'" in caplog.text
    assert "uid=<redacted>" in caplog.text
    assert "cn~=<redacted>" in caplog.text
    assert "private-user" not in caplog.text
    assert "Confidential Name" not in caplog.text


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("(uid=alice)", "(uid=<redacted>)"),
        (
            "(&(uid=alice)(cn:dn:1.2.3:=secret))",
            "(&(uid=<redacted>)(cn:dn:1.2.3:=<redacted>))",
        ),
        (r"(cn=a\29b)", "(cn=<redacted>)"),
    ],
)
def test_filter_redaction_keeps_structure_only(expression, expected):
    assert _redact_filter_values(expression) == expected


def test_cancelled_search_interrupts_an_active_ldap_socket(monkeypatch):
    cancelled = Event()
    request_started = Event()
    socket_closed = Event()

    class FakeSocket:
        def shutdown(self, _how):
            socket_closed.set()

        def close(self):
            socket_closed.set()

    class FakeConnection:
        def __init__(self, _server, **_options):
            self.closed = True
            self.socket = FakeSocket()
            self.entries = []
            self.result = {"controls": {}}

        def open(self):
            self.closed = False

        def bind(self):
            return True

        def search(self, **_kwargs):
            request_started.set()
            if not socket_closed.wait(1):
                raise TimeoutError("cancellation did not interrupt the LDAP request")
            raise OSError("socket closed")

        def unbind(self):
            self.closed = True

    monkeypatch.setattr("datasifter.directory.service.Connection", FakeConnection)
    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)
    errors = []

    def run_search():
        try:
            service.search(
                "dc=test",
                "(objectClass=*)",
                cancelled=cancelled,
            )
        except LDAPOperationError as error:
            errors.append(error)

    worker = Thread(target=run_search)
    worker.start()
    assert request_started.wait(1)
    cancelled.set()
    worker.join(1)

    assert not worker.is_alive()
    assert socket_closed.is_set()
    assert len(errors) == 1
    assert "cancelled" in str(errors[0]).casefold()


def test_cancelled_write_interrupts_socket_and_warns_of_uncertain_outcome(monkeypatch):
    cancelled = Event()
    request_started = Event()
    socket_closed = Event()
    profile = DirectoryProfile(name="write", host="ldap.test")

    class FakeSocket:
        def shutdown(self, _how):
            socket_closed.set()

        def close(self):
            socket_closed.set()

    class FakeConnection:
        def __init__(self, _server, **_options):
            self.closed = True
            self.socket = FakeSocket()

        def open(self):
            self.closed = False

        def bind(self):
            return True

        def modify(self, _dn, _changes):
            request_started.set()
            if not socket_closed.wait(1):
                raise TimeoutError("cancellation did not interrupt the LDAP write")
            raise OSError("socket closed")

    monkeypatch.setattr("datasifter.directory.service.Connection", FakeConnection)
    service = LDAPService(profile, None)
    errors = []

    def run_write():
        try:
            service.modify_entry(
                "cn=one,dc=test",
                {"description": [("replace", ["new value"])]},
                cancelled,
            )
        except LDAPOperationError as error:
            errors.append(error)

    worker = Thread(target=run_write)
    worker.start()
    assert request_started.wait(1)
    cancelled.set()
    worker.join(1)

    assert not worker.is_alive()
    assert socket_closed.is_set()
    assert len(errors) == 1
    assert "may have completed" in str(errors[0])


def test_cancelled_operations_fail_before_opening_a_connection(monkeypatch):
    cancelled = Event()
    cancelled.set()
    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("cancelled operations must not open a connection")

    monkeypatch.setattr(service, "_connection", unexpected_connection)
    operations = (
        lambda: service.test_network(cancelled),
        lambda: service.test_authentication(cancelled),
        lambda: service.discover_base_dns(cancelled),
        lambda: service.schema_options(cancelled),
        lambda: service.search("dc=test", "(objectClass=*)", cancelled=cancelled),
        lambda: service.search_page(
            "dc=test",
            "(objectClass=*)",
            cancelled=cancelled,
        ),
        lambda: service.read_entry("cn=one,dc=test", cancelled=cancelled),
        lambda: service.add_entry("cn=one,dc=test", {"objectClass": ["top"]}, cancelled),
        lambda: service.modify_entry("cn=one,dc=test", {}, cancelled),
        lambda: service.delete_entry("cn=one,dc=test", cancelled=cancelled),
        lambda: service.move_entry("cn=one,dc=test", "cn=two", cancelled=cancelled),
        lambda: service.change_password(
            "cn=one,dc=test",
            "new-value",
            cancelled=cancelled,
        ),
    )
    for operation in operations:
        with pytest.raises(LDAPOperationError, match="cancelled"):
            operation()


def test_recursive_delete_stops_between_entries_when_cancelled(monkeypatch):
    cancelled = Event()
    profile = DirectoryProfile(name="test", host="ldap.test")
    service = LDAPService(profile, None)

    class FakeEntry:
        def __init__(self, dn):
            self.entry_dn = dn
            self.entry_attributes = []

        def __getitem__(self, _name):
            raise KeyError(_name)

    class FakeConnection:
        def __init__(self):
            self.entries = [FakeEntry("cn=parent,dc=test"), FakeEntry("cn=child,cn=parent,dc=test")]
            self.result = {"controls": {}}
            self.deleted = []

        def search(self, **_kwargs):
            return True

        def delete(self, dn):
            self.deleted.append(dn)
            cancelled.set()
            return True

    connection = FakeConnection()

    @contextmanager
    def fake_context(*_args, **_kwargs):
        yield connection

    monkeypatch.setattr(service, "_connection", fake_context)

    with pytest.raises(LDAPOperationError, match="may have completed"):
        service.delete_entry("dc=test", recursive=True, cancelled=cancelled)

    assert connection.deleted == ["cn=child,cn=parent,dc=test"]

def test_schema_options_exposes_attribute_syntax_and_object_class_rules(monkeypatch):
    profile = DirectoryProfile(name="schema", host="ldap.test")
    service = LDAPService(profile, None)
    schema = SimpleNamespace(
        object_classes={
            "person": SimpleNamespace(
                kind=1,
                must_contain=["sn", "cn"],
                may_contain=["uid"],
                superior=["top"],
            )
        },
        attribute_types={
            "cn": SimpleNamespace(
                oid="2.5.4.3",
                syntax="1.3.6.1.4.1.1466.115.121.1.15",
                description="Common name",
                single_value=False,
                equality="caseIgnoreMatch",
                ordering="",
                substring="caseIgnoreSubstringsMatch",
            )
        },
    )

    class FakeConnection:
        server = SimpleNamespace(schema=schema)

    @contextmanager
    def fake_context(*_args, **_kwargs):
        yield FakeConnection()

    monkeypatch.setattr(service, "_connection", fake_context)
    monkeypatch.setattr(
        "datasifter.directory.service.constant_to_class_kind",
        lambda _kind: "structural",
    )

    result = service.schema_options()

    assert result["object_classes"]["person"]["required"] == ["sn", "cn"]
    assert result["object_classes"]["person"]["superiors"] == ["top"]
    assert result["attribute_details"][0]["syntax"] == (
        "1.3.6.1.4.1.1466.115.121.1.15"
    )
    assert result["attribute_details"][0]["equality"] == "caseIgnoreMatch"


def test_network_test_reaches_tcp_listener_without_ldap_bind(monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        profile = DirectoryProfile(
            name="local", host="127.0.0.1", port=listener.getsockname()[1],
            authentication_mode="simple", bind_dn="cn=test",
        )
        service = LDAPService(profile, None)

        def unexpected_connection():
            raise AssertionError("TCP test must not bind or retrieve credentials")

        monkeypatch.setattr(service, "_connection", unexpected_connection)
        assert "TLS and LDAP authentication were not tested" in service.test_network()


def test_network_test_surfaces_connection_failure(monkeypatch):
    def failed_connection(*args, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(socket, "create_connection", failed_connection)
    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)
    with pytest.raises(LDAPOperationError, match="TCP connectivity failed"):
        service.test_network()


@pytest.mark.parametrize("authentication,password", [("anonymous", None), ("simple", "secret")])
def test_authentication_test_binds_without_search(monkeypatch, authentication, password):
    profile = DirectoryProfile(
        name="test", host="ldap.test", authentication_mode=authentication, bind_dn="cn=test",
    )
    service = LDAPService(profile, password)
    calls = []

    @contextmanager
    def bound_connection(*_args, **_kwargs):
        calls.append("bind")
        yield object()

    monkeypatch.setattr(service, "_connection", bound_connection)
    message = service.test_authentication()
    assert calls == ["bind"]
    assert ("No user credentials" in message) == (authentication == "anonymous")


def test_authentication_test_rejects_empty_simple_bind_password():
    service = LDAPService(
        DirectoryProfile(
            name="test", host="ldap.test", authentication_mode="simple", bind_dn="cn=test",
        ),
        None,
    )
    with pytest.raises(LDAPOperationError, match="requires a bind password"):
        service.test_authentication()


def test_authentication_test_surfaces_bind_failure(monkeypatch):
    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)

    @contextmanager
    def failed_bind(*_args, **_kwargs):
        raise LDAPOperationError("LDAP bind failed: invalidCredentials")
        yield

    monkeypatch.setattr(service, "_connection", failed_bind)
    with pytest.raises(LDAPOperationError, match="invalidCredentials"):
        service.test_authentication()


def test_authentication_uses_starttls_before_binding_and_closes_connection(monkeypatch):
    calls = []
    profile = DirectoryProfile(
        name="test", host="ldap.test", transport="starttls",
        authentication_mode="simple", bind_dn="cn=admin",
    )

    class FakeConnection:
        closed = True

        def __init__(self, server, **options):
            assert options["user"] == "cn=admin"
            assert options["password"] == "secret"
            assert options["auto_bind"] is False

        def open(self):
            calls.append("open")
            self.closed = False

        def start_tls(self, read_server_info):
            calls.append("tls")

        def bind(self):
            calls.append("bind")
            return True

        def unbind(self):
            calls.append("unbind")
            self.closed = True

    monkeypatch.setattr("datasifter.directory.service.Connection", FakeConnection)
    service = LDAPService(profile, "secret")
    assert "authentication succeeded" in service.test_authentication()
    assert calls == ["open", "tls", "bind", "unbind"]


def test_base_dn_discovery_queries_root_dse_and_prioritizes_default(monkeypatch):
    from types import SimpleNamespace

    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)

    class RootConnection:
        def __init__(self):
            self.entries = [SimpleNamespace(entry_attributes_as_dict={
                "namingContexts": ["dc=other", "dc=default", "dc=other"],
                "defaultNamingContext": ["dc=default"],
            })]

        def search(self, **kwargs):
            assert kwargs["search_base"] == ""
            assert kwargs["search_scope"] == "BASE"
            assert kwargs["attributes"] == ["*"]
            assert kwargs["get_operational_attributes"] is True
            return True

    @contextmanager
    def bound(*_args, **_kwargs):
        yield RootConnection()

    monkeypatch.setattr(service, "_connection", bound)
    assert service.discover_base_dns() == ["dc=default", "dc=other"]


def test_base_dn_discovery_reports_access_failure(monkeypatch):
    from types import SimpleNamespace

    service = LDAPService(DirectoryProfile(name="test", host="ldap.test"), None)
    connection = SimpleNamespace(
        result={"description": "insufficientAccessRights"},
        search=lambda **kwargs: False,
    )

    @contextmanager
    def bound(*_args, **_kwargs):
        yield connection

    monkeypatch.setattr(service, "_connection", bound)
    with pytest.raises(LDAPOperationError, match="Base DN discovery failed"):
        service.discover_base_dns()
