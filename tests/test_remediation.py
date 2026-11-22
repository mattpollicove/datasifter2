import ssl

import pytest
from ldap3.core.exceptions import (
    LDAPAttributeError,
    LDAPInvalidCredentialsResult,
    LDAPSocketOpenError,
)

from datasifter.ai.local_model import ModelOutputError
from datasifter.directory.service import LDAPImportError, LDAPOperationError, LDAPResultError
from datasifter.directory.worker import DirectoryWorker
from datasifter.remediation import DiagnosticCategory, translate_error


@pytest.mark.parametrize(
    ("code", "category", "phrase"),
    [
        (49, DiagnosticCategory.AUTHENTICATION, "bind identity"),
        (50, DiagnosticCategory.PERMISSION, "permissions"),
        (19, DiagnosticCategory.CONSTRAINT, "schema"),
        (21, DiagnosticCategory.SCHEMA, "syntax"),
        (65, DiagnosticCategory.SCHEMA, "object classes"),
        (66, DiagnosticCategory.CONSTRAINT, "subordinate"),
    ],
)
def test_ldap_result_codes_produce_actionable_diagnostics(code, category, phrase):
    diagnostic = translate_error(LDAPResultError("server detail secret-value", code))
    assert diagnostic.category is category
    assert diagnostic.code == f"ldap_{code}"
    assert phrase in diagnostic.as_text()
    assert "secret-value" not in diagnostic.as_text()


def test_wrapped_ldap_result_and_partial_import_context():
    cause = LDAPInvalidCredentialsResult(result=49)
    error = LDAPImportError("record 2 failed; 1 record was already applied")
    error.__cause__ = cause
    diagnostic = translate_error(error)
    assert diagnostic.code == "ldap_49"
    assert "1 record was already applied" in diagnostic.as_text()


@pytest.mark.parametrize(
    ("cause", "category", "code"),
    [
        (LDAPSocketOpenError("unreachable"), DiagnosticCategory.CONNECTIVITY, "connection"),
        (ssl.SSLError("bad certificate"), DiagnosticCategory.CERTIFICATE, "tls_certificate"),
        (ModelOutputError("invalid output"), DiagnosticCategory.MODEL, "model_output"),
        (LDAPAttributeError("unknown attribute"), DiagnosticCategory.SCHEMA, "ldap_schema"),
    ],
)
def test_wrapped_transport_and_model_failures_are_classified(cause, category, code):
    error = LDAPOperationError("operation failed")
    error.__cause__ = cause
    diagnostic = translate_error(error)
    assert (diagnostic.category, diagnostic.code) == (category, code)
    assert diagnostic.remediation


def test_unknown_ldap_code_is_not_misclassified():
    diagnostic = translate_error(LDAPResultError("server detail", 123))
    assert diagnostic.category is DiagnosticCategory.DIRECTORY
    assert diagnostic.code == "ldap_123"
    assert "123" in diagnostic.as_text()


def test_worker_emits_typed_and_copyable_remediation():
    def fail(_cancelled, _progress):
        raise LDAPResultError("invalid credentials", 49)

    worker = DirectoryWorker(fail)
    diagnostics = []
    messages = []
    worker.signals.diagnostic.connect(diagnostics.append)
    worker.signals.failed.connect(messages.append)
    worker.run()
    assert diagnostics[0].code == "ldap_49"
    assert "What to do:" in messages[0]
    assert "bind identity" in messages[0]
