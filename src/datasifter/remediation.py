"""Structured, UI-independent diagnostics for directory and local-model failures."""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from enum import Enum

from ldap3.core.exceptions import (
    LDAPAttributeError,
    LDAPBindError,
    LDAPCommunicationError,
    LDAPInvalidServerError,
    LDAPInvalidValueError,
    LDAPObjectClassError,
    LDAPOperationResult,
    LDAPSchemaError,
)


class DiagnosticCategory(str, Enum):
    AUTHENTICATION = "authentication"
    CONNECTIVITY = "connectivity"
    CERTIFICATE = "certificate"
    PERMISSION = "permission"
    SCHEMA = "schema"
    CONSTRAINT = "constraint"
    DIRECTORY = "directory"
    MODEL = "model"
    SAFETY = "safety"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionableDiagnostic:
    category: DiagnosticCategory
    code: str
    message: str
    remediation: tuple[str, ...] = ()
    context: str = ""

    def as_text(self) -> str:
        parts = [self.message]
        if self.context:
            parts.append(self.context)
        if self.remediation:
            parts.append("What to do:\n" + "\n".join(f"- {step}" for step in self.remediation))
        return "\n\n".join(parts)


_LDAP_RESULTS: dict[int, tuple[DiagnosticCategory, str, tuple[str, ...]]] = {
    8: (DiagnosticCategory.AUTHENTICATION, "The server requires stronger authentication.",
        ("Use an approved TLS connection and the authentication method required by the server.",)),
    19: (DiagnosticCategory.CONSTRAINT, "The directory rejected a constraint.",
         ("Check the server's schema and policy for the requested values.",)),
    20: (DiagnosticCategory.CONSTRAINT, "An attribute value already exists.",
         ("Review the current entry and remove the duplicate from the change.",)),
    16: (DiagnosticCategory.SCHEMA, "The requested attribute value was not found.",
         ("Refresh the entry and check which values are present.",)),
    17: (DiagnosticCategory.SCHEMA, "The directory does not recognize an attribute.",
         ("Use an attribute advertised by the connected directory schema.",)),
    21: (DiagnosticCategory.SCHEMA, "An attribute value has invalid syntax.",
         ("Check the attribute syntax in the schema and correct the value.",)),
    32: (DiagnosticCategory.DIRECTORY, "The requested directory entry was not found.",
         ("Check the base DN and target DN, then refresh the directory.",)),
    34: (DiagnosticCategory.DIRECTORY, "The distinguished name is invalid.",
         ("Correct the DN and escape special characters in its values.",)),
    49: (DiagnosticCategory.AUTHENTICATION, "Directory authentication failed.",
         ("Check the bind identity and password in the connection profile.",)),
    48: (DiagnosticCategory.AUTHENTICATION, "The server rejected the authentication method.",
         ("Choose an authentication method supported by the server.",)),
    50: (DiagnosticCategory.PERMISSION, "The directory denied the operation.",
         ("Check the bind account's permissions and directory access controls.",)),
    53: (DiagnosticCategory.CONSTRAINT, "The directory refused the operation.",
         ("Check the server's policy, permissions, and transport requirements.",)),
    51: (DiagnosticCategory.CONNECTIVITY, "The directory server is busy.",
         ("Wait and retry; check server load if the problem persists.",)),
    52: (DiagnosticCategory.CONNECTIVITY, "The directory server is unavailable.",
         ("Check the directory service and network access before retrying.",)),
    64: (DiagnosticCategory.SCHEMA, "The entry violates directory naming rules.",
         ("Review the target DN and the naming rules for its parent container.",)),
    65: (DiagnosticCategory.SCHEMA, "The entry violates the object-class schema.",
         ("Check required and permitted attributes for the selected object classes.",)),
    66: (DiagnosticCategory.CONSTRAINT, "The entry has subordinate entries.",
         ("Review and remove subordinate entries first, or explicitly review a recursive delete.",)),
    67: (DiagnosticCategory.CONSTRAINT, "The naming attribute cannot be changed this way.",
         ("Use a rename operation to change the entry's relative DN.",)),
    68: (DiagnosticCategory.CONSTRAINT, "An entry already exists at the target DN.",
         ("Choose a different name or inspect the existing entry.",)),
}


def issue_remediation(code: str) -> tuple[str, ...]:
    """Return remediation for a typed dry-run issue, without parsing its message."""
    steps = {
        "read_only": "Use a writable profile only if you are authorized to make this change.",
        "outside_base_dn": "Choose a target inside the profile's configured base DN.",
        "entry_not_found": "Check the target name or DN and refresh the directory.",
        "entry_exists": "Choose another name or inspect the existing entry.",
        "parent_not_found": "Choose an existing parent container.",
        "has_children": "Review the subordinate entries before requesting recursive deletion.",
        "protected_entry": "Choose a non-protected entry; do not modify directory system objects.",
        "unknown_attribute": "Choose an attribute supported by the connected directory schema.",
        "unknown_object_class": "Choose object classes supported by the connected schema.",
        "missing_required_attribute": "Provide the attributes required by the selected object classes.",
        "single_valued_attribute": "Provide only one value for this attribute.",
        "value_conflict": "Review the current values and remove conflicting changes.",
        "invalid_value": "Correct the attribute value according to the directory schema.",
        "system_attribute": "Use a writable attribute instead of a server-maintained attribute.",
        "rdn_attribute": "Use a rename operation to change the naming attribute.",
        "insecure_transport": "Use an approved LDAPS or StartTLS connection for this operation.",
        "schema_unavailable": "Load or restore the directory schema before retrying.",
        "account_state": "Review the backend's account-state and password requirements.",
        "unsupported": "Use a supported operation for this directory backend.",
        "invalid_target": "Choose a valid target and destination within the allowed scope.",
    }
    return (steps[code],) if code in steps else ()


