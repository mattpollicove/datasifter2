"""Phase 1 PyQt6 desktop workspace."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import replace
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
    QTextCursor,
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
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
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
from datasifter.ai.local_model import LlamaCppFilterGenerator, model_sha256
from datasifter.build_info import build_label
from datasifter.config import (
    ConfigurationError,
    ConfigurationStore,
    CredentialVault,
    DirectoryProfile,
)
from datasifter.directory.filters import (
    FilterSyntaxError,
    filter_attribute_names,
    validate_filter,
)
from datasifter.directory.service import LDAPService
from datasifter.directory.worker import DirectoryWorker
from datasifter.security.certificates import (
    CertificateTrustError,
    CertificateTrustStore,
    certificate_details,
)
from datasifter.ui.settings_dialog import SettingsDialog

logger = logging.getLogger("datasifter.protocol")

# Tree item role: whether cached attributes were read with operational attributes ("+").
_CACHED_OPERATIONAL_ROLE = Qt.ItemDataRole.UserRole + 6
# Worker progress value that carries the model prompt instead of a generated token.
_PROMPT_PROGRESS = -1
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
        self, parent: QWidget | None = None, profile: DirectoryProfile | None = None
    ) -> None:
        super().__init__(parent)
        self._original_profile = profile
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
        layout.addRow(self.test_authentication_button)
        password_options = QHBoxLayout()
        password_options.addWidget(self.show_password)
        password_options.addWidget(self.save_password)
        password_options.addStretch()
        layout.addRow(password_options)
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
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
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

    def accept(self) -> None:
        if self._test_worker is not None:
            return
        try:
            self.profile = self._profile_from_fields()
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


class CommandReviewDialog(QDialog):
    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review LDAP filter")
        self.resize(560, 220)
        layout = QVBoxLayout(self)
        self.command = QLineEdit(command)
        self.validation = QLabel()
        self.execute_button = QPushButton("Execute")
        self.cancel_button = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.execute_button)
        buttons.addWidget(self.cancel_button)
        layout.addWidget(QLabel("Review and edit the generated LDAP filter before execution:"))
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
        self._active_profile: DirectoryProfile | None = None
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

    def _build_ui(self) -> None:
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.profiles = QComboBox()
        self.list_profiles_button = QPushButton("List profiles")
        self.add_profile_button = QPushButton("Add profile")
        self.edit_profile_button = QPushButton("Edit profile")
        self.connect_button = QPushButton("Connect")
        self.cancel_button = QPushButton("Cancel active operations")
        self.cancel_button.setEnabled(False)
        self.list_profiles_button.clicked.connect(self._list_profiles)
        self.add_profile_button.clicked.connect(self._add_profile)
        self.edit_profile_button.clicked.connect(self._edit_profile)
        self.connect_button.clicked.connect(self._connect)
        self.cancel_button.clicked.connect(self._cancel_operation)
        toolbar_layout.addWidget(QLabel("Directory"))
        toolbar_layout.addWidget(self.profiles, 1)
        toolbar_layout.addWidget(self.list_profiles_button)
        toolbar_layout.addWidget(self.add_profile_button)
        toolbar_layout.addWidget(self.edit_profile_button)
        toolbar_layout.addWidget(self.connect_button)
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

        self.filter_edit = QPlainTextEdit("(objectClass=*)")
        self.filter_edit.setPlaceholderText("RFC 4515 LDAP filter")
        self.filter_edit.setMaximumHeight(58)
        self.filter_highlighter = LDAPFilterHighlighter(self.filter_edit.document())
        self.filter_execute = QPushButton("Search")
        self.filter_execute.clicked.connect(self._execute_filter)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.filter_execute)

        self.ai_request = LensRequestEdit()
        self.ai_request.setPlaceholderText("Describe the directory entries you want to find.")
        self.ai_request.setToolTip("Enter: generate filter. Shift+Enter: new line.")
        self.ai_request.submitted.connect(
            lambda: self.ai_generate.click() if self.ai_generate.isEnabled() else None
        )
        self.ai_request.textChanged.connect(self._refresh_lens_controls)
        self.ai_request.setMaximumHeight(90)
        self.ai_generate = QPushButton("Generate local filter")
        self.ai_generate.clicked.connect(self._generate_filter)
        self.ai_output = QPlainTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setPlaceholderText("Generated LDAP filter will appear here.")
        self.ai_output.setMaximumHeight(76)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.ai_panel = QWidget()
        ai_layout = QVBoxLayout(self.ai_panel)
        ai_layout.addWidget(QLabel("Directory Lens"))
        ai_layout.addWidget(self.ai_request)
        ai_layout.addWidget(self.ai_generate)
        ai_layout.addWidget(self.ai_output)
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
        tabs.addTab(splitter, "Directory workspace")
        tabs.addTab(schema_panel, "Schema inspector")
        tabs.addTab(self.log_output, "Protocol log")
        tabs.addTab(prompts_panel, "Directory Lens log")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("No directory connected")
        self.version_label = QLabel(f"Version {__version__}")
        self.statusBar().addPermanentWidget(self.version_label)

        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction("Application settings...", self._open_settings)
        lens_menu = settings_menu.addMenu("Directory Lens")
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
                "Directory Lens enabled" if preferences.ai_enabled else "Directory Lens disabled"
            )
        self._apply_search_highlight()
        self._refresh_model_status()
        dialog.accept()

    def _refresh_lens_controls(self) -> None:
        preferences = self.configuration.preferences
        approved = bool(
            preferences.model_path and preferences.model_sha256 and preferences.model_provenance
        )
        reason = ""
        if not preferences.ai_enabled:
            reason = "Enable Directory Lens in Application settings."
        elif not approved:
            reason = "Select and approve a local GGUF model in Application settings."
        elif self._workers:
            reason = "Wait for active operations to finish."
        elif self._active_profile is None:
            reason = "Connect to a directory first."
        elif not self.ai_request.toPlainText().strip():
            reason = "Describe the directory entries you want to find."
        self.ai_generate.setEnabled(not reason)
        self.ai_generate.setToolTip(reason or "Generate a local LDAP filter (Enter)")
        self.ai_request.setEnabled(preferences.ai_enabled)
        if self._settings_dialog is not None:
            self._settings_dialog.set_busy(
                bool(self._workers), preferences.ai_enabled, approved
            )

    def _log_lens_event(self, message: str) -> None:
        self.ai_prompt_log.appendPlainText(
            f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n{message}\n"
        )

    def _refresh_profiles(self) -> None:
        self.profiles.clear()
        for profile in self.configuration.profiles:
            self.profiles.addItem(profile.name, profile.profile_id)
        self.list_profiles_button.setEnabled(bool(self.configuration.profiles))

    def _list_profiles(self) -> None:
        profiles = self.configuration.profiles
        if not profiles:
            return
        lines = [
            f"{profile.name} — {profile.host}:{profile.port} "
            f"({profile.transport}, {profile.authentication_mode.replace('_', ' ')})"
            for profile in profiles
        ]
        QMessageBox.information(self, "Saved profiles", "\n".join(lines))

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
        dialog = ProfileDialog(self)
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
        dialog = ProfileDialog(self, profile)
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
        self.configuration.profiles[index] = updated
        try:
            self._save_profile_configuration(dialog, updated)
        except (ConfigurationError, CertificateTrustError) as error:
            self.configuration.profiles[index] = profile
            QMessageBox.critical(self, "Profile not saved", str(error))
            return
        try:
            if updated.authentication_mode == "simple":
                self._save_profile_password(
                    updated,
                    dialog.password.text(),
                    dialog.save_password.isChecked(),
                )
            else:
                self._save_profile_password(updated, "", False)
            self.vault.store_client_key_passphrase(updated, dialog.client_key_passphrase.text())
        except ConfigurationError as error:
            QMessageBox.critical(
                self, "Credential update failed",
                f"Profile settings were saved, but credentials were not fully updated: {error}",
            )
        if self._active_profile and self._active_profile.profile_id == updated.profile_id:
            self._active_profile = None
            self._selected_dn = None
            self.tree.clear()
            self.attributes.setRowCount(0)
            self._known_object_classes = []
            self._known_attributes = []
            self._schema_names.pop(updated.profile_id, None)
            self._set_mutations_enabled(False)
            self.statusBar().showMessage("Profile updated. Connect again to browse the directory.")
        self._refresh_profiles()
        self.profiles.setCurrentIndex(self.profiles.findData(updated.profile_id))

    def _save_dialog_trust(self, dialog: ProfileDialog, profile: DirectoryProfile) -> None:
        if dialog.trusted_certificate:
            draft = replace(profile, tofu_fingerprint="")
            self.trust_store.accept_tofu(draft, dialog.trusted_certificate)
            profile.tofu_fingerprint = draft.tofu_fingerprint

    def _save_profile_configuration(
        self, dialog: ProfileDialog, profile: DirectoryProfile
    ) -> None:
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

    def _connect(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            QMessageBox.information(self, "No profile", "Add a directory profile first.")
            return
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
        if profile is None or (
            self._active_profile
            and profile.profile_id != self._active_profile.profile_id
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
        self._set_mutations_enabled(
            self._active_profile is not None and not self._active_profile.read_only
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

    def _run_directory_search(self, profile: DirectoryProfile, expression: str) -> None:
        if not self._confirm_leave_entry():
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
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

    def _show_directory(
        self,
        entries: list[dict[str, Any]],
        page_cookie: bytes | None = None,
        operational: bool = False,
        searched_attributes: set[str] | None = None,
    ) -> None:
        self.tree.clear()
        self._search_attributes = set(searched_attributes or ())
        self._selected_dn = None
        self.attributes.setRowCount(0)
        base_dn = self._active_profile.base_dn if self._active_profile else ""
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
        form.addRow("Distinguished name", dn)
        form.addRow("Object classes (comma-separated)", object_classes)
        form.addRow("Directory object-class schema", QLabel(class_details))
        form.addRow("Directory attribute schema", QLabel(attribute_details))
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
        def operation(
            cancelled: Event,
            _progress: Callable[[int, str], None],
        ) -> None:
            self._service(profile, password).add_entry(
                dn.text().strip(),
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

    def _save_entry_changes(self) -> None:
        profile = self._active_profile
        if profile is None or profile.read_only or not self._selected_dn:
            return
        changes = self._attribute_changes()
        if not changes:
            self.statusBar().showMessage("No attribute changes to save")
            return
        try:
            password = self._profile_password(profile)
        except ConfigurationError as error:
            QMessageBox.critical(self, "Credential error", str(error))
            return
        distinguished_name = self._selected_dn

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

    def _generate_filter(self) -> None:
        self._refresh_lens_controls()
        if not self.ai_generate.isEnabled():
            QMessageBox.information(self, "Directory Lens", self.ai_generate.toolTip())
            return
        profile = self._active_profile
        if profile is None:
            QMessageBox.information(self, "Not connected", "Connect to a directory first.")
            return
        model_path = self.configuration.preferences.model_path
        model_sha256_expected = self.configuration.preferences.model_sha256
        request = self.ai_request.toPlainText()
        loaded = self._loaded_model
        model_lock = self._model_lock
        cached_schema = self._schema_names.get(profile.profile_id)
        password: str | None = None
        if cached_schema is None:
            try:
                password = self._profile_password(profile)
            except ConfigurationError as error:
                QMessageBox.critical(self, "Credential error", str(error))
                return
        selected_classes = list(self._known_object_classes)
        selected_attributes = list(self._known_attributes)
        self.ai_output.clear()
        record: dict[str, Any] = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": Path(model_path).name if model_path else "(no model configured)",
            "request": request.strip(),
            "prompt": None,
            "response": "",
        }

        def operation(
            cancelled: Event,
            progress: Callable[[int, str], None],
        ) -> dict[str, Any]:
            if cancelled.is_set():
                return {}
            schema = cached_schema
            if schema is None:
                # Structural schema names only; entry values are never sent to the model.
                options = self._service(profile, password).schema_options(cancelled)
                schema = (list(options["object_classes"]), list(options["attributes"]))
            if loaded is not None and loaded[:2] == (model_path, model_sha256_expected):
                generator = loaded[2]
            else:
                generator = LlamaCppFilterGenerator(model_path, model_sha256_expected)
            # A llama.cpp context is not safe for concurrent generation.
            with model_lock:
                generated = generator.generate_filter(
                    request,
                    selected_classes + schema[0],
                    selected_attributes + schema[1],
                    on_token=lambda token: progress(0, token),
                    cancelled=cancelled,
                    on_prompt=lambda prompt: progress(_PROMPT_PROGRESS, prompt),
                )
            return {
                "filter": "" if cancelled.is_set() else generated,
                "schema": schema,
            }

        def generated(result: dict[str, Any]) -> None:
            if result.get("schema") is not None:
                self._schema_names[profile.profile_id] = result["schema"]
            self._log_ai_prompt(record, result=result.get("filter", ""))
            current = self.configuration.preferences
            if current.ai_enabled and (
                current.model_path, current.model_sha256
            ) == (model_path, model_sha256_expected) and self._active_profile is profile:
                self._review_generated_filter(result.get("filter", ""))

        def failed(message: str) -> None:
            self._log_ai_prompt(record, error=message)
            self._show_error(message)

        def display_token(percent: int, token: str) -> None:
            if percent == _PROMPT_PROGRESS:
                record["prompt"] = token
                return
            record["response"] += token
            cursor = self.ai_output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(token)
            self.ai_output.setTextCursor(cursor)

        self._run_worker(
            operation,
            generated,
            on_failure=failed,
            on_progress=display_token,
        )

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
            "GGUF model files (*.gguf);;All files (*)",
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
                self, "Directory Lens disabled", "Enable Directory Lens in Application settings."
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
                + ("Loaded in memory and ready." if loaded else "Not loaded; loads on first use.")
            )
        if not preferences.ai_enabled:
            self.model_status.setText("Directory Lens is disabled.\n" + self.model_status.text())
        if self._settings_dialog is not None:
            self._settings_dialog.model_status.setText(
                f"Model: {preferences.model_path or '(none)'}\n{self.model_status.text()}"
            )
        self._refresh_lens_controls()

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
    ) -> None:
        worker = DirectoryWorker(operation)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(on_failure or self._show_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        else:
            worker.signals.progress.connect(
                lambda percent, message: self.statusBar().showMessage(
                    f"{percent}% — {message}"
                )
            )
        worker.signals.finished.connect(lambda: self._worker_finished(worker))
        self._workers.add(worker)
        self._refresh_lens_controls()
        self._update_save_enabled()
        self._current_worker = worker
        self.profiles.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.add_profile_button.setEnabled(False)
        self.edit_profile_button.setEnabled(False)
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
            self.add_profile_button.setEnabled(True)
            self.edit_profile_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.progress.setVisible(False)

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage("Operation failed")
        QMessageBox.warning(self, "DataSifter operation failed", message)

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
