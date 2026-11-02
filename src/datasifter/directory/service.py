"""Synchronous LDAP operations. Construct and use one instance per worker task."""

from __future__ import annotations

import logging
import socket
import ssl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread
from time import perf_counter
from typing import Any

from ldap3 import (
    ALL,
    ANONYMOUS,
    BASE,
    EXTERNAL,
    LEVEL,
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    NONE,
    SASL,
    SIMPLE,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.protocol.rfc4512 import constant_to_class_kind

from datasifter.config import DirectoryProfile
from datasifter.security.certificates import CertificateTrustStore


class LDAPOperationError(RuntimeError):
    """Raised for LDAP operation failures with safe, user-facing detail."""


MODIFY_OPERATIONS = {
    "add": MODIFY_ADD,
    "delete": MODIFY_DELETE,
    "replace": MODIFY_REPLACE,
}
protocol_logger = logging.getLogger("datasifter.protocol")


def _dn_depth(distinguished_name: str) -> int:
    depth = 1
    escaped = False
    for character in distinguished_name:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ",":
            depth += 1
    return depth


def _redact_request_value(value: Any, key: str, request_type: str) -> Any:
    normalized_key = key.casefold().replace("_", "")
    if normalized_key == "filter" and isinstance(value, str):
        return _redact_filter_values(value)
    if normalized_key in {
        "simple",
        "credentials",
        "password",
        "newpassword",
        "oldpassword",
        "requestvalue",
        "vals",
        "values",
        "value",
        "controls",
    }:
        return "<redacted>"
    if request_type == "addRequest" and normalized_key == "attributes":
        if isinstance(value, dict):
            return {
                name: ["<redacted>"] * (len(values) if isinstance(values, list) else 1)
                for name, values in value.items()
            }
        return "<redacted>"
    if isinstance(value, dict):
        return {
            child_key: _redact_request_value(child_value, child_key, request_type)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_request_value(item, key, request_type)
            for item in value
        ]
    return value


def _log_protocol_request(
    operation: str,
    connection: Connection | None = None,
    **fields: Any,
) -> None:
    request = getattr(connection, "request", None)
    request_types = {
        "bindRequest": "bindRequest",
        "searchRequest": "searchRequest",
        "addRequest": "addRequest",
        "modifyRequest": "modifyRequest",
        "deleteRequest": "delRequest",
        "modifyDNRequest": "modDNRequest",
        "passwordModifyRequest": "extendedReq",
    }
    if (
        isinstance(request, dict)
        and request.get("type") == request_types.get(operation)
    ):
        safe_request = {
            key: _redact_request_value(value, key, str(request["type"]))
            for key, value in request.items()
        }
        protocol_logger.info("LDAP request %s", safe_request)
        return
    protocol_logger.info("LDAP request %s", {"operation": operation, **fields})


def _redact_filter_values(expression: str) -> str:
    from datasifter.directory.filters import validate_filter

    validate_filter(expression)
    text = expression.strip()
    output: list[str] = []

    def parse_filter(position: int) -> int:
        output.append("(")
        position += 1
        if text[position] in "&|!":
            output.append(text[position])
            position += 1
            while position < len(text) and text[position] == "(":
                position = parse_filter(position)
        else:
            escaped = False
            close = position
            while close < len(text):
                if escaped:
                    escaped = False
                elif text[close] == "\\":
                    escaped = True
                elif text[close] == ")":
                    break
                close += 1
            assertion = text[position:close]
            operator_end = -1
            escaped = False
            for index, character in enumerate(assertion):
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == "=":
                    operator_end = index + 1
                    if index and assertion[index - 1] in "~<>:":
                        continue
                    break
            if operator_end < 0:
                output.append("<redacted>")
            else:
                output.append(assertion[:operator_end])
                output.append("<redacted>")
            position = close
        output.append(")")
        return position + 1

    position = 0
    while position < len(text):
        position = parse_filter(position)
    return "".join(output)


def _raise_if_cancelled(cancelled: Event | None, write: bool = False) -> None:
    if cancelled and cancelled.is_set():
        message = "LDAP operation cancelled."
        if write:
            message += (
                " An in-flight directory change may have completed; verify the directory "
                "before retrying."
            )
        raise LDAPOperationError(message)


def _interrupt_socket_on_cancel(
    connection: Connection,
    cancelled: Event,
    finished: Event,
) -> None:
    while not finished.is_set():
        if not cancelled.wait(0.05):
            continue
        while not finished.is_set():
            connection_socket = getattr(connection, "socket", None)
            if connection_socket is not None:
                try:
                    connection_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection_socket.close()
                except OSError:
                    pass
                return
            finished.wait(0.05)


class LDAPService:
    def __init__(
        self,
        profile: DirectoryProfile,
        password: str | None,
        client_key_passphrase: str | None = None,
        trust_store: CertificateTrustStore | None = None,
    ) -> None:
        self.profile = profile
        self.password = password
        self.client_key_passphrase = client_key_passphrase
        self.trust_store = trust_store or CertificateTrustStore()

    def test_network(self, cancelled: Event | None = None) -> str:
        """Check TCP reachability without sending LDAP credentials."""
        started = perf_counter()
        try:
            _raise_if_cancelled(cancelled)
            deadline = started + 10
            while True:
                _raise_if_cancelled(cancelled)
                try:
                    with socket.create_connection(
                        (self.profile.host, self.profile.port),
                        timeout=min(0.25, max(0.01, deadline - perf_counter())),
                    ):
                        break
                except TimeoutError:
                    if perf_counter() >= deadline:
                        raise
        except OSError as error:
            raise LDAPOperationError(f"TCP connectivity failed: {error}") from error
        protocol_logger.info(
            "TCP connectivity test completed elapsed_ms=%d",
            int((perf_counter() - started) * 1000),
        )
        return (
            f"TCP connection to {self.profile.host}:{self.profile.port} succeeded. "
            "TLS and LDAP authentication were not tested."
        )

    def test_authentication(self, cancelled: Event | None = None) -> str:
        """Perform the configured LDAP bind without browsing directory entries."""
        _raise_if_cancelled(cancelled)
        if self.profile.authentication_mode == "simple" and not self.password:
            raise LDAPOperationError(
                "Simple bind requires a bind password. Edit the profile to store one."
            )
        with self._connection(cancelled):
            pass
        protocol_logger.info(
            "LDAP authentication test completed authentication=%s",
            self.profile.authentication_mode,
        )
        if self.profile.authentication_mode == "anonymous":
            return "Anonymous LDAP bind succeeded. No user credentials were authenticated."
        return f"LDAP authentication succeeded ({self.profile.authentication_mode})."

    def discover_base_dns(self, cancelled: Event | None = None) -> list[str]:
        """Read advertised naming contexts from the authenticated Root DSE."""
        _raise_if_cancelled(cancelled)
        with self._connection(cancelled) as connection:
            _raise_if_cancelled(cancelled)
            try:
                search_succeeded = connection.search(
                    search_base="",
                    search_filter="(objectClass=*)",
                    search_scope=BASE,
                    attributes=["*"],
                    get_operational_attributes=True,
                )
            finally:
                _log_protocol_request(
                    "searchRequest",
                    connection,
                    base="",
                    filter=_redact_filter_values("(objectClass=*)"),
                    scope="BASE",
                    attributes=["*", "+"],
                )
            if not search_succeeded:
                raise LDAPOperationError(
                    self._result_message(connection, "Base DN discovery failed")
                )
            _raise_if_cancelled(cancelled)
            contexts: list[str] = []
            for entry in connection.entries:
                attributes = {
                    key.casefold(): value for key, value in entry.entry_attributes_as_dict.items()
                }
                for name in ("defaultnamingcontext", "namingcontexts"):
                    values = attributes.get(name, [])
                    if isinstance(values, str):
                        values = [values]
                    for value in values:
                        if isinstance(value, str) and value.strip() and value not in contexts:
                            contexts.append(value)
        protocol_logger.info("Base DN discovery completed context_count=%d", len(contexts))
        return contexts

    @contextmanager
    def _connection(
        self,
        cancelled: Event | None = None,
        write_operation: bool = False,
    ) -> Iterator[Connection]:
        _raise_if_cancelled(cancelled, write_operation)
        if self.profile.authentication_mode == "simple" and not self.password:
            raise LDAPOperationError(
                "Simple bind requires a bind password. Edit the profile to store one."
            )
        tls = None
        use_ssl = self.profile.transport == "ldaps"
        if self.profile.transport != "plain":
            ca_certificate = self.profile.ca_certificate or (
                self.trust_store.pem_for_profile(self.profile) or ""
            )
            tls = Tls(
                validate=ssl.CERT_REQUIRED,
                ca_certs_file=ca_certificate or None,
                local_certificate_file=self.profile.client_certificate or None,
                local_private_key_file=self.profile.client_key or None,
                local_private_key_password=self.client_key_passphrase,
                valid_names=[self.profile.host],
            )
        server = Server(
            self.profile.host,
            port=self.profile.port,
            use_ssl=use_ssl,
            tls=tls,
            get_info=ALL,
            connect_timeout=10,
        )
        if self.profile.authentication_mode == "simple":
            authentication = SIMPLE
            user = self.profile.bind_dn
            password = self.password or None
            sasl_mechanism = None
        elif self.profile.authentication_mode == "sasl_external":
            authentication = SASL
            user = None
            password = None
            sasl_mechanism = EXTERNAL
        else:
            authentication = ANONYMOUS
            user = None
            password = None
            sasl_mechanism = None
        connection_options: dict[str, Any] = {
            "user": user,
            "password": password,
            "authentication": authentication,
            "auto_bind": False,
            "raise_exceptions": True,
            "receive_timeout": 30,
        }
        if sasl_mechanism:
            connection_options["sasl_mechanism"] = sasl_mechanism
        connection = Connection(server, **connection_options)
        finished = Event()
        cancellation_thread = None
        try:
            if cancelled:
                cancellation_thread = Thread(
                    target=_interrupt_socket_on_cancel,
                    args=(connection, cancelled, finished),
                    daemon=True,
                )
                cancellation_thread.start()
            connection.open()
            _raise_if_cancelled(cancelled, write_operation)
            if self.profile.transport == "starttls":
                connection.start_tls(read_server_info=False)
            self._verify_tofu_pin(connection)
            try:
                bind_succeeded = connection.bind()
            finally:
                _log_protocol_request(
                    "bindRequest",
                    connection,
                    authentication=self.profile.authentication_mode,
                    bind_dn=(
                        self.profile.bind_dn
                        if self.profile.authentication_mode == "simple"
                        else ""
                    ),
                    password="<redacted>",
                )
            if not bind_succeeded:
                raise LDAPOperationError(self._result_message(connection, "LDAP bind failed"))
            yield connection
        except LDAPOperationError:
            if cancelled and cancelled.is_set():
                _raise_if_cancelled(cancelled, write_operation)
            raise
        except Exception as error:
            if cancelled and cancelled.is_set():
                _raise_if_cancelled(cancelled, write_operation)
            raise LDAPOperationError(f"LDAP operation failed: {error}") from error
        finally:
            finished.set()
            if connection.closed is False:
                if cancelled and cancelled.is_set():
                    connection_socket = getattr(connection, "socket", None)
                    if connection_socket is not None:
                        try:
                            connection_socket.close()
                        except OSError as error:
                            protocol_logger.warning(
                                "LDAP socket close after cancellation failed: %s", error
                            )
                else:
                    connection.unbind()
            if cancellation_thread:
                cancellation_thread.join(timeout=0.5)

    def _verify_tofu_pin(self, connection: Connection) -> None:
        if not self.profile.tofu_fingerprint:
            return
        socket_object = getattr(connection, "socket", None)
        if socket_object is None or not hasattr(socket_object, "getpeercert"):
            raise LDAPOperationError("Unable to verify the pinned directory certificate.")
        certificate_der = socket_object.getpeercert(binary_form=True)
        if not certificate_der:
            raise LDAPOperationError("The directory did not provide a TLS certificate.")
        self.trust_store.verify_fingerprint(self.profile, certificate_der)

    @staticmethod
    def _result_message(connection: Connection, prefix: str) -> str:
        description = connection.result.get("message") or connection.result.get("description")
        return f"{prefix}: {description or 'server returned no detail'}"

    def search(
        self,
        base_dn: str,
        filter_expression: str,
        attributes: list[str] | None = None,
        scope: str = "subtree",
        page_size: int = 500,
        cancelled: Event | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> list[dict[str, Any]]:
        from datasifter.directory.filters import validate_filter

        validate_filter(filter_expression)
        scopes = {"base": BASE, "level": LEVEL, "subtree": SUBTREE}
        if scope not in scopes:
            raise ValueError(f"Unsupported LDAP search scope: {scope}")
        if page_size < 1:
            raise ValueError("Page size must be positive.")
        _raise_if_cancelled(cancelled)
        results: list[dict[str, Any]] = []
        started = perf_counter()
        with self._connection(cancelled) as connection:
            cookie = None
            while True:
                _raise_if_cancelled(cancelled)
                try:
                    connection.search(
                        search_base=base_dn,
                        search_filter=filter_expression,
                        search_scope=scopes[scope],
                        attributes=attributes or ["*"],
                        paged_size=page_size,
                        paged_cookie=cookie,
                    )
                finally:
                    _log_protocol_request(
                        "searchRequest",
                        connection,
                        base=base_dn,
                        filter=_redact_filter_values(filter_expression),
                        scope=scope.upper(),
                        attributes=attributes or ["*"],
                        page_size=page_size,
                        page_cookie="<present>" if cookie else "",
                    )
                _raise_if_cancelled(cancelled)
                results.extend(self._entry_dict(entry) for entry in connection.entries)
                if progress:
                    progress(0, f"Loaded {len(results)} entries")
                controls = connection.result.get("controls", {})
                page_control = controls.get("1.2.840.113556.1.4.319", {})
                cookie = page_control.get("value", {}).get("cookie")
                if not cookie:
                    break
        protocol_logger.info(
            "LDAP search completed elapsed_ms=%d result_count=%d",
            int((perf_counter() - started) * 1000),
            len(results),
        )
        return results

    def search_page(
        self,
        base_dn: str,
        filter_expression: str,
        attributes: list[str] | None = None,
        scope: str = "level",
        page_size: int = 500,
        page_cookie: bytes | None = None,
        cancelled: Event | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> tuple[list[dict[str, Any]], bytes | None]:
        from datasifter.directory.filters import validate_filter

        validate_filter(filter_expression)
        scopes = {"base": BASE, "level": LEVEL, "subtree": SUBTREE}
        if scope not in scopes:
            raise ValueError(f"Unsupported LDAP search scope: {scope}")
        if page_size < 1:
            raise ValueError("Page size must be positive.")
        _raise_if_cancelled(cancelled)
        started = perf_counter()
        with self._connection(cancelled) as connection:
            try:
                connection.search(
                    search_base=base_dn,
                    search_filter=filter_expression,
                    search_scope=scopes[scope],
                    attributes=attributes or ["*"],
                    paged_size=page_size,
                    paged_cookie=page_cookie,
                )
            finally:
                _log_protocol_request(
                    "searchRequest",
                    connection,
                    base=base_dn,
                    filter=_redact_filter_values(filter_expression),
                    scope=scope.upper(),
                    attributes=attributes or ["*"],
                    page_size=page_size,
                    page_cookie="<present>" if page_cookie else "",
                )
            _raise_if_cancelled(cancelled)
            entries = [self._entry_dict(entry) for entry in connection.entries]
            if progress:
                progress(0, f"Loaded {len(entries)} entries")
            controls = connection.result.get("controls", {})
            page_control = controls.get("1.2.840.113556.1.4.319", {})
            cookie = page_control.get("value", {}).get("cookie")
        protocol_logger.info(
            "LDAP paged search completed elapsed_ms=%d result_count=%d",
            int((perf_counter() - started) * 1000),
            len(entries),
        )
        return entries, cookie or None

    def schema_options(self, cancelled: Event | None = None) -> dict[str, Any]:
        _raise_if_cancelled(cancelled)
        with self._connection(cancelled) as connection:
            schema = connection.server.schema
            _log_protocol_request("schemaRequest", connection, source="server.schema")
            if schema is None:
                raise LDAPOperationError("The directory did not provide LDAP schema information.")
            object_classes = {
                name: {
                    "kind": constant_to_class_kind(definition.kind),
                    "required": list(definition.must_contain),
                    "optional": list(definition.may_contain),
                    "superiors": list(definition.superior or []),
                }
                for name, definition in schema.object_classes.items()
            }
            attribute_details = [
                {
                    "name": name,
                    "oid": definition.oid or "",
                    "syntax": definition.syntax or "",
                    "description": definition.description or "",
                    "single_value": definition.single_value,
                    "equality": definition.equality or "",
                    "ordering": definition.ordering or "",
                    "substring": definition.substring or "",
                }
                for name, definition in sorted(
                    schema.attribute_types.items(),
                    key=lambda item: item[0].casefold(),
                )
            ]
            attributes = sorted(schema.attribute_types)
        return {
            "object_classes": object_classes,
            "attributes": attributes,
            "attribute_details": attribute_details,
        }

    @staticmethod
    def _entry_dict(entry: Any) -> dict[str, Any]:
        return {
            "dn": entry.entry_dn,
            "attributes": {
                name: entry[name].values for name in entry.entry_attributes
            },
        }

    @staticmethod
    def inspect_server_certificate(
        profile: DirectoryProfile,
        client_key_passphrase: str | None = None,
        cancelled: Event | None = None,
    ) -> bytes:
        _raise_if_cancelled(cancelled)
        if profile.transport == "plain":
            raise ValueError("A plain LDAP profile does not use a TLS certificate.")
        tls = Tls(
            validate=ssl.CERT_NONE,
            local_certificate_file=profile.client_certificate or None,
            local_private_key_file=profile.client_key or None,
            local_private_key_password=client_key_passphrase,
        )
        server = Server(
            profile.host,
            port=profile.port,
            use_ssl=profile.transport == "ldaps",
            tls=tls,
            get_info=NONE,
            connect_timeout=10,
        )
        connection = Connection(server, auto_bind=False, raise_exceptions=True)
        finished = Event()
        cancellation_thread = None
        try:
            if cancelled:
                cancellation_thread = Thread(
                    target=_interrupt_socket_on_cancel,
                    args=(connection, cancelled, finished),
                    daemon=True,
                )
                cancellation_thread.start()
            connection.open()
            _raise_if_cancelled(cancelled)
            if profile.transport == "starttls":
                connection.start_tls(read_server_info=False)
            certificate = connection.socket.getpeercert(binary_form=True)
            if not certificate:
                raise LDAPOperationError("The directory did not provide a TLS certificate.")
            return ssl.DER_cert_to_PEM_cert(certificate).encode("ascii")
        except LDAPOperationError:
            if cancelled and cancelled.is_set():
                _raise_if_cancelled(cancelled)
            raise
        except Exception as error:
            if cancelled and cancelled.is_set():
                _raise_if_cancelled(cancelled)
            raise LDAPOperationError(f"Unable to inspect directory TLS certificate: {error}") from error
        finally:
            finished.set()
            if connection.closed is False:
                if cancelled and cancelled.is_set():
                    connection_socket = getattr(connection, "socket", None)
                    if connection_socket is not None:
                        try:
                            connection_socket.close()
                        except OSError as error:
                            protocol_logger.warning(
                                "Certificate-inspection socket close after cancellation failed: %s",
                                error,
                            )
                else:
                    connection.unbind()
            if cancellation_thread:
                cancellation_thread.join(timeout=0.5)

    def read_entry(
        self,
        distinguished_name: str,
        include_operational: bool = False,
        cancelled: Event | None = None,
    ) -> dict[str, Any]:
        _raise_if_cancelled(cancelled)
        attributes = ["*", "+"] if include_operational else ["*"]
        results = self.search(
            distinguished_name,
            "(objectClass=*)",
            attributes=attributes,
            scope="base",
            cancelled=cancelled,
        )
        if not results:
            raise LDAPOperationError(f"Directory entry was not found: {distinguished_name}")
        return results[0]

    def add_entry(
        self,
        distinguished_name: str,
        attributes: dict[str, list[Any]],
        cancelled: Event | None = None,
    ) -> None:
        self._ensure_writable()
        _raise_if_cancelled(cancelled, write=True)
        if not attributes.get("objectClass"):
            raise ValueError("New entries must include at least one objectClass.")
        started = perf_counter()
        with self._connection(cancelled, write_operation=True) as connection:
            _raise_if_cancelled(cancelled, write=True)
            try:
                added = connection.add(distinguished_name, attributes=attributes)
            finally:
                _log_protocol_request(
                    "addRequest",
                    connection,
                    dn=distinguished_name,
                    attributes={name: "<redacted>" for name in attributes},
                )
            if not added:
                raise LDAPOperationError(self._result_message(connection, "Unable to add entry"))
        self._log_operation("add", started)

    def modify_entry(
        self,
        distinguished_name: str,
        changes: dict[str, list[tuple[str, Any]]],
        cancelled: Event | None = None,
    ) -> None:
        self._ensure_writable()
        _raise_if_cancelled(cancelled, write=True)
        operations: dict[str, list[tuple[int, Any]]] = {}
        for attribute, updates in changes.items():
            operations[attribute] = []
            for operation, values in updates:
                if operation not in MODIFY_OPERATIONS:
                    raise ValueError(f"Unsupported LDAP modify operation: {operation}")
                operations[attribute].append((MODIFY_OPERATIONS[operation], values))
        started = perf_counter()
        with self._connection(cancelled, write_operation=True) as connection:
            _raise_if_cancelled(cancelled, write=True)
            try:
                modified = connection.modify(distinguished_name, operations)
            finally:
                _log_protocol_request(
                    "modifyRequest",
                    connection,
                    dn=distinguished_name,
                    changes={
                        name: [(operation, "<redacted>") for operation, _values in updates]
                        for name, updates in changes.items()
                    },
                )
            if not modified:
                raise LDAPOperationError(self._result_message(connection, "Unable to modify entry"))
        self._log_operation("modify", started)

    def delete_entry(
        self,
        distinguished_name: str,
        recursive: bool = False,
        cancelled: Event | None = None,
    ) -> None:
        self._ensure_writable()
        _raise_if_cancelled(cancelled, write=True)
        if recursive:
            descendants = self.search(
                distinguished_name,
                "(objectClass=*)",
                attributes=["1.1"],
                scope="subtree",
                cancelled=cancelled,
            )
            dns = sorted(
                (entry["dn"] for entry in descendants),
                key=_dn_depth,
                reverse=True,
            )
        else:
            dns = [distinguished_name]
        started = perf_counter()
        with self._connection(cancelled, write_operation=True) as connection:
            for dn in dns:
                _raise_if_cancelled(cancelled, write=True)
                try:
                    deleted = connection.delete(dn)
                finally:
                    _log_protocol_request("deleteRequest", connection, dn=dn)
                if not deleted:
                    raise LDAPOperationError(self._result_message(connection, f"Unable to delete {dn}"))
        self._log_operation("delete", started)

    def move_entry(
        self,
        distinguished_name: str,
        relative_dn: str,
        new_superior: str | None = None,
        delete_old_rdn: bool = True,
        cancelled: Event | None = None,
    ) -> None:
        self._ensure_writable()
        _raise_if_cancelled(cancelled, write=True)
        started = perf_counter()
        with self._connection(cancelled, write_operation=True) as connection:
            _raise_if_cancelled(cancelled, write=True)
            try:
                modified_dn = connection.modify_dn(
                    distinguished_name,
                    relative_dn,
                    delete_old_dn=delete_old_rdn,
                    new_superior=new_superior,
                )
            finally:
                _log_protocol_request(
                    "modifyDNRequest",
                    connection,
                    dn=distinguished_name,
                    new_rdn=relative_dn,
                    new_superior=new_superior or "",
                    delete_old_rdn=delete_old_rdn,
                )
            if not modified_dn:
                raise LDAPOperationError(self._result_message(connection, "Unable to rename/move entry"))
        self._log_operation("modifyDN", started)

    def change_password(
        self,
        distinguished_name: str,
        new_password: str,
        old_password: str | None = None,
        cancelled: Event | None = None,
    ) -> None:
        self._ensure_writable()
        _raise_if_cancelled(cancelled, write=True)
        started = perf_counter()
        with self._connection(cancelled, write_operation=True) as connection:
            _raise_if_cancelled(cancelled, write=True)
            try:
                changed = connection.extend.standard.modify_password(
                    distinguished_name,
                    new_password,
                    old_password,
                )
            finally:
                _log_protocol_request(
                    "passwordModifyRequest",
                    connection,
                    dn=distinguished_name,
                    old_password="<redacted>" if old_password else "",
                    new_password="<redacted>",
                )
            if not changed:
                raise LDAPOperationError(
                    self._result_message(connection, "LDAP Password Modify operation failed")
                )
        self._log_operation("passwordModify", started)

    @staticmethod
    def _log_operation(operation: str, started: float) -> None:
        protocol_logger.info(
            "LDAP %s completed elapsed_ms=%d",
            operation,
            int((perf_counter() - started) * 1000),
        )

    def _ensure_writable(self) -> None:
        if self.profile.read_only:
            raise LDAPOperationError("This profile is read-only; directory changes are blocked.")