def translate_error(error: Exception) -> ActionableDiagnostic:
    """Translate structured failures without relying on exception-message matching."""
    from datasifter.ai.local_model import LocalModelError, ModelOutputError
    from datasifter.datalens.errors import (
        ConfirmationRequiredError,
        MutationRejectedError,
        PreviewChangedError,
        ReadOnlyDirectoryError,
    )
    from datasifter.datalens.service import AuditLogError
    from datasifter.directory.service import LDAPImportError, LDAPOperationError, LDAPResultError
    from datasifter.security.certificates import CertificateTrustError

    if isinstance(error, AuditLogError):
        return ActionableDiagnostic(DiagnosticCategory.SAFETY, "audit", str(error),
                                    ("Check the local audit log permissions and disk space.",))
    if isinstance(error, PreviewChangedError):
        return ActionableDiagnostic(DiagnosticCategory.SAFETY, "preview_changed", str(error),
                                    ("Generate and review a new preview before applying.",))
    if isinstance(error, ConfirmationRequiredError):
        return ActionableDiagnostic(DiagnosticCategory.SAFETY, "confirmation_required", str(error),
                                    ("Review the complete preview and explicitly confirm it.",))
    if isinstance(error, ReadOnlyDirectoryError):
        return ActionableDiagnostic(DiagnosticCategory.SAFETY, "read_only", str(error),
                                    issue_remediation("read_only"))
    if isinstance(error, MutationRejectedError):
        issues = tuple(issue for issue in error.preview.issues if issue.severity.value == "error")
        steps = tuple(dict.fromkeys(step for issue in issues for step in issue_remediation(issue.code.value)))
        return ActionableDiagnostic(DiagnosticCategory.CONSTRAINT, "preview_rejected", str(error), steps)

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = None
        if isinstance(current, LDAPResultError):
            code = current.result_code
        elif isinstance(current, LDAPOperationResult) and isinstance(current.result, int):
            code = current.result
        if code in _LDAP_RESULTS:
            category, message, steps = _LDAP_RESULTS[code]
            context = str(error) if isinstance(error, LDAPImportError) else ""
            return ActionableDiagnostic(category, f"ldap_{code}", message, steps, context)
        if code is not None:
            return ActionableDiagnostic(
                DiagnosticCategory.DIRECTORY, f"ldap_{code}",
                f"The directory returned LDAP result code {code}.",
                ("Consult the directory server's documentation and logs for this result code.",),
                str(error) if isinstance(error, LDAPImportError) else "",
            )
        if isinstance(current, (CertificateTrustError, ssl.SSLError)):
            return ActionableDiagnostic(
                DiagnosticCategory.CERTIFICATE, "tls_certificate",
                "The directory TLS connection or certificate could not be verified.",
                ("Check the server certificate, hostname, trust settings, and certificate expiry.",),
            )
        if isinstance(current, (LDAPCommunicationError, LDAPInvalidServerError, ConnectionError,
                                TimeoutError, socket.gaierror)):
            return ActionableDiagnostic(
                DiagnosticCategory.CONNECTIVITY, "connection",
                "The directory could not be reached or the connection was interrupted.",
                ("Check the host, port, network access, and directory server availability.",),
            )
        if isinstance(current, LDAPBindError):
            return ActionableDiagnostic(
                DiagnosticCategory.AUTHENTICATION, "bind",
                "The directory bind could not be completed.",
                ("Check the bind identity, credentials, and server authentication requirements.",),
            )
        if isinstance(current, (LDAPAttributeError, LDAPObjectClassError, LDAPSchemaError,
                                LDAPInvalidValueError)):
            return ActionableDiagnostic(
                DiagnosticCategory.SCHEMA, "ldap_schema",
                "The operation does not match the directory schema.",
                ("Check supported object classes, attribute names, and value syntax in the schema inspector.",),
            )
        if isinstance(current, ModelOutputError):
            from datasifter.datalens.help import get_help_catalog

            catalog = get_help_catalog()
            examples = tuple(
                f"{item.operation.capitalize()}: {item.example}"
                for item in catalog.intents
            )
            local_examples = tuple(item.example for item in catalog.directory_information)
            return ActionableDiagnostic(
                DiagnosticCategory.MODEL, "model_output",
                "The local model returned an invalid structured intent.",
                (
                    "Rephrase the request with one operation and explicit names.",
                    "Try one of these supported request examples:",
                    *examples,
                    "Directory information commands work without a model:",
                    *local_examples,
                    "See Help → Data Lens help → Directory information for details.",
                    "If it continues, try another approved GGUF model.",
                ),
            )
        if isinstance(current, LocalModelError):
            return ActionableDiagnostic(
                DiagnosticCategory.MODEL, "model", str(current),
                ("Check the approved GGUF model and local AI settings, then retry.",),
            )
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)

    if isinstance(error, LDAPOperationError):
        return ActionableDiagnostic(
            DiagnosticCategory.DIRECTORY, "ldap_error", str(error),
            ("Check the directory connection and server-side details before retrying.",),
        )
    return ActionableDiagnostic(DiagnosticCategory.UNKNOWN, "unknown", str(error))
