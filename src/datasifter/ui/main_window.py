"""Phase 1 PyQt6 desktop workspace."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRegularExpression,
    QSignalBlocker,
    Qt,
    QThreadPool,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QKeyEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datasifter import __version__
from datasifter.ai.local_model import LlamaCppFilterGenerator, ModelOutputError, model_sha256
from datasifter.build_info import build_label
from datasifter.config import (
    ConfigurationError,
    ConfigurationStore,
    CredentialVault,
    DirectoryProfile,
)
from datasifter.datalens import (
    ClarificationPrompt,
    ClarifyIntent,
    DirectoryIntent,
    DirectoryResolver,
    PreparedIntent,
    PreparedMutation,
    PreparedSearch,
    ProposedMutationIntent,
    SearchIntent,
    SearchResult,
    SearchSpec,
    create_adapter,
    detect_backend,
    prepare_intent,
)
from datasifter.datalens.help import format_help_catalog, get_help_catalog
from datasifter.datalens.models import is_sensitive
from datasifter.datalens.service import AuditLogError, DataLensMutationService, LocalAuditLog
from datasifter.directory.filters import (
    FilterSyntaxError,
    filter_attribute_names,
    structured_filter,
    validate_filter,
)
from datasifter.directory.ldif import LDIFRecord, read_ldif, write_ldif
from datasifter.directory.service import LDAPOperationError, LDAPService
from datasifter.directory.worker import DirectoryWorker
from datasifter.remediation import issue_remediation
from datasifter.security.certificates import (
    CertificateTrustError,
    CertificateTrustStore,
    certificate_details,
)
from datasifter.session_commands import (
    COUNT_PEOPLE,
    CURRENT_TARGET,
    DISCONNECT,
    LIST_TARGETS,
    MONITOR,
    SessionCommand,
    SessionCommandError,
    describe_target,
    parse_session_command,
    resolve_target,
)
from datasifter.ui.about_dialog import AboutDialog
from datasifter.ui.data_lens_tab import DataLensTab
from datasifter.ui.settings_dialog import SettingsDialog

logger = logging.getLogger("datasifter.protocol")

# Tree item role: whether cached attributes were read with operational attributes ("+").
_CACHED_OPERATIONAL_ROLE = Qt.ItemDataRole.UserRole + 6


@dataclass(frozen=True)
class IntentOperationResult:
    intent: DirectoryIntent
    prepared: PreparedIntent
    schema: tuple[list[str], list[str]]
_DEFAULT_LDAP_FILTER = "(objectClass=*)"
_OPERATIONAL_ATTRIBUTES = frozenset(
    {
        "createtimestamp",
        "creatorsname",
        "modifiersname",
        "modifytimestamp",
        "entryuuid",
        "entrydn",
        "entrycsn",
        "subschemasubentry",
        "hassubordinates",
        "numsubordinates",
        "structuralobjectclass",
        "governingstructurerule",
        "pwdchangedtime",
        "pwdaccountlockedtime",
        "pwdfailuretime",
        "pwdhistory",
        "pwdgraceusetime",
        "ismemberof",
        "nsuniqueid",
        "objectguid",
        "objectsid",
        "whencreated",
        "whenchanged",
        "usncreated",
        "usnchanged",
    }
)
_OPERATIONAL_PREFIXES = ("ds-entry-",)


def _is_operational_attribute(name: str) -> bool:
    folded = name.casefold()
    return folded in _OPERATIONAL_ATTRIBUTES or folded.startswith(_OPERATIONAL_PREFIXES)


def confirm_certificate_trust(
    parent: QWidget, profile: DirectoryProfile, certificate: bytes
) -> bool:
    details = certificate_details(certificate)
    previous = (
        f"Previously trusted SHA-256: {profile.tofu_fingerprint}\n"
        if profile.tofu_fingerprint else ""
    )
    response = QMessageBox.warning(
        parent,
        "Directory certificate changed" if previous else "Trust directory certificate?",
        "Self-signed certificates are recommended for non-production environments only.\n\n"
        f"{previous}SHA-256: {details.fingerprint_sha256}\n"
        f"Issuer: {details.issuer}\nSubject: {details.subject}\n"
        f"Expires: {details.not_after.isoformat()}\n\n"
        "Trust this certificate for this profile? Verify its fingerprint with your "
        "directory administrator before accepting.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return response == QMessageBox.StandardButton.Yes


class LogEmitter(QObject):
    message = pyqtSignal(str)


class LensRequestEdit(QTextEdit):
    submitted = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = LogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        self.emitter.message.emit(self.format(record))


class LDAPFilterHighlighter(QSyntaxHighlighter):
    def __init__(self, document: Any) -> None:
        super().__init__(document)
        attribute_format = QTextCharFormat()
        attribute_format.setForeground(QColor("#795E26"))
        operator_format = QTextCharFormat()
        operator_format.setForeground(QColor("#AF00DB"))
        operator_format.setFontWeight(700)
        boolean_format = QTextCharFormat()
        boolean_format.setForeground(QColor("#0000FF"))
        boolean_format.setFontWeight(700)
        self.rules = (
            (QRegularExpression(r"(?<=\()[A-Za-z0-9;.-]+(?=[:~<>]?=)"), attribute_format),
            (QRegularExpression(r"~=|>=|<=|:=|="), operator_format),
            (QRegularExpression(r"(?<=[(&|!])[&|!]"), boolean_format),
        )

    def highlightBlock(self, text: str) -> None:
        for expression, text_format in self.rules:
            matches = expression.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class ProfileDialog(QDialog):
    def __init__(
        self, parent: QWidget | None = None, profile: DirectoryProfile | None = None,
        existing_profiles: Sequence[DirectoryProfile] = (),
    ) -> None:
        super().__init__(parent)
        self._original_profile = profile
        self._existing_profiles = existing_profiles
        self.save_as = False
        self._profile_id = profile.profile_id if profile else str(uuid4())
        self._test_worker: DirectoryWorker | None = None
        self.trusted_certificate: bytes | None = None
        self._trusted_endpoint: tuple[str, int, str, str] | None = None
        self.setWindowTitle("Edit directory profile" if profile else "Add directory profile")
        self.setMinimumWidth(640)
        layout = QFormLayout(self)
        self.name = QLineEdit()
        self.host = QLineEdit()
        self.port = QLineEdit("389")
        self.transport = QComboBox()
        self.transport.addItem("Plain LDAP", "plain")
        self.transport.addItem("StartTLS", "starttls")
        self.transport.addItem("Implicit LDAPS", "ldaps")
        self.transport.setCurrentIndex(0)
        self._default_port = 389
        self.transport.currentIndexChanged.connect(self._update_default_port)
        self.authentication_mode = QComboBox()
        self.authentication_mode.addItem("Anonymous", "anonymous")
        self.authentication_mode.addItem("Simple bind", "simple")
        self.authentication_mode.addItem("SASL EXTERNAL", "sasl_external")
        self.authentication_mode.setCurrentIndex(
            self.authentication_mode.findData("simple")
        )
        self.bind_dn = QLineEdit()
        self.base_dn = QComboBox()
        self.base_dn.setEditable(True)
        self.base_dn.setPlaceholderText("Test authentication to discover Base DNs, or enter one")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox("Show password")
        self.save_password = QCheckBox("Save password in OS keyring")
        self.ca_certificate = QLineEdit()
        self.client_certificate = QLineEdit()
        self.client_key = QLineEdit()
        self.client_key_passphrase = QLineEdit()
        self.client_key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.read_only = QCheckBox("Block all directory changes")
        self.test_network_button = QPushButton("Test connection")
        self.test_authentication_button = QPushButton("Test authentication")
        self.cancel_test_button = QPushButton("Cancel check")
        self.cancel_test_button.setEnabled(False)
        layout.addRow("Profile name", self.name)
        layout.addRow("Host / IP", self.host)
        layout.addRow("Port", self.port)
        layout.addRow(self.test_network_button)
        layout.addRow("Transport", self.transport)
        self.plain_warning = QLabel(
            "Plain LDAP is unencrypted, including bind credentials. Use only on a trusted network."
        )
        self.plain_warning.setWordWrap(True)
        layout.addRow(self.plain_warning)
        self.transport.currentIndexChanged.connect(
            lambda: self.plain_warning.setVisible(self.transport.currentData() == "plain")
        )
        layout.addRow("Authentication", self.authentication_mode)
        layout.addRow("Bind DN (required for simple bind)", self.bind_dn)
        layout.addRow("Bind password", self.password)
        # The test authentication button should appear after the password options
        # (Show password / Save password).  Move it below the password options row.
        password_options = QHBoxLayout()
        self.password_options = password_options
        password_options.addWidget(self.show_password)
        password_options.addWidget(self.save_password)
        password_options.addStretch()
        layout.addRow(password_options)
        layout.addRow(self.test_authentication_button)
        layout.addRow("Base DN", self.base_dn)
        layout.addRow("Custom CA certificate", self.ca_certificate)
        layout.addRow("Client certificate", self.client_certificate)
        layout.addRow("Client private key", self.client_key)
        layout.addRow("Client key passphrase (stored in OS keyring)", self.client_key_passphrase)
        layout.addRow(self.read_only)
        self.test_status = QLabel("Test the entered settings before saving (optional).")
        self.test_status.setWordWrap(True)
        layout.addRow(self.test_status)
        layout.addRow(self.cancel_test_button)
        if profile is not None:
            copy_notice = QLabel(
                "Save As does not copy saved credentials or certificate trust. "
                "Enter new credentials if needed."
            )
            copy_notice.setWordWrap(True)
            layout.addRow(copy_notice)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_save)
        self.buttons.rejected.connect(self.reject)
        self.save_as_button: QPushButton | None = None
        if profile is not None:
            self.save_as_button = self.buttons.addButton(
                "Save As...", QDialogButtonBox.ButtonRole.ActionRole
            )
            self.save_as_button.clicked.connect(self._accept_save_as)
        layout.addRow(self.buttons)
        self.test_network_button.clicked.connect(lambda: self._test_settings(False))
        self.test_authentication_button.clicked.connect(lambda: self._test_settings(True))
        self.cancel_test_button.clicked.connect(self._cancel_test)
        self.show_password.toggled.connect(
            lambda visible: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        self.profile: DirectoryProfile | None = None
        if profile:
            self.name.setText(profile.name)
            self.host.setText(profile.host)
            self.transport.setCurrentIndex(self.transport.findData(profile.transport))
            self.port.setText(str(profile.port))
            self.authentication_mode.setCurrentIndex(
                self.authentication_mode.findData(profile.authentication_mode)
            )
            for field_name in (
                "bind_dn", "ca_certificate", "client_certificate", "client_key"
            ):
                getattr(self, field_name).setText(getattr(profile, field_name))
            self.read_only.setChecked(profile.read_only)
            self.base_dn.setEditText(profile.base_dn)
        self.authentication_mode.currentIndexChanged.connect(self._update_authentication_fields)
        self._update_authentication_fields()
        for field in self.findChildren(QLineEdit):
            if field is not self.base_dn.lineEdit():
                field.textChanged.connect(self._settings_changed)
        for field in (self.transport, self.authentication_mode):
            field.currentIndexChanged.connect(self._settings_changed)

    def _settings_changed(self) -> None:
        self.test_status.setText("Settings changed. Run the tests again before saving.")
        selected_dn = self.base_dn.currentText()
        with QSignalBlocker(self.base_dn):
            self.base_dn.clear()
            self.base_dn.setEditText(selected_dn)
        endpoint = (self.host.text().strip(), self.port.text(), self.transport.currentData(),
                    self.ca_certificate.text().strip())
        if self._trusted_endpoint and endpoint != (
            self._trusted_endpoint[0], str(self._trusted_endpoint[1]),
            self._trusted_endpoint[2], self._trusted_endpoint[3],
        ):
            self.trusted_certificate = None
            self._trusted_endpoint = None

    def _update_authentication_fields(self) -> None:
        simple = self.authentication_mode.currentData() == "simple"
        self.bind_dn.setEnabled(simple)
        self.password.setEnabled(simple)
        self.show_password.setEnabled(simple)
        self.save_password.setEnabled(simple)

    def _accept_save(self) -> None:
        self.save_as = False
        self.accept()

    def _accept_save_as(self) -> None:
        self.save_as = True
        self.accept()

    def accept(self) -> None:
        if self._test_worker is not None:
            return
        try:
            profile = self._profile_from_fields()
            if self.save_as:
                profile = replace(
                    profile, profile_id=str(uuid4()),
                    tofu_fingerprint=profile.tofu_fingerprint if self.trusted_certificate else "",
                )
            if any(
                existing.name.strip().casefold() == profile.name.strip().casefold()
                and existing.profile_id != profile.profile_id
                for existing in self._existing_profiles
            ):
                raise ValueError("A profile with this name already exists. Choose a different name.")
            self.profile = profile
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Invalid profile", str(error))
            return
        super().accept()

    def _profile_from_fields(self, for_test: bool = False) -> DirectoryProfile:
        if not for_test and not self.base_dn.currentText().strip():
            raise ValueError("A directory base DN is required.")
        profile = DirectoryProfile(
            name=self.name.text().strip() or ("Profile test" if for_test else ""),
            host=self.host.text().strip(),
            port=int(self.port.text()),
            transport=str(self.transport.currentData()),
            authentication_mode=str(self.authentication_mode.currentData()),
            bind_dn=self.bind_dn.text().strip(),
            base_dn=self.base_dn.currentText().strip(),
            ca_certificate=self.ca_certificate.text().strip(),
            client_certificate=self.client_certificate.text().strip(),
            client_key=self.client_key.text().strip(),
            read_only=self.read_only.isChecked(),
            profile_id=self._profile_id,
        )
        if self._original_profile:
            original = self._original_profile
            same_endpoint = (
                profile.host == original.host
                and profile.port == original.port
                and profile.transport == original.transport
                and profile.ca_certificate == original.ca_certificate
            )
            profile.tofu_fingerprint = original.tofu_fingerprint if same_endpoint else ""
        if self.trusted_certificate and self._trusted_endpoint == self._endpoint(profile):
            profile.tofu_fingerprint = certificate_details(
                self.trusted_certificate
            ).fingerprint_sha256
        return profile

    @staticmethod
    def _endpoint(profile: DirectoryProfile) -> tuple[str, int, str, str]:
        return profile.host, profile.port, profile.transport, profile.ca_certificate

    def _test_settings(self, authentication: bool) -> None:
        if self._test_worker is not None:
            return
        try:
            profile = (
                self._profile_from_fields(for_test=True) if authentication
                else DirectoryProfile(
                    name="Network test", host=self.host.text().strip(), port=int(self.port.text())
                )
            )
            password = self.password.text()
            if authentication and profile.authentication_mode == "simple" and not password:
                raise ValueError("Enter a bind password for simple authentication.")
        except (ValueError, TypeError) as error:
            self._test_failed(str(error))
            return
        passphrase = self.client_key_passphrase.text() or None
        staged_certificate = self.trusted_certificate

        def operation(cancelled: Event, progress: Callable[[int, str], None]) -> Any:
            if not authentication:
                return {
                    "message": LDAPService(profile, None).test_network(cancelled)
                }
            if profile.transport != "plain" and not profile.ca_certificate:
                certificate = LDAPService.inspect_server_certificate(
                    profile,
                    passphrase,
                    cancelled,
                )
                observed = certificate_details(certificate).fingerprint_sha256
                if observed.casefold() != profile.tofu_fingerprint.casefold():
                    return {"certificate": certificate}
            if cancelled.is_set():
                raise RuntimeError("Profile test cancelled.")
            if staged_certificate:
                with tempfile.TemporaryDirectory(prefix="datasifter-trust-") as directory:
                    trust = CertificateTrustStore(Path(directory))
                    draft = replace(profile, tofu_fingerprint="")
                    trust.accept_tofu(draft, staged_certificate)
                    service = LDAPService(draft, password, passphrase, trust)
                    message = service.test_authentication(cancelled)
                    base_dns = service.discover_base_dns(cancelled)
            else:
                service = LDAPService(profile, password, passphrase)
                message = service.test_authentication(cancelled)
                base_dns = service.discover_base_dns(cancelled)
            return {"message": message, "base_dns": base_dns}

        worker = DirectoryWorker(operation)
        self._test_worker = worker
        worker.signals.succeeded.connect(
            lambda result: self._test_succeeded(profile, result)
        )
        worker.signals.failed.connect(self._test_failed)
        worker.signals.finished.connect(self._test_finished)
        for field in self.findChildren(QLineEdit):
            field.setEnabled(False)
        self.show_password.setEnabled(False)
        self.save_password.setEnabled(False)
        self.transport.setEnabled(False)
        self.authentication_mode.setEnabled(False)
        self.base_dn.setEnabled(False)
        self.test_network_button.setEnabled(False)
        self.test_authentication_button.setEnabled(False)
        self.cancel_test_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        if self.save_as_button is not None:
            self.save_as_button.setEnabled(False)
        self.test_status.setText("Testing authentication..." if authentication else "Testing TCP...")
        QThreadPool.globalInstance().start(worker)

    def _test_succeeded(self, profile: DirectoryProfile, result: dict[str, Any]) -> None:
        certificate = result.get("certificate")
        if certificate:
            try:
                if not confirm_certificate_trust(self, profile, certificate):
                    self.test_status.setText("Certificate not trusted. Authentication not tested.")
                    return
                self.trusted_certificate = certificate
                self._trusted_endpoint = self._endpoint(profile)
                self.test_status.setText(
                    "Certificate accepted for this draft. Click Test authentication again to bind."
                )
            except CertificateTrustError as error:
                self._test_failed(str(error))
            return
        message = result["message"]
        if "base_dns" in result:
            selected_dn = self.base_dn.currentText()
            self.base_dn.clear()
            self.base_dn.addItems(result["base_dns"])
            if selected_dn:
                self.base_dn.setEditText(selected_dn)
            elif result["base_dns"]:
                self.base_dn.setCurrentIndex(0)
            if result["base_dns"]:
                message += " Select a Base DN from the dropdown."
            else:
                message += " The server advertised no Base DNs; enter one manually."
        self.test_status.setText(message)
        QMessageBox.information(self, "Profile test", message)

    def _test_failed(self, message: str) -> None:
        if self._test_worker and self._test_worker.cancelled.is_set():
            self.test_status.setText("Profile test cancelled.")
            return
        self.test_status.setText(f"Test failed: {message}")
        logger.warning("Profile diagnostic failed")
        QMessageBox.warning(self, "Profile test failed", message)

    def _test_finished(self) -> None:
        self._test_worker = None
        for field in self.findChildren(QLineEdit):
            field.setEnabled(True)
        self.transport.setEnabled(True)
        self.authentication_mode.setEnabled(True)
        self.base_dn.setEnabled(True)
        self._update_authentication_fields()
        self.test_network_button.setEnabled(True)
        self.test_authentication_button.setEnabled(True)
        self.cancel_test_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        if self.save_as_button is not None:
            self.save_as_button.setEnabled(True)

    def _cancel_test(self) -> None:
        if self._test_worker:
            self._test_worker.cancel()
            self.cancel_test_button.setEnabled(False)
            self.test_status.setText("Cancellation requested. Wait for the test to finish.")

    def reject(self) -> None:
        if self._test_worker:
            self._cancel_test()
            return
        super().reject()

    def closeEvent(self, event: Any) -> None:
        if self._test_worker:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)

    def _update_default_port(self, _index: int) -> None:
        if self.port.text() == str(self._default_port):
            self._default_port = 636 if self.transport.currentData() == "ldaps" else 389
            self.port.setText(str(self._default_port))


class ProfileWizard(ProfileDialog):
    """Guide new profile creation while reusing profile diagnostics and validation."""

    def __init__(
        self, parent: QWidget | None = None, existing_profiles: Sequence[DirectoryProfile] = ()
    ) -> None:
        super().__init__(parent, existing_profiles=existing_profiles)
        self.setWindowTitle("New directory connection")
        form = self.layout()
        assert isinstance(form, QFormLayout)
        self.pages = QStackedWidget()
        groups = (
            (
                self.name, self.host, self.port, self.transport, self.plain_warning,
                self.authentication_mode, self.bind_dn, self.password, self.password_options,
            ),
            (
                self.test_network_button, self.ca_certificate, self.client_certificate,
                self.client_key, self.client_key_passphrase, self.test_authentication_button,
            ),
            (self.base_dn, self.read_only),
        )
        for group in groups:
            page = QWidget()
            page_form = QFormLayout(page)
            for item in group:
                row = form.takeRow(item)
                assert row.fieldItem is not None
                label = row.labelItem.widget() if row.labelItem is not None else None
                field_widget = row.fieldItem.widget()
                field_layout = row.fieldItem.layout()
                if field_widget is not None:
                    if label is not None:
                        page_form.addRow(label, field_widget)
                    else:
                        page_form.addRow(field_widget)
                elif field_layout is not None:
                    if label is not None:
                        page_form.addRow(label, field_layout)
                    else:
                        page_form.addRow(field_layout)
                else:
                    raise RuntimeError(f"Unable to move profile field {item!r} to the wizard.")
            self.pages.addWidget(page)
        self.step_label = QLabel()
        form.insertRow(0, self.step_label)
        form.insertRow(1, self.pages)
        self.back_button = self.buttons.addButton("Back", QDialogButtonBox.ButtonRole.ActionRole)
        self.next_button = self.buttons.addButton("Next", QDialogButtonBox.ButtonRole.ActionRole)
        self.back_button.clicked.connect(lambda: self._show_step(self.pages.currentIndex() - 1))
        self.next_button.clicked.connect(lambda: self._show_step(self.pages.currentIndex() + 1))
        self._show_step(0)

    def _show_step(self, index: int) -> None:
        if self._test_worker is not None or not 0 <= index < self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        titles = ("Connection details", "Security and connection checks", "Directory scope")
        self.step_label.setText(f"Step {index + 1} of {len(titles)}: {titles[index]}")
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < self.pages.count() - 1)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setVisible(
            index == self.pages.count() - 1
        )

    def accept(self) -> None:
        if self.pages.currentIndex() != self.pages.count() - 1:
            return
        super().accept()

    def _test_settings(self, authentication: bool) -> None:
        super()._test_settings(authentication)
        if self._test_worker is not None:
            self.back_button.setEnabled(False)
            self.next_button.setEnabled(False)

    def _test_finished(self) -> None:
        super()._test_finished()
        self.next_button.setEnabled(True)
        self._show_step(self.pages.currentIndex())


class CommandReviewDialog(QDialog):
    def __init__(
        self, command: str, parent: QWidget | None = None, summary: str = ""
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Data Lens search")
        self.resize(560, 220)
        layout = QVBoxLayout(self)
        self.command = QLineEdit(command)
        self.summary = QLabel(summary)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.validation = QLabel()
        self.execute_button = QPushButton("Execute")
        self.cancel_button = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.execute_button)
        buttons.addWidget(self.cancel_button)
        layout.addWidget(QLabel("Review and edit the validated LDAP search before execution:"))
        if summary:
            layout.addWidget(self.summary)
        layout.addWidget(self.command)
        layout.addWidget(self.validation)
        layout.addLayout(buttons)
        self.execute_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.command.textChanged.connect(self._validate)
        self._validate(command)

    def _validate(self, command: str) -> None:
        try:
            validate_filter(command)
        except FilterSyntaxError as error:
            self.validation.setText(str(error))
            self.execute_button.setEnabled(False)
        else:
            self.validation.setText("Filter syntax is valid.")
            self.execute_button.setEnabled(True)

    def reviewed_command(self) -> str:
        return self.command.text().strip()


class SuggestedQueryDialog(QDialog):
    def __init__(self, filter_expression: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggested LDAP query")
        self.resize(560, 190)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Data Lens could not interpret the request. This LDAP filter was derived "
                "from its simple search wording; review it before use."
            )
        )
        self.filter_edit = QLineEdit(filter_expression)
        self.filter_edit.setReadOnly(True)
        layout.addWidget(self.filter_edit)
        self.instructions = QLabel(
            "To use it manually, copy the filter, paste it into the Directory Workbench "
            "LDAP filter field, and click Search."
        )
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)
        buttons = QHBoxLayout()
        self.try_button = QPushButton("Try updated query")
        self.copy_button = QPushButton("Copy filter")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.try_button)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.try_button.clicked.connect(self.accept)
        self.copy_button.clicked.connect(self._copy_filter)
        self.close_button.clicked.connect(self.reject)

    def _copy_filter(self) -> None:
        QGuiApplication.clipboard().setText(self.filter_edit.text())
        self.instructions.setText(
            "Filter copied. Paste it into the Directory Workbench LDAP filter field and click Search."
        )


class MutationReviewDialog(QDialog):
    def __init__(self, preview: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Data Lens change")
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Review the complete proposed change before applying it:"))
        details = QPlainTextEdit(preview)
        details.setReadOnly(True)
        layout.addWidget(details)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply change")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CopyableTextDialog(QDialog):
    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(text)
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AttributeValueDelegate(QStyledItemDelegate):
    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QPlainTextEdit:
        editor = QPlainTextEdit(parent)
        editor.setMinimumHeight(80)
        editor.setToolTip(
            "Enter or Tab saves this value (Tab moves to the next editable value, "
            "Shift+Tab to the previous). Shift+Enter adds another value line."
        )
        return editor

    def eventFilter(self, editor: QObject, event: QEvent) -> bool:
        if (
            isinstance(editor, QPlainTextEdit)
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            if (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
                return True
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                backwards = (
                    event.key() == Qt.Key.Key_Backtab
                    or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                )
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
                self._edit_adjacent_value(-1 if backwards else 1)
                return True
        return super().eventFilter(editor, event)

    def _edit_adjacent_value(self, step: int) -> None:
        view = self.parent()
        if not isinstance(view, QTableWidget):
            return
        row = view.currentRow() + step
        while 0 <= row < view.rowCount():
            item = view.item(row, 1)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable:
                view.setCurrentCell(row, 1)
                view.editItem(item)
                return
            row += step

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex
    ) -> None:
        if isinstance(editor, QPlainTextEdit):
            model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)


class MainWindow(QMainWindow):
    def __init__(
        self,
        store: ConfigurationStore | None = None,
        vault: CredentialVault | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"DataSifter 2.0 - v{__version__} ({build_label()})")
        self.resize(1400, 860)
        self.store = store or ConfigurationStore()
        self.vault = vault or CredentialVault()
        self.configuration = self.store.load()
        self.trust_store = CertificateTrustStore()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[DirectoryWorker] = set()
        self._current_worker: DirectoryWorker | None = None
        self._connection_epoch = 0
        self._active_profile: DirectoryProfile | None = None
        self._connecting = False
        self._session_passwords: dict[str, str] = {}
        self._loaded_model: tuple[str, str, LlamaCppFilterGenerator] | None = None
        self._model_lock = Lock()
        self._schema_names: dict[str, tuple[list[str], list[str]]] = {}
        self._known_object_classes: list[str] = []
        self._known_attributes: list[str] = []
        self._session_require_confirmation: bool | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._schema_options: dict[str, Any] = {}
        self.log_handler = QtLogHandler()
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        self.log_handler.emitter.message.connect(self._append_log_message)
        logger.addHandler(self.log_handler)
        logger.setLevel(logging.INFO)
        self._build_ui()
        self._refresh_profiles()
        self._refresh_connection_controls()

    def _build_ui(self) -> None:
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.profiles = QComboBox()
        self.list_profiles_button = QPushButton("List profiles")
        self.add_profile_button = QPushButton("Add profile")
        self.edit_profile_button = QPushButton("Edit profile")
        self.delete_profile_button = QPushButton("Delete profile")
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        self.refresh_tree_button = QPushButton("Refresh tree")
        self.refresh_tree_button.setEnabled(False)
        self.refresh_tree_button.setToolTip(
            "Refresh the highlighted entry's subtree, or the directory root if none is highlighted."
        )
        self.cancel_button = QPushButton("Cancel active operations")
        self.cancel_button.setEnabled(False)
        self.list_profiles_button.clicked.connect(self._list_profiles)
        self.add_profile_button.clicked.connect(self._add_profile)
        self.edit_profile_button.clicked.connect(self._edit_profile)
        self.delete_profile_button.clicked.connect(self._delete_profile)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.refresh_tree_button.clicked.connect(self._refresh_tree)
        self.cancel_button.clicked.connect(self._cancel_operation)
        toolbar_layout.addWidget(QLabel("Directory"))
        toolbar_layout.addWidget(self.profiles, 1)
        toolbar_layout.addWidget(self.list_profiles_button)
        toolbar_layout.addWidget(self.add_profile_button)
        toolbar_layout.addWidget(self.edit_profile_button)
        toolbar_layout.addWidget(self.delete_profile_button)
        toolbar_layout.addWidget(self.connect_button)
        toolbar_layout.addWidget(self.disconnect_button)
        toolbar_layout.addWidget(self.refresh_tree_button)
        toolbar_layout.addWidget(self.cancel_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.warning_banner = QLabel(
            "Self-signed certificates are recommended for non-production environments only."
        )
        self.warning_banner.setStyleSheet("background: #fff3cd; padding: 6px;")
        self.warning_banner.setVisible(False)
        self.transport_warning = QLabel()
        self.transport_warning.setStyleSheet("background: #fff3cd; padding: 6px;")
        self.transport_warning.setVisible(False)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Directory Information Tree")
        self.tree.itemClicked.connect(self._select_entry)
        self.tree.itemExpanded.connect(self._expand_directory_node)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)

        self.attributes = QTableWidget(0, 2)
        self.attributes.setHorizontalHeaderLabels(["Attribute", "Values"])
        self.attributes.horizontalHeader().setStretchLastSection(True)
        self.attributes.setWordWrap(True)
        self.attributes.setItemDelegateForColumn(1, AttributeValueDelegate(self.attributes))
        self.attributes.setSortingEnabled(True)
        self.attributes.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.attributes.itemChanged.connect(lambda _item: self.attributes.resizeRowsToContents())
        self.attributes.itemChanged.connect(lambda _item: self._update_save_enabled())
        self.attributes.horizontalHeader().sectionResized.connect(
            lambda *_args: self.attributes.resizeRowsToContents()
        )
        # Enable custom context menu for attribute values
        self.attributes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.attributes.customContextMenuRequested.connect(self._attributes_context_menu)
        self.dn_field = QLineEdit()
        self.dn_field.setReadOnly(True)
        self.dn_field.setPlaceholderText("Select an entry to show its distinguished name")
        self.dn_field.setToolTip("Distinguished name of the selected entry (select or copy)")
        self.copy_dn_button = QPushButton("Copy DN")
        self.copy_dn_button.setEnabled(False)
        self.copy_dn_button.clicked.connect(self._copy_dn)
        self._selected_item: QTreeWidgetItem | None = None
        self._selected_dn: str | None = None
        self._search_attributes: set[str] = set()
        self._original_attributes: dict[str, list[Any]] = {}
        self._readonly_attribute_names: set[str] = set()
        self.operational_toggle = QCheckBox("Show operational attributes")
        self.operational_toggle.stateChanged.connect(self._reload_selected_entry)
        self.add_entry_button = QPushButton("Add entry")
        self.add_attribute_button = QPushButton("Add attribute")
        self.save_entry_button = QPushButton("Save attribute changes")
        self.delete_entry_button = QPushButton("Delete entry")
        self.move_entry_button = QPushButton("Rename / move")
        self.password_button = QPushButton("Change password")
        self.add_entry_button.clicked.connect(self._add_entry)
        self.add_attribute_button.clicked.connect(self._add_attribute)
        self.save_entry_button.clicked.connect(self._save_entry_changes)
        self.delete_entry_button.clicked.connect(self._delete_entry)
        self.move_entry_button.clicked.connect(self._move_entry)
        self.password_button.clicked.connect(self._change_password)
        self._set_mutations_enabled(False)

        self.filter_edit = QPlainTextEdit(_DEFAULT_LDAP_FILTER)
        self.filter_edit.setPlaceholderText("RFC 4515 LDAP filter")
        self.filter_edit.setMaximumHeight(58)
        self.filter_highlighter = LDAPFilterHighlighter(self.filter_edit.document())
        self.filter_execute = QPushButton("Search")
        self.filter_execute.clicked.connect(self._execute_filter)
        self.filter_clear = QPushButton("Clear filter")
        self.filter_clear.setToolTip("Reset the filter and search for all entries.")
        self.filter_clear.clicked.connect(self._clear_filter)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.filter_execute)
        filter_row.addWidget(self.filter_clear)

        self.ai_request = LensRequestEdit()
        self.ai_request.setPlaceholderText(
            "Describe a directory search or requested change, or manage the session: "
            "connect to <profile>, switch to <profile>, disconnect, list targets, current target."
        )
        self.ai_request.setToolTip("Enter: run request. Shift+Enter: new line.")
        self.ai_request.submitted.connect(
            lambda: self.ai_generate.click() if self.ai_generate.isEnabled() else None
        )
        self.ai_request.textChanged.connect(self._refresh_lens_controls)
        self.ai_request.setMaximumHeight(90)
        self.ai_generate = QPushButton("Run request")
        self.ai_generate.clicked.connect(self._generate_filter)
        self.ai_output = QPlainTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setPlaceholderText(
            "Reviewed search proposals, mutation dry-run previews, clarifications, and session "
            "command results appear here."
        )
        self.ai_output.setMaximumHeight(76)
        self.show_ai_explanation = QCheckBox("Show AI explanation")
        self.ai_explanation = QPlainTextEdit()
        self.ai_explanation.setReadOnly(True)
        self.ai_explanation.setPlaceholderText(
            "The validated interpretation and resolution steps will appear here."
        )
        self.ai_explanation.setMaximumHeight(140)
        self.ai_explanation.setVisible(False)
        self.show_ai_explanation.toggled.connect(self.ai_explanation.setVisible)
        self.model_status_label = QLabel()
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.ai_panel = QWidget()
        ai_layout = QVBoxLayout(self.ai_panel)
        ai_layout.addWidget(QLabel("Data Lens"))
        ai_layout.addWidget(self.ai_request)
        ai_layout.addWidget(self.ai_generate)
        ai_layout.addWidget(self.ai_output)
        ai_layout.addWidget(self.show_ai_explanation)
        ai_layout.addWidget(self.ai_explanation)
        ai_layout.addWidget(self.model_status)
        self._refresh_model_status()

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(toolbar)
        left_layout.addWidget(self.progress)
        left_layout.addWidget(self.warning_banner)
        left_layout.addWidget(self.transport_warning)
        left_layout.addWidget(self.tree, 1)
        left_layout.addLayout(filter_row)
        left_layout.addWidget(self.ai_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self.operational_toggle)
        dn_row = QHBoxLayout()
        dn_row.addWidget(QLabel("Distinguished name"))
        dn_row.addWidget(self.dn_field, 1)
        dn_row.addWidget(self.copy_dn_button)
        right_layout.addLayout(dn_row)
        mutation_row = QHBoxLayout()
        for button in (
            self.add_entry_button,
            self.add_attribute_button,
            self.save_entry_button,
            self.delete_entry_button,
            self.move_entry_button,
            self.password_button,
        ):
            mutation_row.addWidget(button)
        right_layout.addLayout(mutation_row)
        right_layout.addWidget(self.attributes, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        schema_panel = QWidget()
        schema_layout = QVBoxLayout(schema_panel)
        schema_toolbar = QHBoxLayout()
        self.schema_refresh_button = QPushButton("Load directory schema")
        self.schema_refresh_button.clicked.connect(self._load_schema)
        self.schema_status = QLabel("Connect, then load the directory schema.")
        schema_toolbar.addWidget(self.schema_refresh_button)
        schema_toolbar.addWidget(self.schema_status, 1)
        self.schema_table = QTableWidget(0, 3)
        self.schema_table.setHorizontalHeaderLabels(["Kind", "Name", "Schema details"])
        self.schema_table.horizontalHeader().setStretchLastSection(True)
        schema_filters = QHBoxLayout()
        self.schema_object_class = QComboBox()
        self.schema_object_class.addItem("All object classes", "")
        self.schema_object_class.currentIndexChanged.connect(self._filter_schema)
        self.schema_attribute_search = QLineEdit()
        self.schema_attribute_search.setPlaceholderText("Search attributes as you type...")
        self.schema_attribute_search.setClearButtonEnabled(True)
        self.schema_attribute_search.textChanged.connect(self._filter_schema)
        schema_filters.addWidget(QLabel("objectClass"))
        schema_filters.addWidget(self.schema_object_class)
        schema_filters.addWidget(self.schema_attribute_search, 1)
        schema_layout.addLayout(schema_toolbar)
        schema_layout.addLayout(schema_filters)
        schema_layout.addWidget(self.schema_table)
        prompts_panel = QWidget()
        prompts_layout = QVBoxLayout(prompts_panel)
        prompts_toolbar = QHBoxLayout()
        prompts_toolbar.addWidget(
            QLabel(
                "Requests and the exact prompts sent to the local model. Prompts contain your "
                "request and schema names only, never entry values, and are kept in memory "
                "for this session."
            ),
            1,
        )
        self.clear_prompts_button = QPushButton("Clear")
        self.clear_prompts_button.clicked.connect(self._clear_prompt_log)
        prompts_toolbar.addWidget(self.clear_prompts_button)
        self.ai_prompt_log = QPlainTextEdit()
        self.ai_prompt_log.setReadOnly(True)
        self.ai_prompt_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.ai_prompt_log.setPlaceholderText("Generate a local filter to see the prompt here.")
        prompts_layout.addLayout(prompts_toolbar)
        prompts_layout.addWidget(self.ai_prompt_log)
        tabs = QTabWidget()
        tabs.addTab(splitter, "Directory Workbench")
        tabs.addTab(schema_panel, "Schema inspector")
        tabs.addTab(self.log_output, "Protocol log")
        self.data_lens_tab = DataLensTab(parent=self)
        self.data_lens_tab.rewrite_command = self._rewrite_directory_command
        tabs.addTab(prompts_panel, "Data Lens log")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("No directory connected")
        self.statusBar().addPermanentWidget(self.model_status_label)
        self.version_label = QLabel(f"Version {__version__}")
        self.statusBar().addPermanentWidget(self.version_label)
        self._update_model_status_bar()

        directory_menu = self.menuBar().addMenu("Directory")
        self.import_ldif_action = directory_menu.addAction(
            "Import LDIF change records...", self._import_ldif
        )
        self.export_ldif_action = directory_menu.addAction(
            "Export selected entry as LDIF...", self._export_selected_ldif
        )
        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction("Application settings...", self._open_settings)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("Data Lens help...", self._open_data_lens_help)
        help_menu.addAction("About DataSifter...", self._open_about)
        lens_menu = settings_menu.addMenu("Data Lens")
        settings_action = lens_menu.addAction(
            "Review filters before execution (global)"
        )
        self.ai_confirmation_action = settings_action
        settings_action.setCheckable(True)
        settings_action.setChecked(self.configuration.preferences.require_ai_confirmation)
        settings_action.toggled.connect(self._set_confirmation_preference)
        self.session_confirmation_action = lens_menu.addAction(
            "Require confirmation this session"
        )
        self.session_confirmation_action.setCheckable(True)
        self.session_confirmation_action.setChecked(
            self.configuration.preferences.require_ai_confirmation
        )
        self.session_confirmation_action.toggled.connect(self._set_session_confirmation)
        lens_menu.addAction("Choose local GGUF model…", self._choose_model)
        lens_menu.addAction("Download and select GGUF models...", self._manage_models)
        safety_menu = settings_menu.addMenu("Directory safety")
        self.directory_confirmation_actions = {}
        for operation, label in (
            ("delete", "Confirm before deleting entries"),
            ("rename", "Confirm before renaming or moving entries"),
        ):
            action = safety_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(getattr(
                self.configuration.preferences, f"require_{operation}_confirmation"
            ))
            action.toggled.connect(
                lambda enabled, operation=operation: self._set_directory_confirmation(
                    operation, enabled
                )
            )
            self.directory_confirmation_actions[operation] = action
        appearance_menu = settings_menu.addMenu("Appearance")
        self.highlight_search_action = appearance_menu.addAction(
            "Highlight searched attributes"
        )
        self.highlight_search_action.setCheckable(True)
        self.highlight_search_action.setChecked(
            self.configuration.preferences.highlight_search_attributes
        )
        self.highlight_search_action.toggled.connect(self._set_search_highlight_preference)
        self.ai_panel.setVisible(self.configuration.preferences.ai_enabled)
        self._refresh_lens_controls()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.configuration.preferences, self)
        self._settings_dialog = dialog
        dialog.choose_model_requested.connect(self._choose_model)
        dialog.manage_models_requested.connect(self._manage_models)
        dialog.load_model_requested.connect(self._load_model)
        dialog.save_requested.connect(lambda: self._save_settings(dialog))
        self._refresh_model_status()
        try:
            dialog.exec()
        finally:
            self._settings_dialog = None
            dialog.deleteLater()

    def _open_about(self) -> None:
        try:
            dialog = AboutDialog(self)
        except OSError as error:
            logger.exception("Could not load About dialog documents")
            QMessageBox.critical(self, "About DataSifter", str(error))
            return
        dialog.exec()

    def _save_settings(self, dialog: SettingsDialog) -> None:
        if self._workers:
            QMessageBox.information(
                dialog, "Operations active", "Wait for active operations before saving settings."
            )
            return
        previous = self.configuration.preferences
        preferences = replace(
            previous,
            ai_enabled=dialog.ai_enabled.isChecked(),
            require_ai_confirmation=dialog.ai_confirmation.isChecked(),
            require_delete_confirmation=dialog.delete_confirmation.isChecked(),
            require_rename_confirmation=dialog.rename_confirmation.isChecked(),
            highlight_search_attributes=dialog.highlight_search.isChecked(),
        )
        self.configuration.preferences = preferences
        try:
            self.store.save(self.configuration)
        except ConfigurationError as error:
            self.configuration.preferences = previous
            QMessageBox.critical(dialog, "Settings not saved", str(error))
            return
        for action, value in (
            (self.ai_confirmation_action, preferences.require_ai_confirmation),
            (self.highlight_search_action, preferences.highlight_search_attributes),
            (self.directory_confirmation_actions["delete"], preferences.require_delete_confirmation),
            (self.directory_confirmation_actions["rename"], preferences.require_rename_confirmation),
        ):
            with QSignalBlocker(action):
                action.setChecked(value)
        if self._session_require_confirmation is None:
            with QSignalBlocker(self.session_confirmation_action):
                self.session_confirmation_action.setChecked(preferences.require_ai_confirmation)
        if not preferences.ai_enabled:
            self._loaded_model = None
        self.ai_panel.setVisible(preferences.ai_enabled)
        if previous.ai_enabled != preferences.ai_enabled:
            self._log_lens_event(
                "Data Lens enabled" if preferences.ai_enabled else "Data Lens disabled"
            )
        self._apply_search_highlight()
        self._refresh_model_status()
        dialog.accept()

    def _refresh_lens_controls(self) -> None:
        if hasattr(self, "tabs"):
            self._refresh_connection_controls()
        preferences = self.configuration.preferences
        request = self.ai_request.toPlainText().strip()
        session_command = parse_session_command(request)
        approved = self._model_is_approved()
        reason = ""
        if session_command is not None:
            if not preferences.ai_enabled:
                reason = "Enable Data Lens in Application settings."
            elif self._workers and session_command.action != DISCONNECT:
                reason = "Wait for active operations to finish."
            elif session_command.action in (COUNT_PEOPLE, MONITOR) and (
                self._active_profile is None or self._connecting
            ):
                reason = "Connect to a directory first."
        elif not preferences.ai_enabled:
            reason = "Enable Data Lens in Application settings."
        elif self._workers:
            reason = "Wait for active operations to finish."
        elif self._active_profile is None or self._connecting:
            reason = "Connect to a directory first."
        elif not self.ai_request.toPlainText().strip():
            reason = "Describe a directory search or change to propose."
        self.ai_generate.setText("Run command" if session_command is not None else "Run request")
        self.ai_generate.setEnabled(not reason)
        action_hint = (
            "Run the Data Lens session command (Enter)"
            if session_command is not None
            else (
                "Translate the request to a validated intent. Searches follow the review setting; "
                "changes require explicit confirmation after preview."
            )
        )
        self.ai_generate.setToolTip(reason or action_hint)
        self.ai_request.setEnabled(preferences.ai_enabled)
        if self._settings_dialog is not None:
            self._settings_dialog.set_busy(
                bool(self._workers), preferences.ai_enabled, approved
            )

    def _model_is_approved(self) -> bool:
        preferences = self.configuration.preferences
        return bool(
            preferences.model_path and preferences.model_sha256 and preferences.model_provenance
            and Path(preferences.model_path).suffix.casefold() == ".gguf"
        )

    def _model_is_loaded(self) -> bool:
        preferences = self.configuration.preferences
        return self._loaded_model is not None and self._loaded_model[:2] == (
            preferences.model_path, preferences.model_sha256
        )

    def _confirm_model_start(self) -> bool:
        if not self._model_is_approved():
            response = QMessageBox.question(
                self, "Local model unavailable",
                "No approved local GGUF model is selected. Open Application settings "
                "to select and approve one?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response == QMessageBox.StandardButton.Yes:
                self._open_settings()
            return False
        response = QMessageBox.question(
            self, "Start local model?",
            f"The local model {Path(self.configuration.preferences.model_path).name} "
            "is not loaded. Load it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return response == QMessageBox.StandardButton.Yes

    def _prompt_model_on_startup(self) -> None:
        if (
            self.isVisible() and self.configuration.preferences.ai_enabled
            and not self._model_is_loaded() and not self._workers
            and self._confirm_model_start()
        ):
            self._load_model()

    def _refresh_connection_controls(self) -> None:
        connected = self._active_profile is not None and not self._connecting
        self.filter_edit.setEnabled(connected)
        self.filter_execute.setEnabled(connected)
        self.filter_clear.setEnabled(connected and not self._workers)
        self.schema_refresh_button.setEnabled(connected)
        self.refresh_tree_button.setEnabled(connected and not self._workers)
        profile = self._active_profile
        self.import_ldif_action.setEnabled(
            connected and profile is not None and not profile.read_only and not self._workers
        )
        self.export_ldif_action.setEnabled(
            connected and self._selected_dn is not None and not self._workers
        )

    def _import_ldif(self) -> None:
        profile = self._active_profile
        if profile is None or profile.read_only or self._workers:
            return
        if self._has_unsaved_changes():
            QMessageBox.information(
                self,
                "Save attribute changes first",
                "Save or discard the selected entry's pending changes before importing LDIF.",
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import LDIF change records",
            "",
            "LDIF files (*.ldif *.LDIF);;All files (*)",
        )
        if not path:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def validate_file(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> list[LDIFRecord]:
            if cancelled.is_set():
                return []
            records = read_ldif(path)
            if cancelled.is_set():
                raise ValueError("LDIF validation was cancelled; no directory changes were sent.")
            return records

        def preview(records: list[LDIFRecord]) -> None:
            if not records:
                QMessageBox.information(self, "Empty LDIF file", "The file contains no records.")
                return
            self._confirm_ldif_import(profile, password, records)

        self._run_worker(
            validate_file,
            preview,
            lambda message: QMessageBox.critical(self, "LDIF validation failed", message),
        )

    def _confirm_ldif_import(
        self,
        profile: DirectoryProfile,
        password: str | None,
        records: list[LDIFRecord],
    ) -> None:
        if self._active_profile is None or self._active_profile.profile_id != profile.profile_id:
            return
        lines: list[str] = []
        for index, record in enumerate(records, start=1):
            if record.change_type == "add":
                details = ", ".join(record.attributes)
            elif record.change_type == "modify":
                details = ", ".join(
                    f"{item.operation} {item.attribute}" for item in record.modifications
                )
            elif record.change_type == "modrdn":
                details = (
                    f"{record.new_rdn or ''}, delete old RDN: "
                    f"{'yes' if record.delete_old_rdn else 'no'}"
                )
                if record.new_superior:
                    details += f", superior {record.new_superior}"
            else:
                details = ""
            summary = f"{index}. {record.change_type.upper()} {record.dn}"
            if details:
                summary += f" — {details}"
            lines.append(summary)

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Confirm LDIF import")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"Apply {len(records)} validated LDIF change records?")
        dialog.setInformativeText(
            "Records will be sent in file order. If the directory rejects a later record, "
            "earlier records or earlier sub-operations in a modify record may already have "
            "been applied and cannot be rolled back as a batch."
        )
        dialog.setDetailedText("\n".join(lines))
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if dialog.exec() != QMessageBox.StandardButton.Apply:
            return

        def apply_records(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> int:
            return self._service(profile, password).apply_ldif_records(
                records,
                cancelled=cancelled,
                progress=progress,
            )

        completed = {"refresh": False}

        def report_success(count: int) -> None:
            completed["refresh"] = True
            self.statusBar().showMessage(f"Imported {count} LDIF change records")

        def report_failure(message: str) -> None:
            completed["refresh"] = True
            QMessageBox.critical(self, "LDIF import failed", message)

        def refresh_directory() -> None:
            if (
                completed["refresh"]
                and self._active_profile is not None
                and self._active_profile.profile_id == profile.profile_id
            ):
                self._connect_profile(profile)

        self._run_worker(
            apply_records,
            report_success,
            report_failure,
            on_finished=refresh_directory,
        )

    def _export_selected_ldif(self) -> None:
        profile = self._active_profile
        distinguished_name = self._selected_dn
        if profile is None or not distinguished_name or self._workers:
            return
        if self._has_unsaved_changes():
            QMessageBox.information(
                self,
                "Save attribute changes first",
                "Save the selected entry's pending changes before exporting it.",
            )
            return
        if QMessageBox.question(
            self,
            "Export directory entry",
            "Export all attributes returned for this entry? The file may contain sensitive "
            "directory data; store and share it carefully.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export selected entry as LDIF",
            "directory-entry.ldif",
            "LDIF files (*.ldif);;All files (*)",
        )
        if not path:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def export_entry(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> str:
            entry = self._service(profile, password).read_entry(
                distinguished_name,
                cancelled=cancelled,
            )
            if cancelled.is_set():
                raise LDAPOperationError("LDIF export cancelled before the file was written.")
            write_ldif(
                path,
                [
                    LDIFRecord(
                        dn=distinguished_name,
                        change_type="add",
                        attributes=entry.get("attributes", {}),
                    )
                ],
            )
            return path

        self._run_worker(
            export_entry,
            lambda exported_path: self.statusBar().showMessage(
                f"Exported selected entry to {exported_path}"
            ),
            lambda message: QMessageBox.critical(self, "LDIF export failed", message),
        )

    def _log_lens_event(self, message: str) -> None:
        self.ai_prompt_log.appendPlainText(
            f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n{message}\n"
        )

    def _refresh_profiles(self) -> None:
        self.profiles.clear()
        for profile in self.configuration.profiles:
            self.profiles.addItem(profile.name, profile.profile_id)
        last_connected_index = self.profiles.findData(
            self.configuration.last_connected_profile_id
        )
        if last_connected_index >= 0:
            self.profiles.setCurrentIndex(last_connected_index)
        self.list_profiles_button.setEnabled(bool(self.configuration.profiles))
        self.delete_profile_button.setEnabled(bool(self.configuration.profiles) and not self._workers)

    def _list_profiles(self) -> None:
        profiles = self.configuration.profiles
        if not profiles:
            return
        QMessageBox.information(
            self, "Saved profiles", "\n".join(describe_target(profile) for profile in profiles)
        )

    def _selected_profile(self) -> DirectoryProfile | None:
        profile_id = self.profiles.currentData()
        return next(
            (profile for profile in self.configuration.profiles if profile.profile_id == profile_id),
            None,
        )

    def _profile_password(self, profile: DirectoryProfile) -> str | None:
        return (
            self.vault.get_password(profile)
            or self._session_passwords.get(profile.profile_id)
        )

    def _save_profile_password(
        self,
        profile: DirectoryProfile,
        password: str,
        remember: bool,
    ) -> None:
        self.vault.store_password(profile, password if remember else "")
        if remember or not password:
            self._session_passwords.pop(profile.profile_id, None)
        else:
            self._session_passwords[profile.profile_id] = password

    def _add_profile(self) -> None:
        dialog = ProfileWizard(self, self.configuration.profiles)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.profile is None:
            return
        profile = dialog.profile
        self.configuration.profiles.append(profile)
        try:
            self._save_profile_configuration(dialog, profile)
        except (ConfigurationError, CertificateTrustError) as error:
            self.configuration.profiles.remove(profile)
            QMessageBox.critical(self, "Profile not saved", str(error))
            return
        if profile.authentication_mode == "simple":
            try:
                self._save_profile_password(
                    profile,
                    dialog.password.text(),
                    dialog.save_password.isChecked(),
                )
            except ConfigurationError as error:
                QMessageBox.critical(
                    self,
                    "Profile saved without password",
                    f"The profile was saved, but its password could not be stored: {error}",
                )
        if dialog.client_key_passphrase.text():
            try:
                self.vault.store_client_key_passphrase(
                    profile,
                    dialog.client_key_passphrase.text(),
                )
            except ConfigurationError as error:
                QMessageBox.critical(
                    self,
                    "Profile saved without key passphrase",
                    f"The profile was saved, but its client-key passphrase was not stored: {error}",
                )
        self._refresh_profiles()
        self.profiles.setCurrentIndex(self.profiles.count() - 1)

    def _edit_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            QMessageBox.information(self, "No profile", "Add a directory profile first.")
            return
        if (
            self._active_profile is not None
            and self._active_profile.profile_id == profile.profile_id
            and not self._confirm_leave_entry()
        ):
            return
        dialog = ProfileDialog(self, profile, self.configuration.profiles)
        try:
            saved_password = self.vault.get_password(profile)
            dialog.password.setText(
                saved_password or self._session_passwords.get(profile.profile_id, "")
            )
            dialog.save_password.setChecked(saved_password is not None)
            dialog.client_key_passphrase.setText(
                self.vault.get_client_key_passphrase(profile) or ""
            )
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.profile is None:
            return
        index = self.configuration.profiles.index(profile)
        updated = dialog.profile
        if dialog.save_as:
            self.configuration.profiles.append(updated)
        else:
            self.configuration.profiles[index] = updated
        try:
            self._save_profile_configuration(dialog, updated)
        except (ConfigurationError, CertificateTrustError) as error:
            if dialog.save_as:
                self.configuration.profiles.remove(updated)
            else:
                self.configuration.profiles[index] = profile
            QMessageBox.critical(self, "Profile not saved", str(error))
            return
        try:
            if dialog.save_as:
                if updated.authentication_mode == "simple" and dialog.password.isModified():
                    self._save_profile_password(
                        updated, dialog.password.text(), dialog.save_password.isChecked()
                    )
                if dialog.client_key_passphrase.isModified():
                    self.vault.store_client_key_passphrase(
                        updated, dialog.client_key_passphrase.text()
                    )
            elif updated.authentication_mode == "simple":
                self._save_profile_password(
                    updated,
                    dialog.password.text(),
                    dialog.save_password.isChecked(),
                )
            else:
                self._save_profile_password(updated, "", False)
            if not dialog.save_as:
                self.vault.store_client_key_passphrase(
                    updated, dialog.client_key_passphrase.text()
                )
        except ConfigurationError as error:
            QMessageBox.critical(
                self, "Credential update failed",
                f"Profile settings were saved, but credentials were not fully updated: {error}",
            )
        if not dialog.save_as and self._active_profile and (
            self._active_profile.profile_id == updated.profile_id
        ):
            self.data_lens_tab.set_service(None)
            self._active_profile = None
            self._connecting = False
            self._selected_dn = None
            self.tree.clear()
            self.attributes.setRowCount(0)
            self._known_object_classes = []
            self._known_attributes = []
            self._schema_names.pop(updated.profile_id, None)
            self._set_mutations_enabled(False)
            self.disconnect_button.setEnabled(False)
            self._refresh_lens_controls()
            self.statusBar().showMessage("Profile updated. Connect again to browse the directory.")
        self._refresh_profiles()
        self.profiles.setCurrentIndex(self.profiles.findData(updated.profile_id))

    def _delete_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None or self._workers:
            return
        is_active = (
            self._active_profile is not None
            and self._active_profile.profile_id == profile.profile_id
        )
        active_notice = (
            " The active directory connection will be disconnected first."
            if is_active
            else ""
        )
        reply = QMessageBox.question(
            self,
            "Delete saved profile",
            f"Delete {profile.name} ({profile.host}:{profile.port})? This also removes its "
            f"saved credentials and trusted certificate.{active_notice} Directory data will "
            "not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if is_active:
            if not self._confirm_leave_entry():
                return
            self._disconnect()
            if self._active_profile is not None:
                return

        try:
            self.vault.delete_password(profile)
            self.vault.store_client_key_passphrase(profile, "")
            self.trust_store.delete_tofu(profile)
            index = self.configuration.profiles.index(profile)
            last_connected_profile_id = self.configuration.last_connected_profile_id
            self.configuration.profiles.pop(index)
            if last_connected_profile_id == profile.profile_id:
                self.configuration.last_connected_profile_id = None
            try:
                self.store.save(self.configuration)
            except ConfigurationError:
                self.configuration.profiles.insert(index, profile)
                self.configuration.last_connected_profile_id = last_connected_profile_id
                raise
        except (ConfigurationError, CertificateTrustError) as error:
            self._refresh_profiles()
            QMessageBox.critical(
                self,
                "Profile deletion incomplete",
                "The profile remains saved, but some associated credentials or certificate "
                f"trust data may already have been removed: {error}",
            )
            return

        self._session_passwords.pop(profile.profile_id, None)
        self._schema_names.pop(profile.profile_id, None)
        self._refresh_profiles()
        self.statusBar().showMessage(f"Deleted profile {profile.name}")

    def _save_dialog_trust(self, dialog: ProfileDialog, profile: DirectoryProfile) -> None:
        if dialog.trusted_certificate:
            draft = replace(profile, tofu_fingerprint="")
            self.trust_store.accept_tofu(draft, dialog.trusted_certificate)
            profile.tofu_fingerprint = draft.tofu_fingerprint

    def _save_profile_configuration(
        self, dialog: ProfileDialog, profile: DirectoryProfile
    ) -> None:
        if any(
            item.profile_id != profile.profile_id
            and item.name.strip().casefold() == profile.name.strip().casefold()
            for item in self.configuration.profiles
        ):
            raise ConfigurationError("A profile with this name already exists. Choose a different name.")
        if not dialog.trusted_certificate:
            self.store.save(self.configuration)
            return
        path = self.trust_store.certificate_path(profile)
        try:
            previous = path.read_bytes() if path.exists() else None
        except OSError as error:
            raise CertificateTrustError(f"Unable to read existing certificate: {error}") from error
        self._save_dialog_trust(dialog, profile)
        try:
            self.store.save(self.configuration)
        except ConfigurationError as error:
            try:
                if previous is None:
                    path.unlink()
                else:
                    self.trust_store.accept_tofu(
                        replace(profile, tofu_fingerprint=""), previous
                    )
            except (OSError, CertificateTrustError) as rollback_error:
                raise CertificateTrustError(
                    f"{error}; restoring certificate trust also failed: {rollback_error}"
                ) from rollback_error
            raise

    def _last_connected_profile(self) -> DirectoryProfile | None:
        profile_id = self.configuration.last_connected_profile_id
        return next(
            (profile for profile in self.configuration.profiles if profile.profile_id == profile_id),
            None,
        )

    def _default_profile(self) -> DirectoryProfile | None:
        profile = self._selected_profile()
        if profile is None:
            profile = self._last_connected_profile()
            if profile is not None:
                self.profiles.setCurrentIndex(self.profiles.findData(profile.profile_id))
        return profile

    def _connect(self) -> None:
        profile = self._default_profile()
        if profile is None:
            QMessageBox.information(self, "No profile", "Add a directory profile first.")
            return
        self._connect_profile(profile)

    def _connect_profile(self, profile: DirectoryProfile) -> None:
        if not self._confirm_leave_entry():
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        if profile.authentication_mode == "simple" and not password:
            password, accepted = QInputDialog.getText(
                self,
                "Directory password",
                f"Enter the bind password for {profile.name}:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return
            if not password:
                QMessageBox.warning(
                    self,
                    "Password required",
                    "Enter a bind password or cancel the connection.",
                )
                return
            self._session_passwords[profile.profile_id] = password
        self._active_profile = profile
        self._connecting = True
        self.disconnect_button.setEnabled(True)
        self.data_lens_tab.set_service(None)
        self._set_mutations_enabled(False)
        self._refresh_lens_controls()
        self._schema_options = {}
        self.schema_table.setRowCount(0)
        with QSignalBlocker(self.schema_object_class):
            self.schema_object_class.clear()
            self.schema_object_class.addItem("All object classes", "")
        self.schema_status.setText("Load the connected directory's schema.")
        self._schema_names.pop(profile.profile_id, None)
        self.warning_banner.setVisible(profile.transport != "plain")
        self.transport_warning.setText(
            "Plain LDAP is unencrypted. Use only on a trusted network."
            if profile.transport == "plain"
            else ""
        )
        self.transport_warning.setVisible(profile.transport == "plain")
        self.statusBar().showMessage(f"Connecting to {profile.host}...")

        def operation(cancelled: Event, progress: Callable[[int, str], None]) -> Any:
            if profile.transport != "plain" and not profile.ca_certificate:
                certificate = LDAPService.inspect_server_certificate(
                    profile,
                    self.vault.get_client_key_passphrase(profile),
                    cancelled,
                )
                observed = certificate_details(certificate).fingerprint_sha256
                if not profile.tofu_fingerprint:
                    return {"certificate": certificate, "profile_id": profile.profile_id}
                if observed.casefold() != profile.tofu_fingerprint.casefold():
                    return {
                        "certificate": certificate,
                        "profile_id": profile.profile_id,
                        "rotation": True,
                        "expected_fingerprint": profile.tofu_fingerprint,
                    }
            entries, cookie = self._service(profile, password).search_page(
                profile.base_dn,
                "(objectClass=*)",
                attributes=["1.1"],
                scope="level",
                page_size=500,
                cancelled=cancelled,
                progress=progress,
            )
            return {"entries": entries, "cookie": cookie, "profile_id": profile.profile_id}

        self._run_worker(
        operation,
        self._connection_result,
        self._show_error,
        )

    def _connection_result(self, result: dict[str, Any]) -> None:
        profile = next(
            (
                candidate
                for candidate in self.configuration.profiles
                if candidate.profile_id == result.get("profile_id")
            ),
            None,
        )
        if (
            profile is None
            or self._active_profile is None
            or profile.profile_id != self._active_profile.profile_id
        ):
            return
        certificate = result.get("certificate")
        if certificate:
            try:
                trusted = confirm_certificate_trust(self, profile, certificate)
            except CertificateTrustError as error:
                QMessageBox.critical(self, "Certificate error", str(error))
                return
            rotation = result.get("rotation", False)
            expected_fingerprint = result.get("expected_fingerprint", "")
            if not trusted:
                self.statusBar().showMessage("Certificate was not trusted")
                return
            try:
                if rotation:
                    self.trust_store.replace_tofu(
                        profile,
                        certificate,
                        expected_fingerprint,
                    )
                else:
                    self.trust_store.accept_tofu(profile, certificate)
                self.store.save(self.configuration)
            except (CertificateTrustError, ConfigurationError) as error:
                QMessageBox.critical(self, "Certificate trust failed", str(error))
                return
            profile_index = self.profiles.findData(profile.profile_id)
            if profile_index >= 0:
                self.profiles.setCurrentIndex(profile_index)
            self._connect()
            return
        self._show_directory(result["entries"], result.get("cookie"))
        try:
            service = self._service(profile, self._profile_password(profile))
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        self.data_lens_tab.set_service(service)
        self._connecting = False
        self._refresh_lens_controls()
        self._set_mutations_enabled(
            self._active_profile is not None and not self._active_profile.read_only
        )
        if self.configuration.last_connected_profile_id != profile.profile_id:
            previous_profile_id = self.configuration.last_connected_profile_id
            self.configuration.last_connected_profile_id = profile.profile_id
            try:
                self.store.save(self.configuration)
            except ConfigurationError as error:
                self.configuration.last_connected_profile_id = previous_profile_id
                QMessageBox.warning(
                    self,
                    "Connection preference not saved",
                    f"Connected to {profile.name}, but DataSifter could not remember it "
                    f"for next time: {error}",
                )

    def _load_schema(self) -> None:
        profile = self._active_profile
        if profile is None:
            self.schema_status.setText("Connect to a directory before loading its schema.")
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        self.schema_status.setText("Loading schema from the connected directory…")

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> dict[str, Any]:
            if cancelled.is_set():
                return {}
            return self._service(profile, password).schema_options(cancelled)

        def display_schema(schema: dict[str, Any]) -> None:
            if not schema or self._active_profile is None:
                return
            if self._active_profile.profile_id != profile.profile_id:
                return
            self._schema_options = schema
            selected = self.schema_object_class.currentData()
            with QSignalBlocker(self.schema_object_class):
                self.schema_object_class.clear()
                self.schema_object_class.addItem("All object classes", "")
                for name in sorted(schema["object_classes"], key=str.casefold):
                    self.schema_object_class.addItem(name, name)
                index = self.schema_object_class.findData(selected)
                self.schema_object_class.setCurrentIndex(max(index, 0))
            rows: list[tuple[str, str, str]] = []
            for name, details in sorted(
                schema["object_classes"].items(),
                key=lambda item: item[0].casefold(),
            ):
                required = ", ".join(details["required"]) or "none"
                optional = ", ".join(details["optional"]) or "none"
                rows.append((
                    "Object class",
                    name,
                    f"{details['kind']}; MUST: {required}; MAY: {optional}",
                ))
            for attribute in schema["attribute_details"]:
                details = f"Syntax: {attribute['syntax'] or 'unspecified'}"
                if attribute["oid"]:
                    details += f"; OID: {attribute['oid']}"
                if attribute["description"]:
                    details += f"; {attribute['description']}"
                if attribute["single_value"]:
                    details += "; single-valued"
                rows.append(("Attribute", attribute["name"], details))
            self.schema_table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.schema_table.setItem(row, column, item)
            self.schema_status.setText(
                f"Loaded {len(schema['object_classes'])} object classes and "
                f"{len(schema['attributes'])} attribute types."
            )
            self._filter_schema()

        self._run_worker(operation, display_schema)

    def _filter_schema(self) -> None:
        selected = self.schema_object_class.currentData()
        query = self.schema_attribute_search.text().strip().casefold()
        classes = self._schema_options.get("object_classes", {})
        allowed: set[str] = set()
        pending = [selected] if selected else []
        seen: set[str] = set()
        lookup = {name.casefold(): details for name, details in classes.items()}
        while pending:
            name = pending.pop().casefold()
            if name in seen:
                continue
            seen.add(name)
            details = lookup.get(name, {})
            allowed.update(value.casefold() for value in details.get("required", []))
            allowed.update(value.casefold() for value in details.get("optional", []))
            pending.extend(details.get("superiors", []))
        unrestricted = "*" in allowed
        for row in range(self.schema_table.rowCount()):
            kind = self.schema_table.item(row, 0).text()
            name = self.schema_table.item(row, 1).text()
            if kind == "Object class":
                visible = not query and (not selected or name == selected)
            else:
                visible = (
                    (not selected or unrestricted or name.casefold() in allowed)
                    and (not query or query in name.casefold())
                )
            self.schema_table.setRowHidden(row, not visible)

    def _execute_filter(self) -> None:
        profile = self._active_profile
        if profile is None:
            QMessageBox.information(self, "Not connected", "Connect to a directory first.")
            return
        expression = self.filter_edit.toPlainText()
        try:
            validate_filter(expression)
        except FilterSyntaxError as error:
            QMessageBox.warning(self, "Invalid LDAP filter", str(error))
            return
        self._run_directory_search(profile, expression)

    def _clear_filter(self) -> None:
        profile = self._active_profile
        if (
            profile is not None and not self._connecting and not self._workers
            and self._run_directory_search(profile, _DEFAULT_LDAP_FILTER)
        ):
            self.filter_edit.setPlainText(_DEFAULT_LDAP_FILTER)

    def _run_directory_search(self, profile: DirectoryProfile, expression: str) -> bool:
        if not self._confirm_leave_entry():
            return False
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return False
        include_operational = self.operational_toggle.isChecked()

        def operation(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> list[dict[str, Any]]:
            return self._service(profile, password).search(
                profile.base_dn,
                expression,
                attributes=["*", "+"] if include_operational else ["*"],
                scope="subtree",
                page_size=500,
                cancelled=cancelled,
                progress=progress,
            )

        def show_results(entries: list[dict[str, Any]]) -> None:
            if not entries:
                self.statusBar().showMessage("No entries found")
                QMessageBox.information(
                    self,
                    "No entries found",
                    f"No directory entries match this search.\n\nFilter: {expression}\n"
                    f"Search base: {profile.base_dn or '(server default)'}\n\n"
                    "The current directory view was left unchanged.",
                )
                return
            self._show_directory(
                entries,
                operational=include_operational,
                searched_attributes=filter_attribute_names(expression),
            )

        self._run_worker(operation, show_results)
        return True

    def _show_directory(
        self,
        entries: list[dict[str, Any]],
        page_cookie: bytes | None = None,
        operational: bool = False,
        searched_attributes: set[str] | None = None,
        root_dn: str | None = None,
    ) -> None:
        self.tree.clear()
        self._search_attributes = set(searched_attributes or ())
        self._selected_dn = None
        self.attributes.setRowCount(0)
        base_dn = root_dn or (self._active_profile.base_dn if self._active_profile else "")
        root = QTreeWidgetItem([base_dn or "Directory"])
        root.setData(0, Qt.ItemDataRole.UserRole, base_dn)
        root.setData(0, Qt.ItemDataRole.UserRole + 2, page_cookie)
        root.setData(0, Qt.ItemDataRole.UserRole + 3, True)
        self.tree.addTopLevelItem(root)
        self._append_entries(root, entries, operational)
        self._add_more_item(root, page_cookie)
        root.setExpanded(True)
        self.statusBar().showMessage(f"Loaded {len(entries)} directory entries")
        self._set_mutations_enabled(
            self._active_profile is not None and not self._active_profile.read_only
        )

    def _append_entries(
        self,
        parent: QTreeWidgetItem,
        entries: list[dict[str, Any]],
        operational: bool = False,
    ) -> None:
        for entry in entries:
            item = QTreeWidgetItem([entry["dn"]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["dn"])
            item.setData(0, Qt.ItemDataRole.UserRole + 1, entry.get("attributes", {}))
            item.setData(0, Qt.ItemDataRole.UserRole + 3, False)
            item.setData(0, _CACHED_OPERATIONAL_ROLE, operational)
            parent.addChild(item)
            if item.data(0, Qt.ItemDataRole.UserRole):
                item.addChild(QTreeWidgetItem(["Expand to load children"]))

    def _add_more_item(self, parent: QTreeWidgetItem, page_cookie: bytes | None) -> None:
        if page_cookie:
            more = QTreeWidgetItem(["Load next 500 entries…"])
            more.setData(0, Qt.ItemDataRole.UserRole + 4, True)
            parent.addChild(more)

    def _expand_directory_node(self, item: QTreeWidgetItem) -> None:
        distinguished_name = item.data(0, Qt.ItemDataRole.UserRole)
        if (
            not distinguished_name
            or item.data(0, Qt.ItemDataRole.UserRole + 3)
            or item.data(0, Qt.ItemDataRole.UserRole + 5)
        ):
            return
        self._load_children(item, distinguished_name, None)

    def _refresh_tree(self) -> None:
        if self._active_profile is None:
            return
        item = self.tree.currentItem() or self.tree.topLevelItem(0)
        if item is not None and item.data(0, Qt.ItemDataRole.UserRole + 4):
            item = item.parent() or self.tree.topLevelItem(0)
        if item is None:
            self.statusBar().showMessage("Connect to a directory before refreshing the tree.")
            return
        self._refresh_tree_item(item)

    def _tree_context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None or item.data(0, Qt.ItemDataRole.UserRole + 4):
            return
        distinguished_name = item.data(0, Qt.ItemDataRole.UserRole)
        if not distinguished_name:
            return
        menu = QMenu(self)
        refresh_action = menu.addAction("Refresh subtree")
        selected_action = menu.exec(self.tree.viewport().mapToGlobal(position))
        if selected_action is refresh_action:
            self._refresh_tree_item(item)

    def _refresh_tree_item(self, item: QTreeWidgetItem) -> None:
        if self._active_profile is None:
            return
        if self._workers:
            self.statusBar().showMessage("Wait for active operations to finish before refreshing.")
            return
        distinguished_name = item.data(0, Qt.ItemDataRole.UserRole)
        if not distinguished_name:
            return
        distinguished_name = str(distinguished_name)
        selected_dn = self._selected_dn
        if not self._confirm_leave_entry():
            return
        if (
            selected_dn
            and selected_dn.casefold() != distinguished_name.casefold()
            and selected_dn.casefold().endswith("," + distinguished_name.casefold())
        ):
            self._clear_entry_view()
        self._load_children(item, distinguished_name, None)
        if (
            selected_dn
            and selected_dn.casefold() == distinguished_name.casefold()
            and self._selected_dn == distinguished_name
        ):
            self._load_entry(distinguished_name)

    def _load_children(
        self,
        parent: QTreeWidgetItem,
        distinguished_name: str,
        page_cookie: bytes | None,
        append: bool = False,
    ) -> None:
        profile = self._active_profile
        if profile is None:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        if not append:
            for index in reversed(range(parent.childCount())):
                parent.removeChild(parent.child(index))
        parent.setData(0, Qt.ItemDataRole.UserRole + 5, True)

        def operation(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> tuple[list[dict[str, Any]], bytes | None]:
            if cancelled.is_set():
                return [], None
            return self._service(profile, password).search_page(
                distinguished_name,
                "(objectClass=*)",
                attributes=["1.1"],
                scope="level",
                page_size=500,
                page_cookie=page_cookie,
                cancelled=cancelled,
                progress=progress,
            )

        def display_page(result: tuple[list[dict[str, Any]], bytes | None]) -> None:
            children, next_cookie = result
            self._append_entries(parent, children)
            self._add_more_item(parent, next_cookie)
            parent.setData(0, Qt.ItemDataRole.UserRole + 2, next_cookie)
            parent.setData(0, Qt.ItemDataRole.UserRole + 3, True)
            parent.setData(0, Qt.ItemDataRole.UserRole + 5, False)

        def display_failure(message: str) -> None:
            parent.setData(0, Qt.ItemDataRole.UserRole + 5, False)
            if not append:
                parent.setData(0, Qt.ItemDataRole.UserRole + 3, False)
                parent.addChild(QTreeWidgetItem(["Expand to retry loading children"]))
            else:
                self._add_more_item(
                    parent,
                    parent.data(0, Qt.ItemDataRole.UserRole + 2),
                )
            self._show_error(message)

        self._run_worker(operation, display_page, display_failure)

    def _select_entry(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, Qt.ItemDataRole.UserRole + 4):
            parent = item.parent()
            if parent is not None:
                cookie = parent.data(0, Qt.ItemDataRole.UserRole + 2)
                parent.removeChild(item)
                self._load_children(
                    parent,
                    str(parent.data(0, Qt.ItemDataRole.UserRole)),
                    cookie,
                    append=True,
                )
            return
        distinguished_name = item.data(0, Qt.ItemDataRole.UserRole)
        attributes = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not distinguished_name or not self._active_profile:
            return
        if str(distinguished_name) == self._selected_dn and self._has_unsaved_changes():
            return
        if not self._confirm_leave_entry():
            previous = self._selected_item
            blocker = QSignalBlocker(self.tree)
            self.tree.setCurrentItem(previous)
            del blocker
            return
        self._selected_item = item
        self._selected_dn = str(distinguished_name)
        self._set_mutations_enabled(not self._active_profile.read_only)
        self._refresh_connection_controls()
        cache_matches = bool(item.data(0, _CACHED_OPERATIONAL_ROLE)) == (
            self.operational_toggle.isChecked()
        )
        if attributes and cache_matches:
            self._display_attributes(attributes)
        else:
            self._load_entry(self._selected_dn)

    def _reload_selected_entry(self, _state: int) -> None:
        if not self._confirm_leave_entry():
            blocker = QSignalBlocker(self.operational_toggle)
            self.operational_toggle.setChecked(not self.operational_toggle.isChecked())
            del blocker
            return
        if self._selected_dn is None:
            item = self.tree.currentItem()
            if item is not None:
                self._select_entry(item, 0)
            return
        self._load_entry(self._selected_dn)

    def _load_entry(self, distinguished_name: str) -> None:
        profile = self._active_profile
        if profile is None:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        include_operational = self.operational_toggle.isChecked()

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> dict[str, Any]:
            return self._service(profile, password).read_entry(
                distinguished_name,
                include_operational=include_operational,
                cancelled=cancelled,
            )

        self._run_worker(
            operation,
            lambda result: self._display_loaded_entry(
                distinguished_name, result, include_operational
            ),
        )

    def _display_loaded_entry(
        self,
        distinguished_name: str,
        result: dict[str, Any],
        operational: bool = False,
    ) -> None:
        if self._selected_dn == distinguished_name:
            if self._selected_item is not None:
                # Keep the tree cache current so reselecting does not show stale values.
                self._selected_item.setData(0, Qt.ItemDataRole.UserRole + 1, result["attributes"])
                self._selected_item.setData(0, _CACHED_OPERATIONAL_ROLE, operational)
            self._display_attributes(result["attributes"])

    def _display_attributes(self, attributes: dict[str, Any]) -> None:
        sorting = self.attributes.isSortingEnabled()
        self.attributes.setSortingEnabled(False)
        self.attributes.setRowCount(0)
        self._readonly_attribute_names = set()
        self._original_attributes = {
            name: list(values) if isinstance(values, (list, tuple)) else [values]
            for name, values in attributes.items()
        }
        self._known_attributes = list(attributes)
        object_classes = attributes.get("objectClass", [])
        if isinstance(object_classes, str):
            object_classes = [object_classes]
        self._known_object_classes = [str(value) for value in object_classes]
        for name, values in attributes.items():
            row = self.attributes.rowCount()
            self.attributes.insertRow(row)
            self.attributes.setItem(row, 0, QTableWidgetItem(str(name)))
            self.attributes.item(row, 0).setFlags(
                self.attributes.item(row, 0).flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            normalized_values = list(values) if isinstance(values, (list, tuple)) else [values]
            binary_value = False
            rendered_values = []
            for value in normalized_values:
                if isinstance(value, bytes):
                    binary_value = True
                    try:
                        rendered_values.append(value.decode("utf-8"))
                    except UnicodeDecodeError:
                        rendered_values.append(f"<binary value: {len(value)} bytes>")
                else:
                    rendered_value = str(value)
                    if "\n" in rendered_value or "\r" in rendered_value:
                        binary_value = True
                    rendered_values.append(rendered_value)
            rendered = "\n".join(rendered_values)
            self.attributes.setItem(row, 1, QTableWidgetItem(rendered))
            is_operational = _is_operational_attribute(str(name))
            if is_operational:
                for column in range(2):
                    item = self.attributes.item(row, column)
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                    item.setToolTip("Operational attribute (read-only)")
            if binary_value or is_operational or (
                self._active_profile and self._active_profile.read_only
            ):
                self._readonly_attribute_names.add(name.casefold())
                self.attributes.item(row, 1).setFlags(
                    self.attributes.item(row, 1).flags()
                    & ~Qt.ItemFlag.ItemIsEditable
                )
        self.attributes.setSortingEnabled(sorting)
        self._apply_search_highlight()
        self.attributes.resizeRowsToContents()
        self._update_save_enabled()

    # ---------------------------------------------------------------------
    # Context menu for attribute values
    # ---------------------------------------------------------------------
    def _attributes_context_menu(self, pos: QPoint) -> None:
        """Show a context menu for the attribute values table.

        The menu provides standard copy/cut/paste/select‑all actions.  The
        actions operate on the current selection of the table.  If the user
        right‑clicks on an empty area, no menu is shown.
        """
        item = self.attributes.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        copy_action = menu.addAction("Copy")
        paste_action = menu.addAction("Paste")
        cut_action = menu.addAction("Cut")
        menu.addSeparator()
        select_all_action = menu.addAction("Select All")
        action = menu.exec(self.attributes.mapToGlobal(pos))
        if action == copy_action:
            self.attributes.copy()
        elif action == paste_action:
            self.attributes.paste()
        elif action == cut_action:
            self.attributes.cut()
        elif action == select_all_action:
            self.attributes.selectAll()

    def _apply_search_highlight(self) -> None:
        enabled = self.configuration.preferences.highlight_search_attributes
        searched = getattr(self, "_search_attributes", set())
        with QSignalBlocker(self.attributes):
            for row in range(self.attributes.rowCount()):
                name_item = self.attributes.item(row, 0)
                if name_item is None:
                    continue
                matched = enabled and name_item.text().casefold() in searched
                for column in range(self.attributes.columnCount()):
                    item = self.attributes.item(row, column)
                    if item is None:
                        continue
                    item.setBackground(QBrush(QColor("#fff3cd")) if matched else QBrush())
                    item.setForeground(QBrush(QColor("#000000")) if matched else QBrush())
                    font = item.font()
                    font.setBold(matched)
                    item.setFont(font)
                operational = _is_operational_attribute(name_item.text())
                tooltip = "Operational attribute (read-only)" if operational else ""
                if matched:
                    tooltip += ("\n" if tooltip else "") + "Used in the current search filter"
                name_item.setToolTip(tooltip)
        self.attributes.viewport().update()

    def _set_search_highlight_preference(self, enabled: bool) -> None:
        preferences = self.configuration.preferences
        previous = preferences.highlight_search_attributes
        preferences.highlight_search_attributes = enabled
        try:
            self.store.save(self.configuration)
        except ConfigurationError as error:
            preferences.highlight_search_attributes = previous
            with QSignalBlocker(self.highlight_search_action):
                self.highlight_search_action.setChecked(previous)
            QMessageBox.critical(self, "Preference not saved", str(error))
            return
        self._apply_search_highlight()

    def _set_mutations_enabled(self, enabled: bool) -> None:
        self.add_entry_button.setEnabled(enabled)
        self.add_attribute_button.setEnabled(enabled and self._selected_dn is not None)
        self._update_save_enabled()
        self.delete_entry_button.setEnabled(enabled and self._selected_dn is not None)
        self.move_entry_button.setEnabled(enabled and self._selected_dn is not None)
        self.password_button.setEnabled(enabled and self._selected_dn is not None)

    def _add_entry(self) -> None:
        profile = self._active_profile
        if profile is None or profile.read_only:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def load_schema(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> dict[str, Any]:
            if cancelled.is_set():
                return {}
            return self._service(profile, password).schema_options(cancelled)

        self._run_worker(
            load_schema,
            lambda schema: self._show_add_entry_dialog(profile, password, schema),
        )

    def _show_add_entry_dialog(
        self,
        profile: DirectoryProfile,
        password: str | None,
        schema: dict[str, Any],
    ) -> None:
        if not schema:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Create LDAP entry")
        form = QFormLayout(dialog)
        dn = QLineEdit()
        object_classes = QLineEdit("top")
        attributes = QPlainTextEdit()
        attributes.setPlaceholderText(
            "One attribute=value pair per line\n"
            "Use the schema's required attributes for the selected object classes."
        )
        class_details = "\n".join(
            f"{name} ({info['kind']}): required {', '.join(info['required']) or 'none'}"
            for name, info in sorted(schema["object_classes"].items())
        )
        attribute_details = ", ".join(schema["attributes"])
        class_schema = QPlainTextEdit(class_details)
        class_schema.setReadOnly(True)
        class_schema.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        class_schema.setMaximumHeight(120)
        attribute_schema = QPlainTextEdit(attribute_details)
        attribute_schema.setReadOnly(True)
        attribute_schema.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        attribute_schema.setMaximumHeight(120)
        form.addRow("Distinguished name", dn)
        form.addRow("Object classes (comma-separated)", object_classes)
        form.addRow("Directory object-class schema", class_schema)
        form.addRow("Directory attribute schema", attribute_schema)
        form.addRow("Attribute values", attributes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values: dict[str, list[str]] = {}
        for line in attributes.toPlainText().splitlines():
            name, separator, value = line.partition("=")
            if not separator or not name.strip():
                QMessageBox.warning(self, "Invalid attribute", f"Invalid attribute line: {line}")
                return
            values.setdefault(name.strip(), []).append(value)
        classes = [value.strip() for value in object_classes.text().split(",") if value.strip()]
        if not classes:
            QMessageBox.warning(self, "Invalid entry", "At least one object class is required.")
            return
        available_classes = {name.casefold(): info for name, info in schema["object_classes"].items()}
        unknown_classes = [name for name in classes if name.casefold() not in available_classes]
        if unknown_classes:
            QMessageBox.warning(
                self,
                "Unknown object class",
                "Choose object classes advertised by the directory schema: "
                + ", ".join(unknown_classes),
            )
            return
        structural_classes = [
            name
            for name in classes
            if str(available_classes[name.casefold()]["kind"]).casefold() == "structural"
        ]
        if not structural_classes:
            QMessageBox.warning(
                self,
                "Missing structural class",
                "At least one structural object class is required.",
            )
            return
        known_attributes = {name.casefold() for name in schema["attributes"]}
        unknown_attributes = [
            name
            for name in values
            if name.casefold() != "objectclass"
            and name.split(";", 1)[0].casefold() not in known_attributes
        ]
        if unknown_attributes:
            QMessageBox.warning(
                self,
                "Unknown attribute",
                "Choose attributes advertised by the directory schema: "
                + ", ".join(unknown_attributes),
            )
            return
        required_attributes = {
            attribute.casefold()
            for class_name in classes
            for attribute in available_classes[class_name.casefold()]["required"]
        }
        missing_required = sorted(required_attributes - {name.casefold() for name in values})
        if missing_required:
            QMessageBox.warning(
                self,
                "Required attributes missing",
                "The selected object classes require: " + ", ".join(missing_required),
            )
            return
        values["objectClass"] = classes
        entry_dn = dn.text().strip()

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).add_entry(
                entry_dn,
                values,
                cancelled,
            )

        self._run_worker(operation, lambda _result: self._connect())

    def _attribute_changes(self) -> dict[str, list[tuple[str, Any]]]:
        current: dict[str, list[str]] = {}
        for row in range(self.attributes.rowCount()):
            name_item = self.attributes.item(row, 0)
            value_item = self.attributes.item(row, 1)
            if name_item is None or value_item is None:
                continue
            name = name_item.text().strip()
            if name and name.casefold() not in self._readonly_attribute_names:
                current[name] = value_item.text().splitlines()
        changes: dict[str, list[tuple[str, Any]]] = {}
        for name in self._original_attributes.keys() | current.keys():
            if name.casefold() in self._readonly_attribute_names:
                continue
            old_values = [str(value) for value in self._original_attributes.get(name, [])]
            new_values = current.get(name, [])
            if old_values == new_values:
                continue
            if not new_values:
                changes[name] = [("delete", [])]
            elif name not in self._original_attributes:
                changes[name] = [("add", new_values)]
            else:
                changes[name] = [("replace", new_values)]
        return changes

    def _update_save_enabled(self) -> None:
        if not hasattr(self, "save_entry_button"):
            return
        profile = self._active_profile
        has_changes = bool(self._selected_dn and self._attribute_changes())
        self.save_entry_button.setEnabled(bool(
            profile and not profile.read_only and has_changes and not self._workers
        ))
        font = self.save_entry_button.font()
        if font.bold() != has_changes:
            font.setBold(has_changes)
            self.save_entry_button.setFont(font)

    @property
    def _selected_dn(self) -> str | None:
        return self.__dict__.get("_selected_dn_value")

    @_selected_dn.setter
    def _selected_dn(self, value: str | None) -> None:
        self.__dict__["_selected_dn_value"] = value
        if value is None:
            self._selected_item = None
        if hasattr(self, "dn_field"):
            self.dn_field.setText(value or "")
            self.dn_field.setCursorPosition(0)
            self.copy_dn_button.setEnabled(bool(value))

    def _copy_dn(self) -> None:
        if self._selected_dn:
            QGuiApplication.clipboard().setText(self._selected_dn)
            self.statusBar().showMessage("Distinguished name copied to clipboard", 3000)

    def _has_unsaved_changes(self) -> bool:
        return bool(self._selected_dn and self._attribute_changes())

    def _commit_open_editor(self) -> None:
        index = self.attributes.currentIndex()
        editor = self.attributes.indexWidget(index) if index.isValid() else None
        delegate = self.attributes.itemDelegateForIndex(index) if editor else None
        if editor is not None and delegate is not None:
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)

    def _confirm_leave_entry(self) -> bool:
        """Return True when it is safe to replace the displayed entry."""
        self._commit_open_editor()
        if not self._has_unsaved_changes():
            return True
        names = ", ".join(sorted(self._attribute_changes(), key=str.casefold))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved attribute changes")
        box.setText(f"{self._selected_dn} has unsaved changes.")
        box.setInformativeText(
            f"Changed attributes: {names}\n\nDiscard these changes, or cancel and use "
            "Save attribute changes first?"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Discard:
            return False
        self._clear_entry_view()
        return True

    def _reconnect_after_entry_removed(self, _result: Any) -> None:
        # The edited entry no longer exists at its old DN, so pending edits cannot be saved.
        self._clear_entry_view()
        self._connect()

    def _clear_entry_view(self) -> None:
        self._original_attributes = {}
        self.attributes.setRowCount(0)
        self._selected_dn = None
        self._update_save_enabled()
        self._refresh_connection_controls()

    def _save_entry_changes(self) -> None:
        profile = self._active_profile
        if profile is None or profile.read_only or not self._selected_dn:
            return
        changes = self._attribute_changes()
        if not changes:
            self.statusBar().showMessage("No attribute changes to save")
            return
        distinguished_name = self._selected_dn
        reply = QMessageBox.question(
            self,
            "Confirm attribute changes",
            f"Apply these changes to the highlighted entry?\n\n{distinguished_name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).modify_entry(
                distinguished_name,
                changes,
                cancelled,
            )

        self._run_worker(operation, lambda _result: self._load_entry(distinguished_name))

    def _add_attribute(self) -> None:
        profile = self._active_profile
        if profile is None or profile.read_only or not self._selected_dn:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def load_schema(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> dict[str, Any]:
            if cancelled.is_set():
                return {}
            return self._service(profile, password).schema_options(cancelled)

        def choose_attribute(schema: dict[str, Any]) -> None:
            if not schema:
                return
            existing = {
                self.attributes.item(row, 0).text().casefold()
                for row in range(self.attributes.rowCount())
                if self.attributes.item(row, 0) is not None
            }
            candidates = [
                name for name in schema["attributes"] if name.casefold() not in existing
            ]
            if not candidates:
                QMessageBox.information(self, "No attributes available", "All schema attributes are present.")
                return
            picker = QDialog(self)
            picker.setWindowTitle("Add LDAP attribute")
            picker.setMinimumWidth(480)
            form = QFormLayout(picker)
            attribute = QComboBox()
            attribute.setEditable(True)
            attribute.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            attribute.addItems(sorted(candidates, key=str.casefold))
            completer = QCompleter(attribute.model(), attribute)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            attribute.setCompleter(completer)
            form.addRow("Type or select a schema attribute:", attribute)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            form.addRow(buttons)
            buttons.accepted.connect(picker.accept)
            buttons.rejected.connect(picker.reject)
            if picker.exec() != QDialog.DialogCode.Accepted:
                return
            name = next(
                (candidate for candidate in candidates
                 if candidate.casefold() == attribute.currentText().strip().casefold()),
                None,
            )
            if name is None:
                QMessageBox.warning(
                    self, "Invalid attribute", "Select an attribute advertised by the schema."
                )
                return
            value, accepted = QInputDialog.getText(
                self,
                "Add LDAP attribute",
                f"Initial value for {name}:",
            )
            if not accepted:
                return
            sorting = self.attributes.isSortingEnabled()
            self.attributes.setSortingEnabled(False)
            row = self.attributes.rowCount()
            self.attributes.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.attributes.setItem(row, 0, name_item)
            self.attributes.setItem(row, 1, QTableWidgetItem(value))
            self.attributes.setSortingEnabled(sorting)
            self.attributes.resizeRowsToContents()

        self._run_worker(load_schema, choose_attribute)

    def _delete_entry(self) -> None:
        profile = self._active_profile
        distinguished_name = self._selected_dn
        if profile is None or profile.read_only or not distinguished_name:
            return
        recursive = QCheckBox("Delete descendants recursively")
        if self.configuration.preferences.require_delete_confirmation:
            dialog = QDialog(self)
            dialog.setWindowTitle("Delete directory entry")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(f"Delete {distinguished_name}? This cannot be undone."))
            layout.addWidget(recursive)
            skip = QCheckBox("Don't ask again before deleting entries")
            layout.addWidget(skip)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if skip.isChecked() and not self._set_directory_confirmation("delete", False):
                return
        recursive_delete = recursive.isChecked()
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).delete_entry(
                distinguished_name,
                recursive=recursive_delete,
                cancelled=cancelled,
            )

        self._run_worker(operation, self._reconnect_after_entry_removed)

    def _move_entry(self) -> None:
        profile = self._active_profile
        distinguished_name = self._selected_dn
        if profile is None or profile.read_only or not distinguished_name:
            return
        if not self._confirm_leave_entry():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Rename or move entry")
        form = QFormLayout(dialog)
        relative_dn = QLineEdit(distinguished_name.split(",", 1)[0])
        superior = QLineEdit(
            distinguished_name.split(",", 1)[1] if "," in distinguished_name else ""
        )
        delete_old_rdn = QCheckBox("Delete old RDN attribute value")
        delete_old_rdn.setChecked(True)
        form.addRow("New relative DN", relative_dn)
        form.addRow("New parent DN (blank keeps current parent)", superior)
        form.addRow(delete_old_rdn)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not relative_dn.text().strip():
            return
        parent_dn = superior.text().strip() or (
            distinguished_name.split(",", 1)[1] if "," in distinguished_name else ""
        )
        target = f"{relative_dn.text().strip()},{parent_dn}" if parent_dn else relative_dn.text().strip()
        if not self._confirm_directory_change(
            "rename", f"Rename/move {distinguished_name} to {target}?"
        ):
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).move_entry(
                distinguished_name,
                relative_dn.text().strip(),
                new_superior=superior.text().strip() or None,
                delete_old_rdn=delete_old_rdn.isChecked(),
                cancelled=cancelled,
            )

        self._run_worker(operation, self._reconnect_after_entry_removed)

    def _change_password(self) -> None:
        profile = self._active_profile
        distinguished_name = self._selected_dn
        if profile is None or profile.read_only or not distinguished_name:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Change directory password")
        form = QFormLayout(dialog)
        old_password = QLineEdit()
        old_password.setEchoMode(QLineEdit.EchoMode.Password)
        new_password = QLineEdit()
        new_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current password (optional for administrator resets)", old_password)
        form.addRow("New password", new_password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not new_password.text():
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).change_password(
                distinguished_name,
                new_password.text(),
                old_password.text() or None,
                cancelled,
            )

        self._run_worker(operation, lambda _result: self._load_entry(distinguished_name))

    def _session_state(self, profile: DirectoryProfile) -> str:
        active = self._active_profile
        if active is None or active.profile_id != profile.profile_id:
            return ""
        if not self._connecting:
            return "connected"
        return "connecting" if self._workers else "connection not completed"

    def _run_session_command(self, request: str, command: SessionCommand) -> None:
        self._refresh_lens_controls()
        if not self.ai_generate.isEnabled():
            QMessageBox.information(self, "Data Lens", self.ai_generate.toolTip())
            return
        try:
            message = self._apply_session_command(command)
        except SessionCommandError as error:
            message = str(error)
        self.ai_output.setPlainText(message)
        self._log_lens_event(f"Session command: {request}\n{message}")
        self._refresh_lens_controls()

    def _apply_session_command(self, command: SessionCommand) -> str:
        profiles = self.configuration.profiles
        active = self._active_profile
        if command.action == LIST_TARGETS:
            if not profiles:
                return "No saved profiles. Use Add profile to create a target."
            last_connected = self._last_connected_profile()
            lines = []
            for profile in profiles:
                state = self._session_state(profile)
                if not state and profile is last_connected:
                    state = "last connected"
                lines.append(describe_target(profile) + (f" [{state}]" if state else ""))
            return "\n".join(lines)
        if command.action == CURRENT_TARGET:
            if active is None:
                last_connected = self._last_connected_profile()
                return "No target is connected." + (
                    f" Last connected: {last_connected.name}." if last_connected else ""
                )
            state = self._session_state(active)
            if state != "connected":
                return f"Not connected. {describe_target(active)} [{state}]"
            return (
                f"Connected to {describe_target(active)}; "
                f"base DN {active.base_dn or '(not set)'}; "
                f"{'read-only' if active.read_only else 'writable'}."
            )
        if command.action == DISCONNECT:
            if active is None:
                return "No target is connected."
            if command.target:
                requested = resolve_target(command.target, profiles)
                if requested.profile_id != active.profile_id:
                    return (
                        f"{requested.name} is not the current target. "
                        f"The current target is {active.name}."
                    )
            self._disconnect()
            if self._active_profile is not None:
                return f"Disconnect cancelled; {active.name} is still the current target."
            return f"Disconnected from {active.name}."
        if command.target:
            profile = resolve_target(command.target, profiles)
        else:
            profile = self._default_profile()
            if profile is None:
                return "No saved profiles. Use Add profile to create a target."
        if self._session_state(profile) == "connected":
            return f"Already connected to {describe_target(profile)}."
        previous = ""
        if active is not None:
            self._disconnect()
            if self._active_profile is not None:
                return f"Switch cancelled; {active.name} is still the current target."
            if active.profile_id != profile.profile_id:
                previous = f"Disconnected from {active.name}. "
        self.profiles.setCurrentIndex(self.profiles.findData(profile.profile_id))
        self._connect_profile(profile)
        if self._active_profile is not profile:
            return f"{previous}Connection to {profile.name} was cancelled."
        return f"{previous}Connecting to {describe_target(profile)}…"

    def _run_directory_information(self, action: str) -> None:
        self._refresh_lens_controls()
        profile = self._active_profile
        if not self.ai_generate.isEnabled() or profile is None:
            QMessageBox.information(self, "Data Lens", self.ai_generate.toolTip())
            return
        if action == COUNT_PEOPLE:
            self._count_people(profile)
        else:
            self._show_monitor(profile)

    def _count_people(self, profile: DirectoryProfile) -> None:
        if not profile.base_dn:
            QMessageBox.information(
                self, "Choose a starting location",
                "Set a directory starting location in the connection profile first. "
                "See Help → Data Lens help → Directory information for people counts.",
            )
            return
        locations = ["The profile's starting location and everything below it"]
        if self._selected_dn and self._selected_dn.casefold() != profile.base_dn.casefold():
            label = self._selected_item.text(0) if self._selected_item else self._selected_dn
            locations.append(f"The selected location: {label}")
        choice, accepted = QInputDialog.getItem(
            self, "Count people", "Where should I start? Select a location in the "
            "directory tree first to count only that location.", locations, 0, False
        )
        if not accepted:
            return
        base_dn = self._selected_dn if choice != locations[0] else profile.base_dn
        if not base_dn:
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        self.ai_output.setPlainText("Counting visible people; this may take some time.")

        def operation(cancelled: Event, progress: Callable[[int, str], None]) -> int:
            service = self._service(profile, password)
            adapter = create_adapter(service, detect_backend(service, cancelled))
            return service.count_entries(base_dn, adapter.user_filter(), cancelled, progress)

        def completed(count: int) -> None:
            self.ai_output.setPlainText(
                f"Counted {count} people visible to this account under {base_dn}.\n"
                "This covers the selected location only. Permissions may hide entries, "
                "and the directory may change during the count."
            )

        self._run_worker(operation, completed, on_failure=self._directory_information_error)

    def _monitor_credentials(
        self, active: DirectoryProfile
    ) -> tuple[DirectoryProfile, str | None] | None:
        profiles = [
            profile for profile in self.configuration.profiles
            if profile.profile_id != active.profile_id
            and profile.host.casefold() == active.host.casefold()
            and profile.port == active.port
            and profile.transport == active.transport
        ]
        options = ["Current connection", "One-time credentials"] + [
            f"Saved profile: {profile.name} ({profile.profile_id})" for profile in profiles
        ]
        choice, accepted = QInputDialog.getItem(
            self, "Monitoring access",
            "Choose access for this read-only monitoring request:", options, 0, False
        )
        if not accepted:
            return None
        if choice == options[1]:
            if active.transport == "plain" and QMessageBox.warning(
                self, "Unencrypted connection",
                "This connection sends the monitoring password unencrypted. "
                "Continue only on an approved non-production test network.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return None
            bind_dn, accepted = QInputDialog.getText(
                self, "Monitoring account", "Enter the monitoring account's bind identity:"
            )
            if not accepted:
                return None
            if not bind_dn.strip():
                QMessageBox.warning(self, "Monitoring account", "A bind identity is required.")
                return None
            password, accepted = QInputDialog.getText(
                self, "Monitoring password", "Enter the password for this request only:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return None
            if not password:
                QMessageBox.warning(self, "Monitoring password", "A password is required.")
                return None
            return replace(active, authentication_mode="simple", bind_dn=bind_dn.strip()), password
        selected = active if choice == options[0] else profiles[options.index(choice) - 2]
        try:
            password = self._profile_password(selected)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return None
        if selected.authentication_mode == "simple" and not password:
            password, accepted = QInputDialog.getText(
                self, "Monitoring password", f"Enter the bind password for {selected.name}:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return None
            if not password:
                QMessageBox.warning(self, "Monitoring password", "A password is required.")
                return None
        return selected, password

    def _show_monitor(self, active: DirectoryProfile) -> None:
        credentials = self._monitor_credentials(active)
        if credentials is None:
            return
        profile, password = credentials
        self.ai_output.setPlainText("Reading directory monitoring statistics.")

        def operation(cancelled: Event, progress: Callable[[int, str], None]) -> list[dict[str, Any]]:
            return self._service(profile, password).search(
                "cn=monitor", "(objectClass=*)", attributes=["*", "+"],
                cancelled=cancelled, progress=progress, max_entries=201,
            )

        def completed(entries: list[dict[str, Any]]) -> None:
            lines = ["Numeric statistics available under cn=monitor:"]
            for entry in entries[:200]:
                stats = []
                for name, values in entry["attributes"].items():
                    if not is_sensitive(name):
                        stats.extend(
                            f"  {name}: {value}" for value in values
                            if str(value).strip().lstrip("-").isdigit()
                        )
                if stats:
                    lines.append(str(entry["dn"]))
                    lines.extend(stats)
            if len(lines) == 1:
                lines.append(
                    "No numeric statistics were returned. Monitoring may be unavailable "
                    "or access may be restricted."
                )
            if len(entries) > 200:
                lines.append("Only the first 200 monitor entries are shown.")
            lines.append(
                "See Help → Data Lens help → Directory information for monitoring limitations."
            )
            self.ai_output.setPlainText("\n".join(lines))

        self._run_worker(operation, completed, on_failure=self._directory_information_error)

    def _directory_information_error(self, message: str) -> None:
        detail = f"{message}\n\nSee Help → Data Lens help → Directory information."
        self.ai_output.setPlainText(detail)
        self._show_error(detail)

    def _generate_filter(self) -> None:
        request = self.ai_request.toPlainText().strip()
        self.ai_explanation.clear()
        session_command = parse_session_command(request)
        if session_command is not None:
            if session_command.action in (COUNT_PEOPLE, MONITOR):
                self.ai_output.clear()
                self._run_directory_information(session_command.action)
            else:
                self._run_session_command(request, session_command)
            self.ai_explanation.setPlainText(
                "This session command was matched locally; no AI model was used."
            )
            return
        self._refresh_lens_controls()
        if not self.ai_generate.isEnabled():
            QMessageBox.information(self, "Data Lens", self.ai_generate.toolTip())
            return
        profile = self._active_profile
        if profile is None:
            QMessageBox.information(self, "Not connected", "Connect to a directory first.")
            return
        if (not self._model_is_approved() or not self._model_is_loaded()) and (
            not self._confirm_model_start()
        ):
            return
        if self._active_profile is not profile or self._workers:
            return
        model_path = self.configuration.preferences.model_path
        model_sha256_expected = self.configuration.preferences.model_sha256
        loaded = self._loaded_model
        model_lock = self._model_lock
        cached_schema = self._schema_names.get(profile.profile_id)
        password: str | None = None
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        selected_classes = tuple(self._known_object_classes)
        selected_attributes = tuple(self._known_attributes)
        self.ai_output.clear()
        model_name = Path(model_path).name if model_path else "(no model configured)"
        suggested_filter: str | None = None
        generator_used: LlamaCppFilterGenerator | None = None

        def operation(cancelled: Event, _progress: Callable[[int, str], None]) -> IntentOperationResult:
            nonlocal suggested_filter, generator_used
            if cancelled.is_set():
                raise LDAPOperationError("Data Lens request cancelled.")
            service = self._service(profile, password)
            schema = cached_schema
            if schema is None:
                options = service.schema_options(cancelled)
                schema = (
                    sorted(str(name) for name in options["object_classes"]),
                    sorted(str(name) for name in options["attributes"]),
                )
            object_classes = tuple(dict.fromkeys((*selected_classes, *schema[0])))
            attribute_names = tuple(dict.fromkeys((*selected_attributes, *schema[1])))
            if loaded is not None and loaded[:2] == (model_path, model_sha256_expected):
                generator = loaded[2]
            else:
                generator = LlamaCppFilterGenerator(model_path, model_sha256_expected)
            generator_used = generator
            with model_lock:
                try:
                    intent = generator.generate_intent(
                        request,
                        list(object_classes),
                        list(attribute_names),
                        cancelled=cancelled,
                    )
                except ModelOutputError:
                    words = request.split(maxsplit=1)
                    if words and words[0].casefold() in {
                        "find", "show", "list", "get", "search", "display"
                    }:
                        candidate = structured_filter(request, attribute_names)
                        if candidate is not None:
                            try:
                                validate_filter(candidate)
                            except FilterSyntaxError:
                                candidate = None
                        suggested_filter = candidate
                    raise
            if isinstance(intent, ClarifyIntent):
                prepared = intent
            else:
                backend = detect_backend(service, cancelled)
                resolver = DirectoryResolver(create_adapter(service, backend))
                prepared = prepare_intent(intent, resolver, cancelled=cancelled)
            return IntentOperationResult(intent, prepared, schema)

        def generated(result: IntentOperationResult) -> None:
            self._schema_names[profile.profile_id] = result.schema
            current = self.configuration.preferences
            if current.ai_enabled and (
                current.model_path, current.model_sha256
            ) == (model_path, model_sha256_expected) and self._active_profile is profile:
                if generator_used is not None:
                    self._loaded_model = (model_path, model_sha256_expected, generator_used)
                    self._refresh_model_status()
                self.ai_explanation.setPlainText(
                    self._explain_lens_result(result.intent, result.prepared, profile)
                )
                self._display_intent_result(profile, result.prepared)
                self._log_structured_intent(model_name, result.intent.intent)

        def failed(message: str) -> None:
            current = self.configuration.preferences
            if generator_used is not None and current.ai_enabled and (
                current.model_path, current.model_sha256
            ) == (model_path, model_sha256_expected) and self._active_profile is profile:
                self._loaded_model = (model_path, model_sha256_expected, generator_used)
                self._refresh_model_status()
            self._log_structured_intent(model_name, "unavailable")
            self.ai_explanation.setPlainText(
                "The request failed before a validated interpretation was available."
                + (
                    "\nA separate simple-search rule suggested an LDAP filter; "
                    "review it because it may not capture the complete request."
                    if suggested_filter is not None else ""
                )
            )
            if suggested_filter is None:
                self._show_error(message)
            else:
                self._offer_suggested_query(suggested_filter)

        self._run_worker(
            operation,
            generated,
            on_failure=failed,
        )

    @staticmethod
    def _clarification_text(
        clarifications: tuple[ClarificationPrompt, ...]
    ) -> str:
        lines: list[str] = []
        for prompt in clarifications:
            lines.append(prompt.question)
            lines.extend(f"  - {option.label}" for option in prompt.options)
        return "\n".join(lines)

    @staticmethod
    def _explain_lens_result(
        intent: DirectoryIntent, prepared: PreparedIntent, profile: DirectoryProfile
    ) -> str:
        lines = [
            "Validated interpretation and resolution (not the model's internal reasoning).",
            f"Model intent: {intent.intent}.",
        ]
        if isinstance(prepared, ClarifyIntent):
            lines.extend([
                f"Clarification required ({prepared.reason.value}):",
                prepared.question,
            ])
        elif isinstance(prepared, PreparedSearch):
            if isinstance(intent, SearchIntent):
                lines.append(f"Requested entry kind: {intent.kind.value}.")
                if len(intent.conditions) > 1:
                    lines.append(
                        f"Conditions combined with {'AND' if intent.match_all else 'OR'}."
                    )
                for condition in intent.conditions:
                    value = f" {condition.value!r}" if condition.value is not None else ""
                    negate = "NOT " if condition.negate else ""
                    lines.append(
                        f"Condition: {negate}{condition.term} {condition.operator.value}{value}."
                    )
            if prepared.ready and prepared.spec is not None:
                spec = prepared.spec
                lines.extend([
                    f"Resolved LDAP filter: {spec.filter}",
                    f"Base: {spec.base_dn or profile.base_dn or '(server default)'}",
                    (
                        f"Scope: {spec.scope.value}; attributes: {', '.join(spec.attributes)}; "
                        f"maximum results: {spec.size_limit}."
                    ),
                    "This is the proposed filter before any review edits.",
                ])
            else:
                lines.extend([
                    "The resolver needs clarification:",
                    MainWindow._clarification_text(prepared.clarifications),
                ])
        elif isinstance(prepared, PreparedMutation):
            if isinstance(intent, ProposedMutationIntent):
                lines.append(f"Proposed operation: {intent.mutation.operation}.")
            if prepared.clarifications:
                lines.extend([
                    "The resolver needs clarification:",
                    MainWindow._clarification_text(prepared.clarifications),
                ])
            if prepared.preview is not None:
                lines.append(f"Dry-run preview: {prepared.preview.summary}")
                lines.extend(
                    f"{issue.severity.value.upper()}: {issue.message}"
                    for issue in prepared.preview.issues
                )
                lines.append(
                    "Preview checks passed." if prepared.ready else "Preview is blocked."
                )
            lines.append("A directory change requires explicit confirmation.")
        return "\n".join(lines)

    def _display_intent_result(
        self, profile: DirectoryProfile, prepared: PreparedIntent
    ) -> None:
        if isinstance(prepared, ClarifyIntent):
            self.ai_output.setPlainText(f"Clarification needed:\n{prepared.question}")
            return
        if isinstance(prepared, PreparedSearch):
            if not prepared.ready or prepared.spec is None:
                self.ai_output.setPlainText(
                    "Clarification needed:\n"
                    + self._clarification_text(prepared.clarifications)
                )
                return
            spec = prepared.spec
            summary = (
                f"Base: {spec.base_dn or profile.base_dn or '(server default)'}\n"
                f"Scope: {spec.scope.value}\n"
                f"Return attributes: {', '.join(spec.attributes)}\n"
                f"Maximum results: {spec.size_limit}"
            )
            self.ai_output.setPlainText(f"Validated search proposal:\n{summary}")
            self.filter_edit.setPlainText(spec.filter)
            if not self._requires_ai_confirmation():
                self._run_prepared_search(profile, spec)
                return
            dialog = CommandReviewDialog(spec.filter, self, summary)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                reviewed = spec.model_copy(update={"filter": dialog.reviewed_command()})
                self.filter_edit.setPlainText(reviewed.filter)
                self._run_prepared_search(profile, reviewed)
            return

        if isinstance(prepared, PreparedMutation):
            lines = ["Mutation proposal — no changes have been applied."]
            if prepared.clarifications:
                lines.append(self._clarification_text(prepared.clarifications))
            if prepared.preview is not None:
                lines.append(f"Preview: {prepared.preview.summary}")
                lines.append(
                    "Status: preview checks passed; no change was applied."
                    if prepared.ready
                    else "Status: blocked by preview findings."
                )
                lines.extend(
                    f"{issue.severity.value.upper()}: {issue.message}"
                    for issue in prepared.preview.issues
                )
                for issue in prepared.preview.issues:
                    if issue.severity.value == "error":
                        lines.extend(f"  What to do: {step}" for step in issue_remediation(issue.code.value))
            self.ai_output.setPlainText("\n".join(lines))
            if not prepared.ready or prepared.plan is None or prepared.preview is None:
                return
            if profile.read_only:
                self.ai_output.appendPlainText("This profile is read-only; the change is blocked.")
                return
            if self._has_unsaved_changes():
                self.ai_output.appendPlainText(
                    "Save or discard pending entry edits before applying a Data Lens change."
                )
                return
            epoch = self._connection_epoch
            dialog = MutationReviewDialog(prepared.preview.model_dump_json(indent=2), self)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            audit = LocalAuditLog(self.store.path.parent / "logs" / "datalens-audit.jsonl")
            if not accepted:
                try:
                    audit.record(
                        uuid4().hex, prepared.plan.kind, prepared.preview.backend, "declined"
                    )
                except AuditLogError as error:
                    self._show_error(str(error))
                return
            if self._active_profile is not profile or self._connection_epoch != epoch:
                self._show_error("The directory connection changed; review a new proposal.")
                return
            self._apply_prepared_mutation(profile, prepared, audit)
            return

        raise TypeError(f"Unsupported prepared intent: {type(prepared).__name__}")

    def _apply_prepared_mutation(
        self, profile: DirectoryProfile, prepared: PreparedMutation, audit: LocalAuditLog
    ) -> None:
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(cancelled: Event, _progress: Callable[[int, str], None]) -> None:
            service = self._service(profile, password)
            backend = detect_backend(service, cancelled)
            DataLensMutationService(create_adapter(service, backend), audit).apply(
                prepared, confirmed=True, cancelled=cancelled
            )

        def completed(_result: None) -> None:
            self.ai_output.appendPlainText("Change applied. Refresh the directory view to see it.")
            self.statusBar().showMessage("Data Lens change applied")

        def failed(message: str) -> None:
            self.ai_output.appendPlainText(
                "Change failed or its outcome is uncertain; verify the directory before retrying."
            )
            self._show_error(message)

        self._run_worker(operation, completed, on_failure=failed)

    def _run_prepared_search(self, profile: DirectoryProfile, spec: SearchSpec) -> None:
        if self._active_profile is not profile:
            return
        if not self._confirm_leave_entry():
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return

        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> SearchResult:
            service = self._service(profile, password)
            backend = detect_backend(service, cancelled)
            adapter = create_adapter(service, backend)
            return adapter.search_entries(spec, cancelled=cancelled)

        def show_results(result: SearchResult) -> None:
            if self._active_profile is not profile:
                return
            safe_entries = []
            for entry in result.entries:
                redacted = entry.redacted()
                safe_entries.append(
                    {
                        "dn": redacted.dn,
                        "attributes": {
                            name: list(values) for name, values in redacted.attributes.items()
                        },
                    }
                )
            if not safe_entries:
                self.statusBar().showMessage("No entries found")
                QMessageBox.information(
                    self,
                    "No entries found",
                    f"No directory entries match this search.\n\nFilter: {spec.filter}\n"
                    f"Search base: {result.base_dn}",
                )
                return
            self._show_directory(
                safe_entries,
                searched_attributes=filter_attribute_names(spec.filter),
                root_dn=result.base_dn,
            )
            if result.truncated:
                self.statusBar().showMessage(
                    f"Showing the first {len(safe_entries)} matching entries."
                )

        self._run_worker(operation, show_results)

    def _log_structured_intent(self, model_name: str, intent_kind: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.ai_prompt_log.appendPlainText(
            f"=== {timestamp} — {model_name} ===\n"
            f"Structured intent: {intent_kind}\n"
            "User request, model prompt, and raw model output are not retained.\n"
        )
        logging.getLogger("datasifter.lens").info(
            "Structured intent processed type=%s", intent_kind
        )

    def _rewrite_directory_command(
        self,
        request: str,
        _example: str,
        on_success: Callable[[str], None],
        on_failure: Callable[[str], None],
    ) -> None:
        preferences = self.configuration.preferences
        path, sha256 = preferences.model_path, preferences.model_sha256
        if not preferences.ai_enabled:
            on_failure("Enable Data Lens in Application settings.")
            return
        if not path or not sha256 or not preferences.model_provenance:
            on_failure("Select and approve a local GGUF model in Application settings.")
            return
        if self._workers:
            on_failure("Wait for active operations to finish.")
            return
        loaded = self._loaded_model

        def operation(cancelled: Event, _progress: Callable[[int, str], None]) -> str:
            generator = (
                loaded[2] if loaded is not None and loaded[:2] == (path, sha256)
                else LlamaCppFilterGenerator(path, sha256)
            )
            with self._model_lock:
                return generator.rewrite_command(request, cancelled)

        self._run_worker(operation, on_success, on_failure=on_failure)

    def _log_ai_prompt(
        self, record: dict[str, Any], result: str | None = None, error: str | None = None
    ) -> None:
        lines = [
            f"=== {record['time']} — {record['model']} ===",
            f"Request: {record['request']}",
        ]
        if record["prompt"] is None:
            lines.append(
                "Prompt sent to model: none (built from attribute rules without the model)"
            )
        else:
            lines += ["Prompt sent to model:", record["prompt"], "--- end of prompt ---"]
            lines.append(f"Model output: {record['response'].strip() or '(none)'}")
        if error is not None:
            lines.append(f"Error: {error}")
        else:
            lines.append(f"Filter: {result or '(cancelled)'}")
        self.ai_prompt_log.appendPlainText("\n".join(lines) + "\n")
        logging.getLogger("datasifter.lens").info(
            "Filter generation completed outcome=%s used_model=%s",
            "failed" if error is not None else ("completed" if result else "cancelled"),
            record["prompt"] is not None,
        )

    def _clear_prompt_log(self) -> None:
        self.ai_prompt_log.clear()

    def _review_generated_filter(self, generated: str) -> None:
        if not generated:
            return
        self.ai_output.setPlainText(generated)
        if not self._requires_ai_confirmation():
            if self._active_profile is not None:
                self._run_directory_search(self._active_profile, generated)
            return
        dialog = CommandReviewDialog(generated, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.filter_edit.setPlainText(dialog.reviewed_command())
            self._execute_filter()

    def _offer_suggested_query(self, filter_expression: str) -> None:
        self.ai_output.setPlainText(
            "The local model returned an invalid structured intent.\n\n"
            f"Suggested LDAP filter:\n{filter_expression}\n\n"
            "Choose Try updated query to review and run it, or copy it into the "
            "Directory Workbench filter field and click Search."
        )
        dialog = SuggestedQueryDialog(filter_expression, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._review_generated_filter(filter_expression)

    def _set_confirmation_preference(self, enabled: bool) -> None:
        previous = self.configuration.preferences.require_ai_confirmation
        self.configuration.preferences.require_ai_confirmation = enabled
        try:
            self.store.save(self.configuration)
        except ConfigurationError as error:
            self.configuration.preferences.require_ai_confirmation = previous
            with QSignalBlocker(self.ai_confirmation_action):
                self.ai_confirmation_action.setChecked(previous)
            QMessageBox.critical(self, "Preference not saved", str(error))
            return
        if self._session_require_confirmation is None:
            blocker = QSignalBlocker(self.session_confirmation_action)
            self.session_confirmation_action.setChecked(enabled)
            del blocker

    def _set_session_confirmation(self, enabled: bool) -> None:
        self._session_require_confirmation = enabled

    def _requires_ai_confirmation(self) -> bool:
        if self._session_require_confirmation is not None:
            return self._session_require_confirmation
        return self.configuration.preferences.require_ai_confirmation

    def _choose_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose local GGUF model",
            self.configuration.preferences.model_path,
            "GGUF model files (*.gguf)",
        )
        if not path:
            return
        self._approve_model_file(path)

    def _approve_model_file(self, path: str, load_after: bool = False) -> None:
        def calculate_hash(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> str:
            return model_sha256(path, cancelled, progress)

        def approve_model(sha256: str) -> None:
            if not sha256:
                return
            response = QMessageBox.question(
                self,
                "Verify local model",
                "Before using this offline model, independently verify that its source "
                "is approved for your organization.\n\n"
                f"File: {path}\nSHA-256: {sha256}\n\n"
                "The application records your attestation and checks this hash before "
                "inference. It cannot independently authenticate the publisher.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
            provenance, accepted = QInputDialog.getText(
                self,
                "Record model source",
                "Enter the approved source you verified (for example, your organization's "
                "model catalog):",
            )
            if not accepted or not provenance.strip():
                QMessageBox.warning(
                    self,
                    "Model source required",
                    "Record the source you verified before saving this model.",
                )
                return
            preferences = self.configuration.preferences
            previous = (
                preferences.model_path,
                preferences.model_sha256,
                preferences.model_provenance,
            )
            preferences.model_path = path
            preferences.model_sha256 = sha256
            preferences.model_provenance = provenance.strip()
            try:
                self.store.save(self.configuration)
            except ConfigurationError as error:
                (
                    preferences.model_path,
                    preferences.model_sha256,
                    preferences.model_provenance,
                ) = previous
                QMessageBox.critical(self, "Model setting not saved", str(error))
                return
            if self._loaded_model is not None and self._loaded_model[:2] != (path, sha256):
                self._loaded_model = None
            if previous != (path, sha256, provenance.strip()):
                self._log_lens_event(
                    f"Model changed: {previous[0] or '(none)'} -> {path}\nSHA-256: {sha256}"
                )
            self._refresh_model_status()
            if load_after and preferences.ai_enabled:
                self._load_model()

        self._run_worker(calculate_hash, approve_model)

    def _load_model(self) -> None:
        preferences = self.configuration.preferences
        if not preferences.ai_enabled:
            QMessageBox.information(
                self, "Data Lens disabled", "Enable Data Lens in Application settings."
            )
            return
        path, sha256 = preferences.model_path, preferences.model_sha256
        if not path or not sha256 or not preferences.model_provenance:
            QMessageBox.information(
                self, "Model not approved", "Select and approve a local GGUF model first."
            )
            return
        self.model_status.setText(f"Loading model into memory...\n{path}")

        def operation(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> LlamaCppFilterGenerator | None:
            progress(0, "Verifying and loading local model")
            generator = LlamaCppFilterGenerator(path, sha256)
            return None if cancelled.is_set() else generator

        def loaded(generator: LlamaCppFilterGenerator | None) -> None:
            current = self.configuration.preferences
            if (
                generator is not None and current.ai_enabled
                and (current.model_path, current.model_sha256) == (path, sha256)
            ):
                self._loaded_model = (path, sha256, generator)
                self.statusBar().showMessage("Local model loaded and ready.", 5000)
            self._refresh_model_status()

        def failed(message: str) -> None:
            self._refresh_model_status()
            self._show_error(message)

        self._run_worker(operation, loaded, failed)

    def _manage_models(self) -> None:
        from datasifter.ui.model_dialog import ModelDialog

        dialog = ModelDialog(self.store.path.parent / "models", self)
        dialog.model_selected.connect(self._approve_model_file)
        dialog.exec()

    def _set_directory_confirmation(self, operation: str, enabled: bool) -> bool:
        field = f"require_{operation}_confirmation"
        previous = getattr(self.configuration.preferences, field)
        setattr(self.configuration.preferences, field, enabled)
        try:
            self.store.save(self.configuration)
        except ConfigurationError as error:
            setattr(self.configuration.preferences, field, previous)
            action = self.directory_confirmation_actions[operation]
            with QSignalBlocker(action):
                action.setChecked(previous)
            QMessageBox.critical(self, "Preference not saved", str(error))
            return False
        action = self.directory_confirmation_actions[operation]
        with QSignalBlocker(action):
            action.setChecked(enabled)
        return True

    def _confirm_directory_change(self, operation: str, message: str) -> bool:
        if not getattr(self.configuration.preferences, f"require_{operation}_confirmation"):
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Confirm directory change")
        dialog.setText(message)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        skip = QCheckBox("Don't ask again before renaming or moving entries")
        dialog.setCheckBox(skip)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return False
        return not skip.isChecked() or self._set_directory_confirmation(operation, False)

    def _refresh_model_status(self) -> None:
        preferences = self.configuration.preferences
        if not preferences.model_path:
            self.model_status.setText("No local model selected.")
        elif Path(preferences.model_path).suffix.casefold() != ".gguf":
            self.model_status.setText(
                "Unsupported model format. Select and approve a local .gguf model file."
            )
        elif not preferences.model_sha256 or not preferences.model_provenance:
            self.model_status.setText(
                "Model approval is incomplete. Reselect it and record its verified source."
            )
        else:
            loaded = self._loaded_model is not None and self._loaded_model[:2] == (
                preferences.model_path,
                preferences.model_sha256,
            )
            self.model_status.setText(
                f"Verified source (operator-attested): {preferences.model_provenance}\n"
                f"SHA-256: {preferences.model_sha256}\n"
                + ("Loaded in memory and ready." if loaded else "Not loaded; prompts on first use.")
            )
        if not preferences.ai_enabled:
            self.model_status.setText("Data Lens is disabled.\n" + self.model_status.text())
        if self._settings_dialog is not None:
            self._settings_dialog.model_status.setText(
                f"Model: {preferences.model_path or '(none)'}\n{self.model_status.text()}"
            )
        self._update_model_status_bar()
        self._refresh_lens_controls()

    def _update_model_status_bar(self) -> None:
        preferences = self.configuration.preferences
        if not preferences.model_path:
            text = "Model: no model chosen"
            tooltip = text
        else:
            model_path = Path(preferences.model_path)
            loaded = self._loaded_model is not None and self._loaded_model[:2] == (
                preferences.model_path,
                preferences.model_sha256,
            )
            if model_path.suffix.casefold() != ".gguf":
                state = "unsupported format"
            elif not preferences.model_sha256 or not preferences.model_provenance:
                state = "approval incomplete"
            elif not preferences.ai_enabled:
                state = "Data Lens disabled"
            elif loaded:
                state = "loaded"
            else:
                state = "not loaded"
            text = f"Model: {model_path.name} ({state})"
            tooltip = f"{text}\n{model_path}"
        self.model_status_label.setText(text)
        self.model_status_label.setToolTip(tooltip)

    def _service(self, profile: DirectoryProfile, password: str | None) -> LDAPService:
        return LDAPService(
            profile,
            password,
            self.vault.get_client_key_passphrase(profile),
            self.trust_store,
        )

    def _cancel_operation(self) -> None:
        workers = tuple(self._workers)
        if workers:
            for worker in workers:
                worker.cancel()
            self.statusBar().showMessage(
                f"Cancellation requested for {len(workers)} active operation(s)"
            )

    def _run_worker(
        self,
        operation: Callable[[Event, Callable[[int, str], None]], Any],
        on_success: Callable[[Any], None],
        on_failure: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        connection_epoch = self._connection_epoch

        def deliver_success(result: Any) -> None:
            if connection_epoch == self._connection_epoch:
                on_success(result)

        def deliver_failure(message: str) -> None:
            if connection_epoch == self._connection_epoch:
                (on_failure or self._show_error)(message)

        def deliver_progress(percent: int, message: str) -> None:
            if connection_epoch != self._connection_epoch:
                return
            if on_progress:
                on_progress(percent, message)
            else:
                self.statusBar().showMessage(f"{percent}% — {message}")

        worker = DirectoryWorker(operation)
        worker.signals.succeeded.connect(deliver_success)
        worker.signals.failed.connect(deliver_failure)
        worker.signals.progress.connect(deliver_progress)

        def deliver_finished() -> None:
            self._worker_finished(worker)
            if on_finished and connection_epoch == self._connection_epoch:
                on_finished()

        worker.signals.finished.connect(deliver_finished)
        self._workers.add(worker)
        self._refresh_lens_controls()
        self._update_save_enabled()
        self._current_worker = worker
        self.profiles.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(self._active_profile is not None)
        self.add_profile_button.setEnabled(False)
        self.edit_profile_button.setEnabled(False)
        self.delete_profile_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(True)
        self.thread_pool.start(worker)

    def _worker_finished(self, worker: DirectoryWorker) -> None:
        self._workers.discard(worker)
        self._refresh_lens_controls()
        self._update_save_enabled()
        if self._current_worker is worker:
            self._current_worker = None
        if self._current_worker is None and self._workers:
            self._current_worker = next(iter(self._workers))
        if not self._workers:
            self.profiles.setEnabled(True)
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(self._active_profile is not None)
            self.add_profile_button.setEnabled(True)
            self.edit_profile_button.setEnabled(True)
            self.delete_profile_button.setEnabled(bool(self.configuration.profiles))
            self.cancel_button.setEnabled(False)
            self.progress.setVisible(False)

    def _disconnect(self) -> None:
        if self._workers:
            reply = QMessageBox.question(
                self,
                "Disconnect from directory",
                f"Cancel {len(self._workers)} active operation(s) and disconnect? "
                "A submitted directory change may already have completed; verify the "
                "directory before retrying.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if not self._confirm_leave_entry():
            return

        self._connection_epoch += 1
        self._cancel_operation()
        self.ai_explanation.clear()
        profile = self._active_profile
        self._active_profile = None
        self._connecting = False
        self.data_lens_tab.set_service(None)
        self._schema_options = {}
        self._known_object_classes = []
        self._known_attributes = []
        if profile is not None:
            self._schema_names.pop(profile.profile_id, None)
        self.schema_table.setRowCount(0)
        with QSignalBlocker(self.schema_object_class):
            self.schema_object_class.clear()
            self.schema_object_class.addItem("All object classes", "")
        self.schema_status.setText("Connect to a directory before loading its schema.")
        self.warning_banner.setVisible(False)
        self.transport_warning.clear()
        self.transport_warning.setVisible(False)
        self.tree.clear()
        self.attributes.setRowCount(0)
        self.dn_field.clear()
        self._selected_item = None
        self._selected_dn = None
        self._search_attributes.clear()
        self._original_attributes.clear()
        self._readonly_attribute_names.clear()
        self.copy_dn_button.setEnabled(False)
        self._set_mutations_enabled(False)
        self.disconnect_button.setEnabled(False)
        self.statusBar().showMessage("Disconnected")
        self._refresh_connection_controls()
        self._refresh_lens_controls()

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage("Operation failed")
        CopyableTextDialog("DataSifter operation failed", message, self).exec()

    def _open_data_lens_help(self) -> None:
        CopyableTextDialog("Data Lens help", format_help_catalog(get_help_catalog()), self).exec()

    def _append_log_message(self, message: str) -> None:
        self.log_output.appendPlainText(message)

    def closeEvent(self, event: Any) -> None:
        if not self._confirm_leave_entry():
            event.ignore()
            return
        for worker in self._workers:
            worker.cancel()
        logger.removeHandler(self.log_handler)
        super().closeEvent(event)
