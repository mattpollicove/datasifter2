import socket
from pathlib import Path

import pytest
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from datasifter import __version__
from datasifter.ai.local_model import ModelOutputError
from datasifter.build_info import build_label
from datasifter.config import (
    ConfigurationError,
    ConfigurationStore,
    DirectoryProfile,
    LocalConfiguration,
    Preferences,
)
from datasifter.ui import about_dialog as about_dialog_module
from datasifter.ui import main_window as main_window_module
from datasifter.ui.about_dialog import GITHUB_ISSUES_URL, AboutDialog
from datasifter.ui.main_window import (
    AttributeValueDelegate,
    CommandReviewDialog,
    LDAPFilterHighlighter,
    MainWindow,
    ProfileDialog,
    ProfileWizard,
    SuggestedQueryDialog,
)


def test_ui_displays_current_build_version(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert window.windowTitle() == f"DataSifter 2.0 - v{__version__} ({build_label()})"
    assert build_label().startswith("source 20")
    assert window.version_label.text() == f"Version {__version__}"
    assert window.model_status_label.text() == "Model: no model chosen"
    window.statusBar().showMessage("Operation failed")
    assert window.version_label.text() == f"Version {__version__}"
    window.close()


def test_status_bar_shows_selected_model_and_load_state(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"

    window._refresh_model_status()
    assert window.model_status_label.text() == "Model: approved.gguf (not loaded)"
    assert str(tmp_path / "approved.gguf") in window.model_status_label.toolTip()

    window._loaded_model = (preferences.model_path, preferences.model_sha256, object())
    window._refresh_model_status()
    assert window.model_status_label.text() == "Model: approved.gguf (loaded)"
    window.close()


def test_help_menu_opens_about_dialog(qtbot, tmp_path, monkeypatch):
    opened = []

    class FakeAboutDialog:
        def __init__(self, parent):
            self.parent = parent

        def exec(self):
            opened.append(self)

    monkeypatch.setattr(main_window_module, "AboutDialog", FakeAboutDialog)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)

    help_menu = next(
        action.menu() for action in window.menuBar().actions() if action.text() == "Help"
    )
    about_action = next(
        action for action in help_menu.actions() if action.text() == "About DataSifter..."
    )
    about_action.trigger()

    assert len(opened) == 1
    assert opened[0].parent is window
    window.close()


def test_data_lens_help_menu_opens_copyable_catalog(qtbot, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        main_window_module.CopyableTextDialog,
        "exec",
        lambda dialog: seen.append((dialog.windowTitle(), dialog.text.toPlainText())),
    )
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    help_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "Help")
    next(action for action in help_menu.actions() if action.text() == "Data Lens help...").trigger()
    assert seen[0][0] == "Data Lens help"
    assert "Attribute aliases" in seen[0][1]
    assert "explicit confirmation" in seen[0][1]
    window.close()


def test_error_dialog_uses_copyable_text(qtbot, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        main_window_module.CopyableTextDialog,
        "exec",
        lambda dialog: seen.append(dialog.text.toPlainText()),
    )
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._show_error("Authentication failed.\n\nWhat to do:\n- Check the bind identity.")
    assert "Check the bind identity." in seen[0]
    window.close()


def test_worker_failure_reaches_ui_with_remediation(qtbot, tmp_path, monkeypatch):
    from datasifter.directory.service import LDAPResultError

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr(window, "_show_error", messages.append)

    def fail(_cancelled, _progress):
        raise LDAPResultError("invalidCredentials", 49)

    window._run_worker(fail, lambda _result: pytest.fail("operation must fail"))
    qtbot.waitUntil(lambda: not window._workers)
    assert "authentication failed" in messages[0]
    assert "bind identity" in messages[0]
    window.close()


def test_blocked_mutation_preview_shows_remediation(qtbot, tmp_path, standard):
    from datalens_support import ADA

    from datasifter.datalens import DeletePlan, PreparedMutation

    adapter = standard.adapter(profile_read_only=True)
    plan = DeletePlan(dn=ADA)
    prepared = PreparedMutation(plan=plan, preview=adapter.execute(plan).preview)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._display_intent_result(adapter._service.profile, prepared)
    assert "What to do:" in window.ai_output.toPlainText()
    assert "writable profile" in window.ai_output.toPlainText()
    window.close()


def test_about_dialog_shows_privacy_license_and_github_support(qtbot):
    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    assert dialog.tabs.tabText(1) == "Privacy"
    assert "not the request, prompt, or raw model response" in dialog.privacy_text.toPlainText()
    assert dialog.tabs.tabText(2) == "MIT License"
    assert dialog.license_text.toPlainText() == (
        Path(__file__).resolve().parents[1] / "LICENSE"
    ).read_text(encoding="utf-8")
    assert GITHUB_ISSUES_URL in dialog.support_link.text()
    assert dialog.support_link.openExternalLinks()
    dialog.close()


def test_about_dialog_reads_documents_from_standalone_bundle(qtbot, tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[1]
    for name in ("LICENSE", "PRIVACY.md"):
        (tmp_path / name).write_text(
            (project_root / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(about_dialog_module.sys, "_MEIPASS", str(tmp_path), raising=False)

    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    assert dialog.tabs.tabText(2) == "MIT License"
    assert dialog.license_text.toPlainText() == (tmp_path / "LICENSE").read_text(
        encoding="utf-8"
    )
    dialog.close()


def test_startup_and_disconnected_lens_do_not_contact_ldap(qtbot, tmp_path, monkeypatch):
    from datasifter import __main__
    from datasifter.directory.service import LDAPService
    from datasifter.ui.data_lens_tab import CommandClarificationDialog

    assert __main__.MainWindow is MainWindow
    monkeypatch.setattr(
        LDAPService, "search", lambda *_args, **_kwargs: pytest.fail("Unexpected LDAP search")
    )
    # The command below is incomplete, which opens a modal dialog; dismiss it.
    monkeypatch.setattr(
        CommandClarificationDialog, "exec", lambda _dialog: QDialog.DialogCode.Rejected
    )
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert window.tabs.tabText(0) == "Directory Workbench"
    assert window.tabs.indexOf(window.data_lens_tab) == -1
    window.show()
    tab = window.data_lens_tab
    assert [
        tab.create_btn.text(),
        tab.update_btn.text(),
        tab.delete_btn.text(),
        tab.move_btn.text(),
        tab.refresh_btn.text(),
        tab.cmd_execute.text(),
    ] == [
        "Create entry",
        "Update entry",
        "Delete entry",
        "Move entry",
        "Refresh tree",
        "Run command",
    ]
    assert tab._lens is None
    assert tab._service is None
    assert not tab.cmd_execute.isEnabled()
    assert not tab.create_btn.isEnabled()
    tab.cmd_input.setText("delete entry cn=Test,dc=example,dc=com")
    tab._execute_command()
    assert "Connect to a directory" in tab.log.toPlainText()
    window.close()


def test_ldif_actions_follow_connection_write_access_and_selection(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)

    assert not window.import_ldif_action.isEnabled()
    assert not window.export_ldif_action.isEnabled()

    profile = DirectoryProfile(name="Writable", host="ldap.test", base_dn="dc=test")
    window._active_profile = profile
    window._refresh_connection_controls()
    assert window.import_ldif_action.isEnabled()
    assert not window.export_ldif_action.isEnabled()

    window._selected_dn = "cn=Alice,dc=test"
    window._refresh_connection_controls()
    assert window.export_ldif_action.isEnabled()

    profile.read_only = True
    window._refresh_connection_controls()
    assert not window.import_ldif_action.isEnabled()
    assert window.export_ldif_action.isEnabled()
    window.close()


@pytest.mark.parametrize("import_fails", [False, True])
def test_ldif_refresh_reconnects_import_profile_not_selected_profile(
    qtbot, tmp_path, monkeypatch, import_fails
):
    from threading import Event

    from datasifter.directory.ldif import LDIFRecord
    from datasifter.directory.service import LDAPImportError

    active = DirectoryProfile(name="Active", host="active.test", base_dn="dc=test")
    selected = DirectoryProfile(name="Selected", host="selected.test", base_dn="dc=test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[active, selected]))
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    active = window.configuration.profiles[0]
    window._active_profile = active
    window.profiles.setCurrentIndex(1)
    records = [LDIFRecord(dn="cn=Alice,dc=test", change_type="delete")]
    imports = []
    reconnects = []
    errors = []

    class FakeService:
        def apply_ldif_records(self, received, cancelled=None, progress=None):
            imports.append(received)
            if import_fails:
                raise LDAPImportError("Earlier changes may have been applied.")
            return len(received)

    def service(profile, _password):
        assert profile.profile_id == active.profile_id
        return FakeService()

    def run_now(operation, success, failure=None, on_progress=None, on_finished=None):
        if on_finished is None:
            reconnects.append(window._active_profile)
            return
        try:
            result = operation(Event(), lambda *_args: None)
        except LDAPImportError as error:
            failure(str(error))
        else:
            success(result)
        on_finished()

    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: QMessageBox.StandardButton.Apply)
    monkeypatch.setattr(
        QMessageBox, "critical", lambda _parent, _title, text: errors.append(text)
    )
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", service)
    monkeypatch.setattr(window, "_run_worker", run_now)

    window._confirm_ldif_import(active, None, records)

    assert imports == [records]
    assert reconnects == [active]
    assert window._selected_profile().profile_id == selected.profile_id
    assert bool(errors) == import_fails
    window.close()


def test_export_selected_entry_writes_validated_ldif(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from datasifter.directory.ldif import read_ldif

    profile = DirectoryProfile(name="Writable", host="ldap.test", base_dn="dc=test")
    distinguished_name = "cn=Alice,dc=test"
    target = tmp_path / "selected-entry.ldif"
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = profile
    window._selected_dn = distinguished_name
    window._refresh_connection_controls()

    class FakeService:
        def read_entry(self, dn, cancelled=None):
            assert dn == distinguished_name
            assert cancelled is not None
            return {
                "dn": dn,
                "attributes": {"objectClass": ["top"], "cn": ["Alice"]},
            }

    monkeypatch.setattr(window, "_profile_password", lambda _profile: "secret")
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(target), "LDIF files (*.ldif)"),
    )

    def run_now(operation, success, _failure=None, **_kwargs):
        success(operation(Event(), lambda *_args: None))

    monkeypatch.setattr(window, "_run_worker", run_now)
    window._export_selected_ldif()

    records = read_ldif(target)
    assert len(records) == 1
    assert records[0].dn == distinguished_name
    assert records[0].attributes == {"objectClass": ["top"], "cn": ["Alice"]}
    window.close()


def test_connection_prepares_lens_with_real_profile_and_credentials(qtbot, tmp_path, monkeypatch):
    from datasifter.directory.service import LDAPService

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert window.tabs.indexOf(window.data_lens_tab) == -1
    assert not window.filter_edit.isEnabled()
    assert not window.filter_execute.isEnabled()
    assert not window.schema_refresh_button.isEnabled()
    profile = DirectoryProfile(name="Connected", host="ldap.test", base_dn="dc=test")
    window.configuration.profiles.append(profile)
    window._active_profile = profile
    service = LDAPService(profile, "session-password", "key-passphrase", window.trust_store)
    monkeypatch.setattr(window, "_profile_password", lambda selected: service.password)

    def make_service(selected, password):
        assert selected is profile
        assert password == "session-password"
        return service

    monkeypatch.setattr(window, "_service", make_service)
    window._connection_result({"profile_id": profile.profile_id, "entries": []})
    assert window.data_lens_tab._service is service
    assert window.data_lens_tab._lens is None
    assert window.data_lens_tab.create_btn.isEnabled()
    assert window.filter_edit.isEnabled()
    assert window.filter_execute.isEnabled()
    assert window.ai_panel.isEnabled()
    assert window.schema_refresh_button.isEnabled()
    window.close()


def test_disconnect_clears_session_and_ignores_late_connection_result(
    qtbot, tmp_path, monkeypatch
):
    from datasifter.directory.service import LDAPService

    profile = DirectoryProfile(name="Connected", host="ldap.test", base_dn="dc=test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    window._active_profile = profile
    window.data_lens_tab.set_service(LDAPService(profile, None))
    window._show_directory([{"dn": "cn=Matt,dc=test", "attributes": {"cn": ["Matt"]}}])
    workers = []
    callbacks = []
    monkeypatch.setattr(window.thread_pool, "start", workers.append)
    window._run_worker(lambda *_args: "late result", callbacks.append)
    window.disconnect_button.setEnabled(True)

    window.disconnect_button.click()
    workers[0].signals.succeeded.emit("late result")
    workers[0].signals.finished.emit()
    window._connection_result(
        {
            "profile_id": profile.profile_id,
            "entries": [{"dn": "cn=Late,dc=test", "attributes": {}}],
        }
    )

    assert workers[0].cancelled.is_set()
    assert callbacks == []
    assert window._active_profile is None
    assert window.disconnect_button.isEnabled() is False
    assert window.data_lens_tab._service is None
    assert window.tree.topLevelItemCount() == 0
    assert window.attributes.rowCount() == 0
    assert window.configuration.profiles == [profile]
    assert window.statusBar().currentMessage() == "Disconnected"
    window.close()


def test_data_lens_initializes_on_show_and_resets_service(qtbot, monkeypatch):
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QTreeView, QWidget

    from datasifter.directory.service import LDAPService
    from datasifter.ui import data_lens_tab

    created = []

    class FakeLens(QWidget):
        succeeded = pyqtSignal(object)
        failed = pyqtSignal(str)

        def __init__(self, service, root_dn):
            super().__init__()
            self.view = QTreeView(self)
            created.append((service, root_dn))

    monkeypatch.setattr(data_lens_tab, "DataLens", FakeLens)
    profile = DirectoryProfile(name="Read-only", host="ldap.test", base_dn="dc=test", read_only=True)
    service = LDAPService(profile, None)
    tab = data_lens_tab.DataLensTab(service)
    qtbot.addWidget(tab)
    assert created == []
    assert not tab.create_btn.isEnabled()
    assert not tab.cmd_execute.isEnabled()
    tab.show()
    assert created == [(service, "dc=test")]
    tab.lens.failed.emit("Search failed")
    assert "Error: Search failed" in tab.log.toPlainText()
    tab.set_service(None)
    assert tab._lens is None
    assert not tab.delete_btn.isEnabled()
    assert tab.connection_status.isVisible()
    replacement = LDAPService(DirectoryProfile(name="Other", host="other.test", base_dn="dc=other"), None)
    tab.set_service(replacement)
    assert created[-1] == (replacement, "dc=other")
    assert tab.create_btn.isEnabled()
    tab.close()


def test_lens_enter_submits_and_shift_enter_inserts_newline(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.show()
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window.ai_request.setPlainText("surname starts with b")
    assert window.ai_generate.isEnabled()
    assert window.ai_generate.text() == "Run request"
    submitted = []
    window.ai_generate.clicked.disconnect()
    window.ai_generate.clicked.connect(lambda: submitted.append(True))
    qtbot.keyClick(window.ai_request, Qt.Key.Key_Return)
    assert submitted == [True]
    assert window.ai_request.toPlainText() == "surname starts with b"
    qtbot.keyClick(window.ai_request, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in window.ai_request.toPlainText()
    assert submitted == [True]
    window.ai_generate.setEnabled(False)
    qtbot.keyClick(window.ai_request, Qt.Key.Key_Enter)
    assert submitted == [True]
    window.close()


def test_lens_requires_approved_model_and_rejects_disabled_ai(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window.ai_request.setPlainText("surname starts with b")
    assert window.ai_generate.isEnabled()
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    window._refresh_model_status()
    assert window.ai_generate.isEnabled()
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._refresh_model_status()
    assert window.ai_generate.isEnabled()
    preferences.ai_enabled = False
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(
        window, "_run_worker", lambda *_args, **_kwargs: pytest.fail("AI must not run")
    )
    window._generate_filter()
    window._load_model()
    assert len(messages) == 2
    assert not window.ai_generate.isEnabled()
    window.close()


def test_startup_offers_settings_or_loads_approved_model(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.show()
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: prompts.append(_args[2]) or QMessageBox.StandardButton.Yes,
    )
    actions = []
    monkeypatch.setattr(window, "_open_settings", lambda: actions.append("settings"))
    monkeypatch.setattr(window, "_load_model", lambda: actions.append("load"))

    window._prompt_model_on_startup()
    assert actions == ["settings"]
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._prompt_model_on_startup()
    assert actions == ["settings", "load"]
    assert "not loaded" in prompts[-1]
    window._loaded_model = (preferences.model_path, preferences.model_sha256, object())
    window._prompt_model_on_startup()
    preferences.ai_enabled = False
    window._loaded_model = None
    window._prompt_model_on_startup()
    assert actions == ["settings", "load"]
    window.close()


def test_declining_model_load_keeps_request_without_starting_worker(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    request = "find users with surname starting with s"
    window.ai_request.setPlainText(request)
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: prompts.append(_args[2]) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        window, "_run_worker", lambda *_args, **_kwargs: pytest.fail("Model must not start")
    )

    window._generate_filter()
    window._generate_filter()

    assert len(prompts) == 2
    assert window.ai_request.toPlainText() == request
    assert window._loaded_model is None
    window.close()


def test_accepted_request_load_is_reused_for_later_requests(qtbot, tmp_path, monkeypatch):
    from datasifter.datalens import ClarifyIntent

    created = []
    prompts = []

    class FakeGenerator:
        def __init__(self, path, sha256):
            created.append((path, sha256))

        def generate_intent(self, *_args, **_kwargs):
            return ClarifyIntent(intent="clarify", question="Which users?")

    class FakeService:
        def schema_options(self, _cancelled):
            return {"object_classes": {}, "attributes": ["sn"]}

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    monkeypatch.setattr(main_window_module, "LlamaCppFilterGenerator", FakeGenerator)
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: prompts.append(_args[2]) or QMessageBox.StandardButton.Yes,
    )
    window.ai_request.setPlainText("find users")

    for _ in range(2):
        window._generate_filter()
        qtbot.waitUntil(lambda: not window._workers)

    assert len(prompts) == 1
    assert created == [(preferences.model_path, preferences.model_sha256)]
    assert window._model_is_loaded()
    assert "Which users?" in window.ai_output.toPlainText()
    window.close()


def test_command_rewrite_requires_approved_model(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    failures = []
    window._rewrite_directory_command("create", "create user example", lambda _: None, failures.append)
    assert "approve" in failures[0]
    assert not window._workers
    window.close()


def test_main_request_uses_structured_intent_without_logging_request(
    qtbot, tmp_path, monkeypatch
):
    from datasifter.datalens import CreateProposal, DirectoryBackend, ProposedMutationIntent

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    request = "create user Matt Pollicove in the people OU, cn is mattp, password is example"
    prefs = window.configuration.preferences
    prefs.model_path = str(tmp_path / "approved.gguf")
    prefs.model_sha256 = "a" * 64
    prefs.model_provenance = "Test catalog"
    generated = []

    class FakeGenerator:
        def generate_intent(self, text, classes, attributes, cancelled=None):
            generated.append((text, classes, attributes))
            return ProposedMutationIntent(
                intent="proposed_mutation",
                mutation=CreateProposal(
                    operation="create", name="mattp", attributes={"password": "example"}
                ),
            )

    class FakeService:
        def schema_options(self, _cancelled):
            return {"object_classes": {}, "attributes": ["cn"]}

    window._loaded_model = (prefs.model_path, prefs.model_sha256, FakeGenerator())
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        main_window_module, "detect_backend", lambda *_args: DirectoryBackend.STANDARD_LDAP
    )
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: object())
    monkeypatch.setattr(
        window.data_lens_tab,
        "_parse_and_execute",
        lambda _text: pytest.fail("The main request field must not execute CRUD commands"),
    )
    window.ai_request.setPlainText(request)
    window._generate_filter()
    qtbot.waitUntil(lambda: not window._workers)

    assert generated[0][0] == request
    assert "Clarification needed" in window.ai_output.toPlainText()
    assert "Use the explicit entry form" in window.ai_explanation.toPlainText()
    assert "example" not in window.ai_explanation.toPlainText()
    assert "password is example" not in window.ai_prompt_log.toPlainText()
    assert "raw model output are not retained" in window.ai_prompt_log.toPlainText()
    window.close()


def test_mutation_intent_displays_dry_run_without_applying(
    qtbot, tmp_path, monkeypatch, make_directory
):
    from datalens_support import ADA, BIND_PASSWORD

    from datasifter.datalens import (
        DeleteProposal,
        DirectoryBackend,
        ProposedMutationIntent,
    )

    directory = make_directory(DirectoryBackend.STANDARD_LDAP)
    adapter = directory.adapter()
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = adapter._service.profile
    prefs = window.configuration.preferences
    prefs.model_path = str(tmp_path / "approved.gguf")
    prefs.model_sha256 = "a" * 64
    prefs.model_provenance = "Test catalog"

    class FakeGenerator:
        def generate_intent(self, *_args, **_kwargs):
            return ProposedMutationIntent(
                intent="proposed_mutation",
                mutation=DeleteProposal(operation="delete", target=ADA, kind="user"),
            )

    window._loaded_model = (prefs.model_path, prefs.model_sha256, FakeGenerator())
    monkeypatch.setattr(window, "_profile_password", lambda _profile: BIND_PASSWORD)
    monkeypatch.setattr(window, "_service", lambda *_args: adapter._service)
    monkeypatch.setattr(
        main_window_module, "detect_backend", lambda *_args: DirectoryBackend.STANDARD_LDAP
    )
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: adapter)
    monkeypatch.setattr(
        main_window_module.MutationReviewDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )
    window.ai_request.setPlainText("delete Ada")
    window._generate_filter()
    qtbot.waitUntil(lambda: not window._workers)

    assert "no change was applied" in window.ai_output.toPlainText()
    explanation = window.ai_explanation.toPlainText()
    assert "Proposed operation: delete." in explanation
    assert "Dry-run preview:" in explanation
    assert "requires explicit confirmation" in explanation
    assert directory.read(ADA) is not None
    audit = (tmp_path / "logs" / "datalens-audit.jsonl").read_text(encoding="utf-8")
    assert '"outcome":"declined"' in audit
    assert ADA not in audit
    window.close()


def test_mutation_intent_applies_only_after_confirmation(qtbot, tmp_path, monkeypatch, standard):
    from datalens_support import ADA, BIND_PASSWORD

    from datasifter.datalens import DeletePlan, DirectoryBackend, PreparedMutation

    adapter = standard.adapter()
    plan = DeletePlan(dn=ADA)
    prepared = PreparedMutation(plan=plan, preview=adapter.execute(plan).preview)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    profile = adapter._service.profile
    window._active_profile = profile
    window.configuration.preferences.require_ai_confirmation = False
    monkeypatch.setattr(
        main_window_module.MutationReviewDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(window, "_profile_password", lambda _profile: BIND_PASSWORD)
    monkeypatch.setattr(window, "_service", lambda *_args: adapter._service)
    monkeypatch.setattr(
        main_window_module, "detect_backend", lambda *_args: DirectoryBackend.STANDARD_LDAP
    )
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: adapter)

    window._display_intent_result(profile, prepared)
    qtbot.waitUntil(lambda: not window._workers)
    assert standard.read(ADA) is None
    assert "Change applied" in window.ai_output.toPlainText()
    window.close()


def test_search_review_preserves_resolved_search_scope(qtbot, tmp_path, monkeypatch):
    from datasifter.datalens import PreparedSearch, SearchScope, SearchSpec

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    spec = SearchSpec(
        base_dn="ou=People,dc=test",
        scope=SearchScope.LEVEL,
        filter="(cn=Ada)",
        attributes=("cn", "mail"),
        size_limit=20,
    )
    reviewed = []
    monkeypatch.setattr(
        CommandReviewDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        window, "_run_prepared_search", lambda _profile, value: reviewed.append(value)
    )

    window._display_intent_result(profile, PreparedSearch(spec=spec))

    assert reviewed[0].base_dn == "ou=People,dc=test"
    assert reviewed[0].scope is SearchScope.LEVEL
    assert reviewed[0].attributes == ("cn", "mail")
    assert reviewed[0].size_limit == 20
    window.close()


def test_prepared_search_executes_through_typed_adapter(
    qtbot, tmp_path, monkeypatch, make_directory
):
    from datalens_support import ADA, BIND_PASSWORD
    from PyQt6.QtCore import Qt

    from datasifter.datalens import DirectoryBackend, SearchScope, SearchSpec

    directory = make_directory(DirectoryBackend.STANDARD_LDAP)
    adapter = directory.adapter()
    profile = adapter._service.profile
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = profile
    monkeypatch.setattr(window, "_confirm_leave_entry", lambda: True)
    monkeypatch.setattr(window, "_profile_password", lambda _profile: BIND_PASSWORD)
    monkeypatch.setattr(window, "_service", lambda *_args: adapter._service)
    monkeypatch.setattr(
        main_window_module, "detect_backend", lambda *_args: DirectoryBackend.STANDARD_LDAP
    )
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: adapter)
    spec = SearchSpec(
        base_dn="ou=People,dc=test",
        scope=SearchScope.SUBTREE,
        filter="(cn=Ada)",
        attributes=("cn", "mail"),
        size_limit=5,
    )

    window._run_prepared_search(profile, spec)
    qtbot.waitUntil(lambda: not window._workers)

    root = window.tree.topLevelItem(0)
    assert root.text(0) == "ou=People,dc=test"
    assert root.childCount() == 1
    assert root.child(0).data(0, Qt.ItemDataRole.UserRole) == ADA
    window.close()


class _NoSecretsVault:
    def get_password(self, _profile):
        return None

    def get_client_key_passphrase(self, _profile):
        return None


def _session_window(qtbot, tmp_path, **configuration):
    first = DirectoryProfile(name="first", host="first.ldap.test", base_dn="dc=first")
    second = DirectoryProfile(name="second", host="second.ldap.test", read_only=True)
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[first, second], **configuration))
    window = MainWindow(store=store, vault=_NoSecretsVault())
    qtbot.addWidget(window)
    first, second = window.configuration.profiles
    return window, first, second


def _run_lens_request(window, request):
    window.ai_request.setPlainText(request)
    assert window.ai_generate.isEnabled()
    window.ai_generate.click()
    return window.ai_output.toPlainText()


def test_lens_search_needs_established_session_but_session_commands_do_not(qtbot, tmp_path):
    window, first, _second = _session_window(qtbot, tmp_path)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"

    window.ai_request.setPlainText("surname starts with b")
    assert not window.ai_generate.isEnabled()
    assert window.ai_generate.toolTip() == "Connect to a directory first."
    window._active_profile = first
    window._connecting = True
    window._refresh_lens_controls()
    assert not window.ai_generate.isEnabled()

    window.ai_request.setPlainText("list profiles in ou=People")
    assert window.ai_generate.text() == "Run request"
    window.ai_request.setPlainText("list targets")
    assert window.ai_panel.isEnabled()
    assert window.ai_generate.isEnabled()
    assert window.ai_generate.text() == "Run command"
    window.close()


def test_session_commands_connect_and_switch_targets(qtbot, tmp_path, monkeypatch):
    window, first, second = _session_window(qtbot, tmp_path)
    operations = []
    monkeypatch.setattr(
        window, "_run_worker", lambda operation, *_args: operations.append(operation)
    )

    output = _run_lens_request(window, "connect to second")

    assert output == "Connecting to second — second.ldap.test:389 (plain, anonymous)…"
    assert window._active_profile is second
    assert window._connecting
    assert window._selected_profile() is second
    assert "Session command: connect to second" in window.ai_prompt_log.toPlainText()

    window._connecting = False
    window._show_directory([{"dn": "cn=Stale,dc=second", "attributes": {}}])
    assert _run_lens_request(window, "connect to second").startswith("Already connected to")

    output = _run_lens_request(window, "Switch to profile FIRST.")

    assert output == (
        "Disconnected from second. "
        "Connecting to first — first.ldap.test:389 (plain, anonymous)…"
    )
    assert window._active_profile is first
    assert window.tree.topLevelItemCount() == 0
    assert len(operations) == 2
    window.close()


def test_session_commands_report_targets_and_disconnect(qtbot, tmp_path, monkeypatch):
    window, first, second = _session_window(qtbot, tmp_path)
    window.configuration.last_connected_profile_id = second.profile_id
    monkeypatch.setattr(
        window, "_connect_profile", lambda _profile: pytest.fail("Must not connect")
    )
    window._active_profile = first
    window.ai_explanation.setPlainText("stale interpretation")

    assert _run_lens_request(window, "list targets") == (
        "first — first.ldap.test:389 (plain, anonymous) [connected]\n"
        "second — second.ldap.test:389 (plain, anonymous) [last connected]"
    )
    assert "no AI model was used" in window.ai_explanation.toPlainText()
    assert "stale interpretation" not in window.ai_explanation.toPlainText()
    assert _run_lens_request(window, "current target") == (
        "Connected to first — first.ldap.test:389 (plain, anonymous); "
        "base DN dc=first; writable."
    )
    assert "No saved profile matches 'nowhere'" in _run_lens_request(
        window, "connect to nowhere"
    )
    assert "second is not the current target" in _run_lens_request(
        window, "disconnect from second"
    )
    assert window._active_profile is first

    assert _run_lens_request(window, "disconnect") == "Disconnected from first."
    assert window._active_profile is None
    assert _run_lens_request(window, "show current session") == (
        "No target is connected. Last connected: second."
    )
    window._disconnect()
    assert not window.ai_explanation.toPlainText()
    window.close()


def test_people_count_uses_selected_location_without_a_model(qtbot, tmp_path, monkeypatch):
    window, profile, _second = _session_window(qtbot, tmp_path)
    window._active_profile = profile
    window._selected_dn = "ou=People,dc=first"
    monkeypatch.setattr(
        QInputDialog, "getItem", lambda _parent, _title, _label, items, *_args: (items[-1], True)
    )
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    calls = []

    class Service:
        def count_entries(self, base, expression, _cancelled, _progress):
            calls.append((base, expression))
            return 7

    class Adapter:
        def user_filter(self):
            return "(objectClass=person)"

    monkeypatch.setattr(window, "_service", lambda *_args: Service())
    monkeypatch.setattr(main_window_module, "detect_backend", lambda *_args: "test")
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: Adapter())
    _run_lens_request(window, "How many people in the directory?")
    qtbot.waitUntil(lambda: not window._workers)
    assert calls == [("ou=People,dc=first", "(objectClass=person)")]
    assert "7 people visible" in window.ai_output.toPlainText()
    assert "Permissions may hide entries" in window.ai_output.toPlainText()
    assert "no AI model" in window.ai_explanation.toPlainText()
    assert "ou=People" not in window.ai_prompt_log.toPlainText()
    window.close()


def test_monitor_uses_one_time_credentials_and_does_not_log_values(qtbot, tmp_path, monkeypatch):
    window, profile, _second = _session_window(qtbot, tmp_path)
    profile.transport = "starttls"
    window._active_profile = profile
    monkeypatch.setattr(
        QInputDialog, "getItem", lambda _parent, _title, _label, items, *_args: (items[1], True)
    )
    monkeypatch.setattr(
        QInputDialog, "getText",
        lambda _parent, title, *_args: ("cn=monitor-admin", True)
        if title == "Monitoring account" else ("temporary-secret", True),
    )
    calls = []

    class Service:
        def search(self, base, _filter, **kwargs):
            calls.append((base, kwargs["max_entries"]))
            return [{
                "dn": "cn=monitor",
                "attributes": {"connections": ["42"], "userPassword": ["1234"]},
            }]

    def service(selected, password):
        assert selected is not profile
        assert selected.bind_dn == "cn=monitor-admin"
        assert password == "temporary-secret"
        return Service()

    monkeypatch.setattr(window, "_service", service)
    _run_lens_request(window, "Show directory stats")
    qtbot.waitUntil(lambda: not window._workers)
    assert calls == [("cn=monitor", 201)]
    assert "connections: 42" in window.ai_output.toPlainText()
    assert "1234" not in window.ai_output.toPlainText()
    assert "temporary-secret" not in window.ai_prompt_log.toPlainText()
    assert profile.authentication_mode == "anonymous"
    assert not window._session_passwords
    window.close()


def test_monitor_can_use_saved_same_endpoint_profile(qtbot, tmp_path, monkeypatch):
    window, profile, _second = _session_window(qtbot, tmp_path)
    monitor = DirectoryProfile(
        name="monitor", host=profile.host, port=profile.port, transport=profile.transport,
        authentication_mode="simple", bind_dn="cn=monitor-admin",
    )
    window.configuration.profiles.append(monitor)
    monkeypatch.setattr(
        QInputDialog, "getItem", lambda _parent, _title, _label, items, *_args: (items[-1], True)
    )
    monkeypatch.setattr(window, "_profile_password", lambda _profile: "saved-secret")
    assert window._monitor_credentials(profile) == (monitor, "saved-secret")
    window.close()


def test_directory_information_errors_reference_help(qtbot, tmp_path, monkeypatch):
    window, _profile, _second = _session_window(qtbot, tmp_path)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    window._directory_information_error("Monitoring denied.")
    assert "Monitoring denied" in errors[0]
    assert "Help → Data Lens help → Directory information" in errors[0]
    assert "Monitoring denied" in window.ai_output.toPlainText()
    window.close()


def test_refresh_tree_button_refreshes_from_highlighted_entry(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTreeWidgetItem

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=example,dc=com")
    window._active_profile = profile
    parent_dn = "ou=people,dc=example,dc=com"
    root = QTreeWidgetItem(["dc=example,dc=com"])
    root.setData(0, Qt.ItemDataRole.UserRole, "dc=example,dc=com")
    parent = QTreeWidgetItem(["ou=people,dc=example,dc=com"])
    parent.setData(0, Qt.ItemDataRole.UserRole, parent_dn)
    root.addChild(parent)
    window.tree.addTopLevelItem(root)
    window.tree.setCurrentItem(parent)
    window._selected_item = parent
    window._selected_dn = parent_dn
    searched_bases = []
    read_dns = []

    class FakeService:
        def search_page(self, base_dn, *_args, **_kwargs):
            searched_bases.append(base_dn)
            return ([{"dn": f"cn=Refreshed,{parent_dn}", "attributes": {}}], None)

        def read_entry(self, distinguished_name, **_kwargs):
            read_dns.append(distinguished_name)
            return {"attributes": {"cn": ["Refreshed"]}}

    service = FakeService()
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", lambda *_args: service)
    monkeypatch.setattr(window, "_confirm_leave_entry", lambda: True)
    monkeypatch.setattr(
        window,
        "_run_worker",
        lambda operation, success, *_args, **_kwargs: success(
            operation(Event(), lambda *_args: None)
        ),
    )
    window._refresh_connection_controls()

    assert window.refresh_tree_button.isEnabled()
    window.refresh_tree_button.click()

    assert searched_bases == [parent_dn]
    assert read_dns == [parent_dn]
    assert parent.childCount() == 1
    assert window._selected_dn == parent_dn
    window.close()


def test_tree_context_menu_refreshes_the_clicked_entry(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    clicked_item = QTreeWidgetItem(["ou=people,dc=example,dc=com"])
    clicked_item.setData(0, Qt.ItemDataRole.UserRole, "ou=people,dc=example,dc=com")
    refreshed = []
    monkeypatch.setattr(QTreeWidget, "itemAt", lambda _tree, _position: clicked_item)
    monkeypatch.setattr(QMenu, "exec", lambda menu, _position: menu.actions()[0])
    monkeypatch.setattr(window, "_refresh_tree_item", refreshed.append)

    window._tree_context_menu(QPoint(1, 1))

    assert refreshed == [clicked_item]
    window.close()


def test_mutation_request_requires_approved_model_in_structured_flow(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    request = "add phone number to mattp 732 555 1212"
    window.ai_request.setPlainText(request)
    messages = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: messages.append(_args[2]) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        window.data_lens_tab,
        "_parse_and_execute",
        lambda _text: pytest.fail("The main request field must not route to legacy CRUD"),
    )
    monkeypatch.setattr(
        window,
        "_run_worker",
        lambda *_args, **_kwargs: pytest.fail("An unapproved model must not run"),
    )

    assert window.ai_generate.isEnabled()
    assert window.ai_generate.text() == "Run request"
    window._generate_filter()
    assert "No approved local GGUF model" in messages[0]
    window.close()


def test_settings_save_hide_reenable_and_persist_lens(qtbot, tmp_path):
    from datasifter.ui.settings_dialog import SettingsDialog

    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    window._loaded_model = ("approved.gguf", "a" * 64, object())
    dialog = SettingsDialog(window.configuration.preferences, window)
    qtbot.addWidget(dialog)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Data Lens", "Safety", "Appearance"
    ]
    dialog.ai_enabled.setChecked(False)
    dialog.delete_confirmation.setChecked(False)
    dialog.rename_confirmation.setChecked(False)
    dialog.highlight_search.setChecked(False)
    dialog.ai_confirmation.setChecked(False)
    window._save_settings(dialog)
    assert window.ai_panel.isHidden()
    assert window._loaded_model is None
    assert not store.load().preferences.ai_enabled
    assert not window.directory_confirmation_actions["delete"].isChecked()
    assert not window.directory_confirmation_actions["rename"].isChecked()
    assert not window.highlight_search_action.isChecked()
    assert not window._requires_ai_confirmation()
    assert "Data Lens disabled" in window.ai_prompt_log.toPlainText()
    reopened = MainWindow(store=store)
    qtbot.addWidget(reopened)
    assert reopened.ai_panel.isHidden()
    enable = SettingsDialog(reopened.configuration.preferences, reopened)
    qtbot.addWidget(enable)
    enable.ai_enabled.setChecked(True)
    reopened._save_settings(enable)
    assert not reopened.ai_panel.isHidden()
    assert store.load().preferences.ai_enabled
    assert not reopened.ai_generate.isEnabled()
    window.close()
    reopened.close()


def test_settings_cancel_and_failed_save_preserve_preferences(qtbot, tmp_path, monkeypatch):
    from dataclasses import replace

    from datasifter.config import ConfigurationError
    from datasifter.ui.settings_dialog import SettingsDialog

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    original = replace(window.configuration.preferences)
    dialog = SettingsDialog(original, window)
    qtbot.addWidget(dialog)
    dialog.ai_enabled.setChecked(False)
    dialog.reject()
    assert window.configuration.preferences == original
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[2]))

    def fail_save(_configuration):
        raise ConfigurationError("Disk unavailable")

    monkeypatch.setattr(window.store, "save", fail_save)
    window._save_settings(dialog)
    assert errors == ["Disk unavailable"]
    assert window.configuration.preferences == original
    assert not window.ai_panel.isHidden()
    window.close()


def test_operational_attributes_are_italic_without_losing_search_highlight(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._search_attributes = {"entryuuid"}
    window._display_attributes({"cn": ["Test"], "entryUUID": ["123"], "ds-entry-checksum": ["x"]})
    for row in range(window.attributes.rowCount()):
        name = window.attributes.item(row, 0).text()
        operational = name != "cn"
        for column in range(2):
            item = window.attributes.item(row, column)
            assert item.font().italic() == operational
        if operational:
            assert "Operational attribute" in window.attributes.item(row, 0).toolTip()
    window._set_search_highlight_preference(False)
    for row in range(window.attributes.rowCount()):
        name = window.attributes.item(row, 0).text()
        assert window.attributes.item(row, 0).font().italic() == (name != "cn")
    window.close()


def test_filter_input_uses_syntax_highlighting(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)

    assert isinstance(window.filter_edit, QPlainTextEdit)
    assert isinstance(window.filter_highlighter, LDAPFilterHighlighter)
    assert window.filter_edit.toPlainText() == "(objectClass=*)"
    assert len(window.filter_highlighter.rules) == 3
    window.close()


def test_clear_filter_resets_and_searches_all_entries(qtbot, tmp_path, monkeypatch):
    from threading import Event

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert not window.filter_clear.isEnabled()
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    window._active_profile = profile
    window._refresh_connection_controls()
    window.filter_edit.setPlainText("(cn=Alice)")
    calls = []

    class FakeService:
        def search(self, base, expression, **_kwargs):
            calls.append((base, expression))
            return [{"dn": "cn=Bob,dc=test", "attributes": {"cn": ["Bob"]}}]

    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, success: success(operation(Event(), lambda *_args: None)),
    )
    window.filter_clear.click()

    assert window.filter_edit.toPlainText() == "(objectClass=*)"
    assert calls == [("dc=test", "(objectClass=*)")]
    assert window.tree.topLevelItem(0).child(0).text(0) == "cn=Bob,dc=test"
    assert not window._search_attributes
    window.close()


def test_clear_filter_cancel_preserves_filter_and_view(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    window._refresh_connection_controls()
    window._show_directory([{"dn": "cn=Alice,dc=test", "attributes": {}}])
    window.filter_edit.setPlainText("(cn=Alice)")
    monkeypatch.setattr(window, "_confirm_leave_entry", lambda: False)
    monkeypatch.setattr(window, "_run_worker", lambda *_args: pytest.fail("Must not search"))

    window.filter_clear.click()

    assert window.filter_edit.toPlainText() == "(cn=Alice)"
    assert window.tree.topLevelItem(0).child(0).text(0) == "cn=Alice,dc=test"
    monkeypatch.setattr(window, "_confirm_leave_entry", lambda: True)
    window.close()


def test_list_profiles_button_is_disabled_until_profiles_exist(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(
        name="Production LDAP",
        host="directory.example",
        authentication_mode="simple",
        bind_dn="cn=manager",
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    assert not window.list_profiles_button.isEnabled()
    assert not window.delete_profile_button.isEnabled()

    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )
    window.configuration.profiles.append(profile)
    window._refresh_profiles()
    assert window.list_profiles_button.isEnabled()
    assert window.delete_profile_button.isEnabled()
    window.list_profiles_button.click()

    assert messages == [
        (
            "Saved profiles",
            "Production LDAP — directory.example:389 (plain, simple)",
        )
    ]
    window.configuration.profiles.clear()
    window._refresh_profiles()
    assert not window.list_profiles_button.isEnabled()
    assert not window.delete_profile_button.isEnabled()
    window.close()


def test_cancel_requests_cancellation_for_all_active_workers(qtbot, tmp_path):
    class FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    workers = [FakeWorker(), FakeWorker()]
    window._workers.update(workers)

    window._cancel_operation()

    assert all(worker.cancelled for worker in workers)
    assert "2 active operation" in window.statusBar().currentMessage()
    for worker in workers:
        worker.cancelled = False
    window.close()
    assert all(worker.cancelled for worker in workers)


def test_attributes_show_all_values_and_sort_without_mixing_rows(qtbot, tmp_path):
    from PyQt6.QtCore import Qt

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.show()
    values = {"sn": ["Zulu"], "objectClass": ["top", "person", "inetOrgPerson"], "cn": ["Alpha"]}
    window._display_attributes(values)
    assert window.attributes.isSortingEnabled()
    assert window.attributes.item(0, 0).text() == "cn"
    row = next(
        row for row in range(window.attributes.rowCount())
        if window.attributes.item(row, 0).text() == "objectClass"
    )
    assert window.attributes.item(row, 1).text().splitlines() == values["objectClass"]
    assert window.attributes.rowHeight(row) >= window.attributes.fontMetrics().lineSpacing() * 3
    window.attributes.sortItems(1, Qt.SortOrder.DescendingOrder)
    assert {
        window.attributes.item(row, 0).text(): window.attributes.item(row, 1).text().splitlines()
        for row in range(window.attributes.rowCount())
    } == values
    delegate = window.attributes.itemDelegateForColumn(1)
    assert isinstance(delegate, AttributeValueDelegate)
    from PyQt6.QtWidgets import QStyleOptionViewItem

    index = window.attributes.model().index(0, 1)
    edited_item = window.attributes.item(0, 1)
    editor = delegate.createEditor(window.attributes, QStyleOptionViewItem(), index)
    delegate.setEditorData(editor, index)
    editor.setPlainText("first\nsecond\nthird")
    delegate.setModelData(editor, window.attributes.model(), index)
    assert edited_item.text().splitlines() == ["first", "second", "third"]
    window.close()


def test_add_attribute_offers_case_insensitive_progressive_search(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtCore import Qt

    class FakeVault:
        def get_password(self, _profile):
            return None

    class FakeService:
        def schema_options(self, cancelled):
            return {"attributes": ["cn", "description", "displayName"]}

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"), vault=FakeVault())
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes({"cn": ["test"]})
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, success: success(operation(Event(), lambda *_args: None)),
    )

    def select_attribute(dialog):
        combo = dialog.findChild(QComboBox)
        assert combo.isEditable()
        assert combo.completer().filterMode() == Qt.MatchFlag.MatchContains
        assert combo.completer().caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
        combo.completer().setCompletionPrefix("PLAY")
        assert combo.completer().completionCount() == 1
        combo.setEditText("DISPLAYNAME")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", select_attribute)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("Example", True))
    window._add_attribute()
    assert {
        window.attributes.item(row, 0).text(): window.attributes.item(row, 1).text()
        for row in range(window.attributes.rowCount())
    } == {"cn": "test", "displayName": "Example"}
    window._clear_entry_view()
    assert window.attributes.isSortingEnabled()
    window.close()


def test_attribute_edits_do_not_delete_read_only_attributes(qtbot, tmp_path, monkeypatch):
    from threading import Event

    profile = DirectoryProfile(name="test", host="ldap.test")

    class FakeVault:
        def get_password(self, _profile):
            return None

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"), vault=FakeVault())
    qtbot.addWidget(window)
    window._active_profile = profile
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes({"cn": ["test"], "entryUUID": ["immutable"]})
    row = next(
        row for row in range(window.attributes.rowCount())
        if window.attributes.item(row, 0).text() == "cn"
    )
    window.attributes.item(row, 1).setText("updated")
    captured = []
    confirmations = []

    class FakeService:
        def modify_entry(self, dn, changes, cancelled):
            captured.append((dn, changes))

    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, message, *_args: (
            confirmations.append(message) or QMessageBox.StandardButton.Yes
        ),
    )
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, *_args: operation(Event(), lambda *_args: None),
    )
    window._save_entry_changes()
    assert captured == [(
        "cn=test,dc=test",
        {"cn": [("replace", ["updated"])]},
    )]
    assert confirmations and "cn=test,dc=test" in confirmations[0]
    window._clear_entry_view()
    window.close()


def test_save_button_tracks_edits_and_reverted_values(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes({"cn": ["test"]})
    assert not window.save_entry_button.isEnabled()
    window.attributes.item(0, 1).setText("changed")
    assert window.save_entry_button.isEnabled()
    assert window.save_entry_button.font().bold()
    window.attributes.item(0, 1).setText("test")
    assert not window.save_entry_button.isEnabled()
    assert not window.save_entry_button.font().bold()
    window._active_profile.read_only = True
    window.attributes.item(0, 1).setText("changed")
    assert not window.save_entry_button.isEnabled()
    window._clear_entry_view()
    window.close()


def test_selected_dn_is_shown_read_only_and_copyable(qtbot, tmp_path):
    from PyQt6.QtGui import QGuiApplication

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert not window.copy_dn_button.isEnabled()
    window._selected_dn = "uid=user.8,ou=People,dc=datasifter,dc=lab"
    assert window.dn_field.text() == "uid=user.8,ou=People,dc=datasifter,dc=lab"
    assert window.dn_field.isReadOnly()
    window.copy_dn_button.click()
    assert QGuiApplication.clipboard().text() == "uid=user.8,ou=People,dc=datasifter,dc=lab"
    window._selected_dn = None
    assert window.dn_field.text() == ""
    assert not window.copy_dn_button.isEnabled()
    window.close()


def test_enter_commits_attribute_edit_and_shift_enter_adds_line(qtbot, tmp_path):
    from PyQt6.QtCore import Qt

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.show()
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes({"description": ["old"]})
    window.attributes.setCurrentCell(0, 1)
    window.attributes.editItem(window.attributes.item(0, 1))
    editor = window.attributes.findChild(QPlainTextEdit)
    assert editor is not None
    editor.selectAll()
    qtbot.keyClicks(editor, "first")
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(editor, "second")
    assert window.attributes.item(0, 1).text() == "old"
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert window.attributes.item(0, 1).text() == "first\nsecond"
    assert window.save_entry_button.isEnabled()
    window._clear_entry_view()
    window.close()


def _window_with_unsaved_edit(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes({"cn": ["test"]})
    window.attributes.item(0, 1).setText("changed")
    return window


def test_leaving_entry_with_unsaved_changes_asks_first(qtbot, tmp_path, monkeypatch):
    window = _window_with_unsaved_edit(qtbot, tmp_path)
    answers = [QMessageBox.StandardButton.Cancel]
    prompts = []

    def answer(box):
        prompts.append(box.text())
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", answer)
    assert not window._confirm_leave_entry()
    assert window._selected_dn == "cn=test,dc=test"
    assert window.attributes.item(0, 1).text() == "changed"
    assert "cn" in prompts[0]

    answers.append(QMessageBox.StandardButton.Discard)
    assert window._confirm_leave_entry()
    assert window._selected_dn is None
    assert not window._has_unsaved_changes()
    window.close()


def test_close_is_cancelled_when_unsaved_changes_are_kept(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtGui import QCloseEvent

    window = _window_with_unsaved_edit(qtbot, tmp_path)
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    window._clear_entry_view()
    window.close()


def test_empty_search_shows_dialog_and_keeps_tree(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtWidgets import QTreeWidgetItem

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    window._active_profile = profile
    window.tree.addTopLevelItem(QTreeWidgetItem(["dc=test"]))
    messages = []

    class FakeService:
        def search(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(window, "_profile_password", lambda *_args: None)
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, success, *_args: success(operation(Event(), lambda *_args: None)),
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda _parent, title, text: messages.append((title, text))
    )
    window._run_directory_search(profile, "(telephoneNumber=*732*)")
    assert messages and messages[0][0] == "No entries found"
    assert "(telephoneNumber=*732*)" in messages[0][1]
    assert window.tree.topLevelItemCount() == 1
    assert window.tree.topLevelItem(0).text(0) == "dc=test"
    window.close()


def test_directory_confirmation_override_persists_separately(qtbot, tmp_path, monkeypatch):
    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    shown = []

    def confirm(message):
        shown.append(message.text())
        message.checkBox().setChecked(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    assert window._confirm_directory_change("rename", "Rename test?")
    assert shown == ["Rename test?"]
    assert not store.load().preferences.require_rename_confirmation
    assert store.load().preferences.require_delete_confirmation
    assert not window.directory_confirmation_actions["rename"].isChecked()
    assert window._confirm_directory_change("rename", "Rename again?")
    assert shown == ["Rename test?"]
    window.close()


def test_cancelled_confirmation_does_not_save_override(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)

    def cancel(message):
        message.checkBox().setChecked(True)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    assert not window._confirm_directory_change("rename", "Rename test?")
    assert window.configuration.preferences.require_rename_confirmation
    window.close()


def test_model_manager_is_offline_until_download_requested(qtbot, tmp_path):
    from PyQt6.QtCore import Qt

    from datasifter.ai.model_catalog import MODEL_CATALOG
    from datasifter.ui.model_dialog import ModelDialog

    dialog = ModelDialog(tmp_path)
    qtbot.addWidget(dialog)
    assert dialog.catalog.count() == len(MODEL_CATALOG) == 8
    assert dialog.worker is None
    assert "Apache-2.0" in dialog.details.text()
    assert dialog.details.textFormat() == Qt.TextFormat.RichText
    assert dialog.details.openExternalLinks()
    assert 'href="https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct"' in dialog.details.text()
    assert MODEL_CATALOG[0].description in dialog.details.text()
    publishers = {model.publisher for model in MODEL_CATALOG}
    assert {"Google (US)", "Meta (US)", "Microsoft (US)"} <= publishers
    for model in MODEL_CATALOG:
        assert model.description and model.upstream and model.quantized_by
        assert len(model.sha256) == 64 and len(model.revision) == 40
    dialog.catalog.setCurrentIndex(1)
    assert "google/gemma-3-1b-it" in dialog.details.text()
    assert "ggml-org" in dialog.details.text()
    assert dialog.download.isEnabled()
    assert not dialog.cancel_download.isEnabled()


def test_model_manager_downloads_then_emits_selected_file(qtbot, tmp_path, monkeypatch):
    from datasifter.ui.model_dialog import ModelDialog

    dialog = ModelDialog(tmp_path)
    qtbot.addWidget(dialog)
    selected = []
    dialog.model_selected.connect(lambda path, load: selected.append((path, load)))
    assert dialog.load_after.isChecked()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *_args: str(tmp_path)
    )

    def download(model, directory, cancelled, progress):
        assert not cancelled.is_set()
        progress(100, "Verified test download")
        return str(directory / model.filename)

    monkeypatch.setattr("datasifter.ui.model_dialog.download_model", download)
    dialog.download.click()
    qtbot.waitUntil(lambda: dialog.worker is None)
    assert selected == [(str(tmp_path / "smollm2-1.7b-instruct-q4_k_m.gguf"), True)]
    assert "approval is required" in dialog.status.text()


def test_model_manager_can_skip_loading_local_selection(qtbot, tmp_path, monkeypatch):
    from datasifter.ui.model_dialog import ModelDialog

    dialog = ModelDialog(tmp_path)
    qtbot.addWidget(dialog)
    selected = []
    dialog.model_selected.connect(lambda path, load: selected.append((path, load)))
    dialog.load_after.setChecked(False)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_args: (str(tmp_path / "local.gguf"), "")
    )
    dialog.local.click()
    assert selected == [(str(tmp_path / "local.gguf"), False)]


def _approve_model(window, qtbot, monkeypatch, path, load_after):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("Org catalog", True))
    window._approve_model_file(str(path), load_after)
    qtbot.waitUntil(lambda: not window._workers)


def test_approved_model_loads_after_download_and_is_reused(qtbot, tmp_path, monkeypatch):
    from datasifter.ai.local_model import model_sha256
    from datasifter.datalens import (
        DirectoryBackend,
        PreparedSearch,
        SearchCondition,
        SearchIntent,
        SearchSpec,
    )

    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFapproved")
    created = []
    received = []

    class FakeGenerator:
        def __init__(self, path, sha256):
            assert sha256 == model_sha256(path)
            created.append(path)

        def generate_intent(self, request, classes, attributes, cancelled=None):
            received.append((classes, attributes))
            return SearchIntent(
                intent="search",
                conditions=(SearchCondition(term="name", value="a"),),
            )

    monkeypatch.setattr("datasifter.ui.main_window.LlamaCppFilterGenerator", FakeGenerator)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    _approve_model(window, qtbot, monkeypatch, model_path, True)

    assert created == [str(model_path)]
    assert window._loaded_model[:2] == (str(model_path), model_sha256(model_path))
    assert "Loaded in memory" in window.model_status.text()

    window._active_profile = DirectoryProfile(name="lab", host="ldap.test")
    reviewed = []
    schema_requests = []

    class FakeService:
        def schema_options(self, _cancelled):
            schema_requests.append(True)
            return {"object_classes": {"inetOrgPerson": {}}, "attributes": ["telephoneNumber"]}

    monkeypatch.setattr(
        window, "_display_intent_result", lambda _profile, prepared: reviewed.append(prepared)
    )
    monkeypatch.setattr(window, "_profile_password", lambda _profile: "secret")
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        main_window_module, "detect_backend", lambda *_args: DirectoryBackend.STANDARD_LDAP
    )
    monkeypatch.setattr(main_window_module, "create_adapter", lambda *_args: object())
    monkeypatch.setattr(
        main_window_module,
        "prepare_intent",
        lambda _intent, _resolver, **_kwargs: PreparedSearch(
            spec=SearchSpec(filter="(cn=a)")
        ),
    )
    window.ai_request.setPlainText("people named a")
    for _attempt in range(2):
        window._generate_filter()
        qtbot.waitUntil(lambda: not window._workers)
    assert len(reviewed) == 2
    assert all(isinstance(item, PreparedSearch) for item in reviewed)
    explanation = window.ai_explanation.toPlainText()
    assert "not the model's internal reasoning" in explanation
    assert "Condition: name equals 'a'." in explanation
    assert "Resolved LDAP filter: (cn=a)" in explanation
    assert window.ai_explanation.isHidden()
    window.show_ai_explanation.setChecked(True)
    assert not window.ai_explanation.isHidden()
    assert window.ai_explanation.isReadOnly()
    window.ai_explanation.selectAll()
    assert "Resolved LDAP filter: (cn=a)" in window.ai_explanation.textCursor().selectedText()
    window.show_ai_explanation.setChecked(False)
    assert window.ai_explanation.isHidden()
    assert created == [str(model_path)]
    assert schema_requests == [True]
    assert received[-1] == (["inetOrgPerson"], ["telephoneNumber"])
    log = window.ai_prompt_log.toPlainText()
    assert log.count("Structured intent: search") == 2
    assert "Request: people named a" not in log
    assert "PROMPT for people named a" not in log
    assert "model.gguf" in log
    assert "raw model output are not retained" in log
    assert window.tabs.tabText(window.tabs.count() - 1) == "Data Lens log"
    window.clear_prompts_button.click()
    assert window.ai_prompt_log.toPlainText() == ""
    window.close()


def test_failed_intent_generation_is_reported_without_logging_request(
    qtbot, tmp_path, monkeypatch
):
    class FakeGenerator:
        def __init__(self, *_args):
            pass

        def generate_intent(self, *_args, **_kwargs):
            raise RuntimeError("invalid structured intent")

    class FakeService:
        def schema_options(self, _cancelled):
            return {"object_classes": {}, "attributes": ["st"]}

    monkeypatch.setattr("datasifter.ui.main_window.LlamaCppFilterGenerator", FakeGenerator)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes
    )
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.configuration.preferences.model_path = str(tmp_path / "approved.gguf")
    window.configuration.preferences.model_sha256 = "a" * 64
    window.configuration.preferences.model_provenance = "Test catalog"
    window._active_profile = DirectoryProfile(name="lab", host="ldap.test")
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    monkeypatch.setattr(window, "_profile_password", lambda _profile: "secret")
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    window.ai_request.setPlainText("people in pa")
    window._generate_filter()
    qtbot.waitUntil(lambda: not window._workers)
    log = window.ai_prompt_log.toPlainText()
    assert "Request: people in pa" not in log
    assert "raw model output are not retained" in log
    assert errors == ["invalid structured intent"]
    assert window._loaded_model is not None
    assert "failed before a validated interpretation" in window.ai_explanation.toPlainText()
    window.close()


def test_invalid_intent_offers_derived_search_filter(qtbot, tmp_path, monkeypatch):
    class FakeGenerator:
        def generate_intent(self, *_args, **_kwargs):
            raise ModelOutputError("invalid structured intent")

    class FakeService:
        def schema_options(self, _cancelled):
            return {"object_classes": {}, "attributes": ["sn"]}

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._loaded_model = (preferences.model_path, preferences.model_sha256, FakeGenerator())
    window._active_profile = DirectoryProfile(name="lab", host="ldap.test")
    monkeypatch.setattr(window, "_profile_password", lambda _profile: "secret")
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    suggestions = []
    monkeypatch.setattr(window, "_offer_suggested_query", suggestions.append)
    window.ai_request.setPlainText("Find all people who have a last name starting with s")

    window._generate_filter()
    qtbot.waitUntil(lambda: not window._workers)

    assert suggestions == ["(sn=s*)"]
    assert "separate simple-search rule" in window.ai_explanation.toPlainText()
    assert "Find all people" not in window.ai_prompt_log.toPlainText()
    window.close()


def test_suggested_query_dialog_copies_filter_and_describes_manual_use(qtbot):
    from PyQt6.QtGui import QGuiApplication

    dialog = SuggestedQueryDialog("(sn=s*)")
    qtbot.addWidget(dialog)
    dialog.copy_button.click()

    assert QGuiApplication.clipboard().text() == "(sn=s*)"
    assert "paste it into the directory workbench" in dialog.instructions.text().casefold()
    dialog.close()


def test_trying_suggested_query_uses_existing_review_flow(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    reviewed = []

    class AcceptedDialog:
        def __init__(self, _filter_expression, _parent):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "SuggestedQueryDialog", AcceptedDialog)
    monkeypatch.setattr(window, "_review_generated_filter", reviewed.append)
    window._offer_suggested_query("(sn=s*)")

    assert reviewed == ["(sn=s*)"]
    assert "(sn=s*)" in window.ai_output.toPlainText()
    window.close()


def test_approved_model_without_load_option_is_not_loaded(qtbot, tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFapproved")

    def fail_load(*_args):
        raise AssertionError("model must not load")

    monkeypatch.setattr("datasifter.ui.main_window.LlamaCppFilterGenerator", fail_load)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    _approve_model(window, qtbot, monkeypatch, model_path, False)

    assert window._loaded_model is None
    assert window.configuration.preferences.model_path == str(model_path)
    assert "prompts on first use" in window.model_status.text()
    window.close()


def test_model_changes_are_logged_only_after_successful_approval(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    first = tmp_path / "first.gguf"
    second = tmp_path / "second.gguf"
    first.write_bytes(b"GGUFfirst")
    second.write_bytes(b"GGUFsecond")
    _approve_model(window, qtbot, monkeypatch, first, False)
    _approve_model(window, qtbot, monkeypatch, second, False)
    log = window.ai_prompt_log.toPlainText()
    assert log.count("Model changed:") == 2
    assert f"Model changed: {first} -> {second}" in log
    assert f"SHA-256: {window.configuration.preferences.model_sha256}" in log
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    window._approve_model_file(str(first))
    qtbot.waitUntil(lambda: not window._workers)
    assert window.ai_prompt_log.toPlainText() == log
    assert window.configuration.preferences.model_path == str(second)
    window.close()


@pytest.mark.parametrize(
    ("filename", "content"),
    [("model.bin", b"GGUFplaceholder"), ("renamed.gguf", b"invalid header")],
)
def test_model_approval_rejects_non_gguf_files(qtbot, tmp_path, monkeypatch, filename, content):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    path = tmp_path / filename
    path.write_bytes(content)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: pytest.fail("Invalid file must not be approved")
    )
    window._approve_model_file(str(path))
    qtbot.waitUntil(lambda: not window._workers)
    assert errors and "GGUF" in errors[0].upper()
    assert not window.configuration.preferences.model_path
    assert not window.ai_generate.isEnabled()
    assert "Model changed:" not in window.ai_prompt_log.toPlainText()
    window.close()


def test_settings_and_generation_are_disabled_during_operations(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialogButtonBox

    from datasifter.directory.worker import DirectoryWorker
    from datasifter.ui.settings_dialog import SettingsDialog

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    preferences.model_sha256 = "a" * 64
    preferences.model_provenance = "Test catalog"
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window.ai_request.setPlainText("sn starts with b")
    dialog = SettingsDialog(preferences, window)
    qtbot.addWidget(dialog)
    window._settings_dialog = dialog
    worker = DirectoryWorker(lambda *_args: None)
    window._workers.add(worker)
    window._refresh_model_status()
    assert not window.ai_generate.isEnabled()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    assert not dialog.choose_model.isEnabled()
    dialog.ai_enabled.setChecked(False)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window._save_settings(dialog)
    assert messages
    assert preferences.ai_enabled
    window._worker_finished(worker)
    assert window.ai_generate.isEnabled()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    window._settings_dialog = None
    window.close()


def test_schema_class_filter_includes_inherited_attributes(qtbot, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Event
    from types import SimpleNamespace

    from ldap3 import OFFLINE_SLAPD_2_4, Server

    from datasifter.directory.service import LDAPService

    profile = DirectoryProfile(name="schema", host="ldap.test")
    service = LDAPService(profile, None)

    @contextmanager
    def offline_connection(_cancelled):
        yield SimpleNamespace(server=Server("offline", get_info=OFFLINE_SLAPD_2_4))

    monkeypatch.setattr(service, "_connection", offline_connection)
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = profile
    monkeypatch.setattr(window, "_profile_password", lambda _profile: None)
    monkeypatch.setattr(window, "_service", lambda *_args: service)
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, success: success(operation(Event(), lambda *_args: None)),
    )
    window._load_schema()
    window.schema_object_class.setCurrentIndex(
        window.schema_object_class.findData("inetOrgPerson")
    )
    visible = {
        window.schema_table.item(row, 1).text()
        for row in range(window.schema_table.rowCount())
        if not window.schema_table.isRowHidden(row)
    }
    assert {"inetOrgPerson", "sn", "cn", "objectClass", "title", "mail"} <= visible
    assert "olcDatabase" not in visible
    window.schema_attribute_search.setText("SN")
    assert [
        window.schema_table.item(row, 1).text()
        for row in range(window.schema_table.rowCount())
        if not window.schema_table.isRowHidden(row)
    ] == ["sn"]
    window.close()


def test_delete_confirmation_override_keeps_recursive_choice_explicit(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtWidgets import QCheckBox

    class FakeVault:
        def get_password(self, _profile):
            return None

    window = MainWindow(
        store=ConfigurationStore(tmp_path / "profiles.json"), vault=FakeVault()
    )
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    captured = []

    class FakeService:
        def delete_entry(self, dn, recursive, cancelled):
            captured.append((dn, recursive))

    def confirm(dialog):
        assert dialog.windowTitle() == "Delete directory entry"
        checkboxes = dialog.findChildren(QCheckBox)
        assert len(checkboxes) == 2
        for checkbox in checkboxes:
            checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", confirm)
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, *_args: operation(Event(), lambda *_args: None),
    )
    window._delete_entry()
    assert captured == [("cn=test,dc=test", True)]
    assert not window.store.load().preferences.require_delete_confirmation
    assert window.store.load().preferences.require_rename_confirmation
    window._delete_entry()
    assert captured[-1] == ("cn=test,dc=test", False)
    window.close()


def test_rename_does_not_submit_after_confirmation_is_cancelled(qtbot, tmp_path, monkeypatch):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    messages = []

    def fill_form(dialog):
        dialog.findChildren(QLineEdit)[0].setText("cn=renamed")
        return QDialog.DialogCode.Accepted

    def cancel(message):
        messages.append(message.text())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QDialog, "exec", fill_form)
    monkeypatch.setattr(QMessageBox, "exec", cancel)
    window._move_entry()
    assert messages == ["Rename/move cn=test,dc=test to cn=renamed,dc=test?"]
    assert not window._workers
    window.close()


def test_add_entry_worker_captures_values_before_dialog_disposal(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtCore import QCoreApplication, QEvent

    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    entry_dn = "ou=temporary,dc=test"
    entry_attributes = (
        "objectClass=top\n"
        "objectClass=organizationalUnit\n"
        "ou=temporary"
    )
    schema = {
        "object_classes": {
            "top": {"kind": "abstract", "required": ["objectClass"]},
            "organizationalUnit": {"kind": "structural", "required": ["ou"]},
        },
        "attributes": ["objectClass", "ou"],
    }
    dialogs = []
    operations = []

    def accept_entry(dialog):
        line_edits = dialog.findChildren(QLineEdit)
        line_edits[0].setText(entry_dn)
        line_edits[1].setText("top, organizationalUnit")
        text_edits = dialog.findChildren(QPlainTextEdit)
        text_edits[-1].setPlainText(entry_attributes)
        dialogs.append(dialog)
        return QDialog.DialogCode.Accepted

    class FakeService:
        def __init__(self):
            self.added = None

        def add_entry(self, dn, values, _cancelled):
            self.added = (dn, values)

    service = FakeService()
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    monkeypatch.setattr(QDialog, "exec", accept_entry)
    monkeypatch.setattr(
        window,
        "_run_worker",
        lambda operation, *_args, **_kwargs: operations.append(operation),
    )
    monkeypatch.setattr(window, "_service", lambda *_args: service)

    window._show_add_entry_dialog(profile, None, schema)

    assert len(operations) == 1
    dialogs[0].deleteLater()
    QCoreApplication.sendPostedEvents(dialogs[0], QEvent.Type.DeferredDelete)
    operations[0](Event(), lambda *_args: None)

    assert service.added == (
        entry_dn,
        {"objectClass": ["top", "organizationalUnit"], "ou": ["temporary"]},
    )
    window.close()


def test_add_entry_dialog_bounds_large_directory_schema(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    schema = {
        "object_classes": {
            f"class-{index:04d}": {
                "kind": "structural",
                "required": ["commonName", "surname"],
            }
            for index in range(1240)
        },
        "attributes": [f"attributeType{index:04d}" for index in range(3224)],
    }
    sizes = []

    def reject_after_measuring(dialog):
        size = dialog.sizeHint()
        sizes.append((size.width(), size.height()))
        return QDialog.DialogCode.Rejected

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    monkeypatch.setattr(QDialog, "exec", reject_after_measuring)

    window._show_add_entry_dialog(profile, None, schema)

    assert len(sizes) == 1
    assert sizes[0][0] < 1600
    assert sizes[0][1] < 1000
    window.close()


def test_schema_inspector_loads_directory_schema_asynchronously(
    qtbot, tmp_path, monkeypatch
):
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")

    class FakeVault:
        def get_password(self, _profile):
            return None

    class FakeService:
        def schema_options(self, cancelled=None):
            return {
                "object_classes": {
                    "person": {
                        "kind": "structural",
                        "required": ["sn", "cn"],
                        "optional": ["uid"],
                    }
                },
                "attributes": ["cn", "sn", "uid"],
                "attribute_details": [
                    {
                        "name": name,
                        "oid": "",
                        "syntax": "1.3.6.1.4.1",
                        "description": "",
                        "single_value": False,
                        "equality": "",
                        "ordering": "",
                        "substring": "",
                    }
                    for name in ["cn", "sn", "uid"]
                ],
            }

    window = MainWindow(
        store=ConfigurationStore(tmp_path / "profiles.json"),
        vault=FakeVault(),
    )
    qtbot.addWidget(window)
    window._active_profile = profile
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())

    def run_now(operation, success, _failure=None):
        from threading import Event

        success(operation(Event(), lambda *_args: None))

    monkeypatch.setattr(window, "_run_worker", run_now)
    window._load_schema()

    assert window.schema_table.rowCount() == 4
    assert window.schema_table.item(0, 0).text() == "Object class"
    assert "MUST: sn, cn" in window.schema_table.item(0, 2).text()
    assert "Syntax: 1.3.6.1.4.1" in window.schema_table.item(1, 2).text()
    assert window.schema_status.text() == (
        "Loaded 1 object classes and 3 attribute types."
    )
    window.schema_object_class.setCurrentIndex(window.schema_object_class.findData("person"))
    window.schema_attribute_search.setText("s")
    visible = [
        window.schema_table.item(row, 1).text()
        for row in range(window.schema_table.rowCount())
        if not window.schema_table.isRowHidden(row)
    ]
    assert visible == ["sn"]
    window.schema_attribute_search.setText("uid")
    assert [
        window.schema_table.item(row, 1).text()
        for row in range(window.schema_table.rowCount())
        if not window.schema_table.isRowHidden(row)
    ] == ["uid"]
    window.schema_attribute_search.clear()
    assert not window.schema_table.isRowHidden(0)
    window.close()


def test_unattested_model_is_not_reported_as_verified(qtbot, tmp_path):
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(
        LocalConfiguration(
            preferences=Preferences(model_path="C:/models/old.gguf"),
        )
    )
    window = MainWindow(store=store)
    qtbot.addWidget(window)

    assert "approval is incomplete" in window.model_status.text()
    window.close()


def test_command_review_disables_execution_for_invalid_filter(qtbot):
    dialog = CommandReviewDialog("(objectClass=person)")
    qtbot.addWidget(dialog)

    assert dialog.execute_button.isEnabled()
    dialog.command.setText("objectClass=person")

    assert not dialog.execute_button.isEnabled()
    assert dialog.result() == 0


def test_command_review_returns_manually_edited_filter(qtbot):
    dialog = CommandReviewDialog("(objectClass=person)")
    qtbot.addWidget(dialog)
    dialog.command.setText("(uid=jdoe)")

    assert dialog.execute_button.isEnabled()
    assert dialog.reviewed_command() == "(uid=jdoe)"


def test_ai_confirmation_supports_global_and_session_overrides(qtbot, tmp_path):
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(
        LocalConfiguration(
            preferences=Preferences(require_ai_confirmation=False),
        )
    )
    window = MainWindow(store=store)
    qtbot.addWidget(window)

    assert not window._requires_ai_confirmation()
    window.session_confirmation_action.setChecked(True)
    assert window._requires_ai_confirmation()
    window.session_confirmation_action.setChecked(False)
    assert not window._requires_ai_confirmation()

    window.close()


def test_profile_transport_sets_secure_default_ports(qtbot):
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.layout().activate()

    assert dialog.transport.currentData() == "plain"
    assert dialog.port.text() == "389"
    assert dialog.authentication_mode.currentData() == "simple"
    assert dialog.bind_dn.isEnabled()
    assert dialog.password.isEnabled()
    assert not dialog.save_password.isChecked()
    assert dialog.show_password.parentWidget() is dialog.save_password.parentWidget()
    assert dialog.show_password.geometry().y() == dialog.save_password.geometry().y()
    assert dialog.password.echoMode() == QLineEdit.EchoMode.Password
    dialog.show_password.setChecked(True)
    assert dialog.password.echoMode() == QLineEdit.EchoMode.Normal
    dialog.show_password.setChecked(False)
    assert dialog.password.echoMode() == QLineEdit.EchoMode.Password
    dialog.transport.setCurrentIndex(2)
    assert dialog.port.text() == "636"

    dialog.port.setText("1636")
    dialog.transport.setCurrentIndex(0)
    assert dialog.port.text() == "1636"


def test_profile_test_buttons_follow_their_input_fields(qtbot):
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    layout = dialog.layout()
    port_row, _ = layout.getWidgetPosition(dialog.port)
    connection_row, _ = layout.getWidgetPosition(dialog.test_network_button)
    password_row, _ = layout.getWidgetPosition(dialog.password)
    authentication_row, _ = layout.getWidgetPosition(dialog.test_authentication_button)
    assert connection_row == port_row + 1
    # The test authentication button should now appear after the password options row.
    # That places it two rows below the password field.
    assert authentication_row == password_row + 2
    assert dialog.transport.currentData() == "plain"
    assert not dialog.plain_warning.isHidden()
    dialog.transport.setCurrentIndex(1)
    assert dialog.plain_warning.isHidden()


def test_add_profile_wizard_guides_save_and_preserves_fields(qtbot, tmp_path, monkeypatch):
    profile_store = ConfigurationStore(tmp_path / "profiles.json")

    class FakeVault:
        def store_password(self, profile, password):
            assert password == ""

    window = MainWindow(store=profile_store, vault=FakeVault())
    qtbot.addWidget(window)

    def complete_wizard(dialog):
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog.pages.currentIndex() == 0
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isVisible()
        dialog.name.setText("staging")
        dialog.host.setText("ldap.test")
        dialog.bind_dn.setText("cn=reader")
        dialog.password.setText("secret")
        dialog.transport.setCurrentIndex(dialog.transport.findData("ldaps"))
        assert dialog.port.text() == "636"
        dialog.next_button.click()
        assert dialog.pages.currentIndex() == 1
        dialog.back_button.click()
        assert dialog.password.text() == "secret"
        dialog.next_button.click()
        dialog.next_button.click()
        assert dialog.pages.currentIndex() == 2
        dialog.base_dn.setEditText("dc=test")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        return dialog.result()

    monkeypatch.setattr(ProfileWizard, "exec", complete_wizard)
    window.add_profile_button.click()

    saved = profile_store.load().profiles
    assert len(saved) == 1
    assert (saved[0].host, saved[0].port, saved[0].transport, saved[0].base_dn) == (
        "ldap.test", 636, "ldaps", "dc=test"
    )
    assert window._session_passwords[saved[0].profile_id] == "secret"
    window.close()


def test_wizard_authentication_discovers_base_dns_without_saving(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.test_authentication",
        lambda service, cancelled=None: "Bind succeeded.",
    )
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.discover_base_dns",
        lambda service, cancelled=None: ["dc=one", "dc=two"],
    )
    dialog = ProfileWizard()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.host.setText("ldap.test")
    dialog.bind_dn.setText("cn=reader")
    dialog.password.setText("secret")
    dialog.next_button.click()
    dialog.test_authentication_button.click()
    assert not dialog.back_button.isEnabled()
    assert not dialog.next_button.isEnabled()
    qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert dialog.next_button.isEnabled()
    dialog.next_button.click()
    assert dialog.base_dn.count() == 2
    assert dialog.base_dn.currentText() == "dc=one"
    dialog.base_dn.setCurrentIndex(1)
    dialog.reject()
    assert dialog.profile is None


def test_wizard_rejects_invalid_profile_and_cancel_does_not_save(qtbot, tmp_path, monkeypatch):
    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    def cancel_after_invalid_save(dialog):
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.next_button.click()
        dialog.next_button.click()
        dialog.base_dn.setEditText("dc=test")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        assert dialog.profile is None
        assert warnings
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(ProfileWizard, "exec", cancel_after_invalid_save)
    window.add_profile_button.click()
    assert store.load().profiles == []
    assert not store.path.exists()
    window.close()


def test_discovered_base_dns_preserve_manual_value_and_report_empty_list(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.base_dn.setEditText("ou=manual,dc=test")
    profile = DirectoryProfile(name="test", host="ldap.test")
    dialog._test_succeeded(profile, {"message": "Bind succeeded.", "base_dns": ["dc=test"]})
    assert dialog.base_dn.currentText() == "ou=manual,dc=test"
    assert dialog.base_dn.count() == 1
    dialog._test_succeeded(profile, {"message": "Bind succeeded.", "base_dns": []})
    assert dialog.base_dn.currentText() == "ou=manual,dc=test"
    assert "advertised no Base DNs" in dialog.test_status.text()


def test_edit_profile_preserves_identity_and_masks_password(qtbot):
    profile = DirectoryProfile(
        name="test", host="ldap.test", base_dn="dc=test", transport="starttls",
        authentication_mode="simple", bind_dn="cn=admin",
        tofu_fingerprint="ab" * 32,
    )
    dialog = ProfileDialog(profile=profile)
    qtbot.addWidget(dialog)
    assert dialog.password.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.password.isEnabled()
    assert dialog.bind_dn.text() == "cn=admin"
    dialog.name.setText("renamed")
    dialog.accept()
    assert dialog.profile.profile_id == profile.profile_id
    assert dialog.profile.tofu_fingerprint == profile.tofu_fingerprint
    assert dialog.profile.name == "renamed"
    dialog.host.setText("other.test")
    dialog.accept()
    assert dialog.profile.tofu_fingerprint == ""


def test_profile_names_must_be_unique_for_add_edit_and_save_as(qtbot, monkeypatch):
    first = DirectoryProfile(name="Lab", host="ldap.test", base_dn="dc=test")
    second = DirectoryProfile(name="Other", host="ldap.test", base_dn="dc=test")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: warnings.append(text))

    wizard = ProfileWizard(existing_profiles=[first, second])
    qtbot.addWidget(wizard)
    wizard.name.setText("  lAb  ")
    wizard.host.setText("ldap.test")
    wizard.authentication_mode.setCurrentIndex(wizard.authentication_mode.findData("anonymous"))
    wizard.base_dn.setEditText("dc=test")
    wizard._show_step(2)
    wizard.accept()
    assert wizard.profile is None

    dialog = ProfileDialog(profile=first, existing_profiles=[first, second])
    qtbot.addWidget(dialog)
    dialog.name.setText(" OTHER ")
    dialog.accept()
    assert dialog.profile is None
    dialog.name.setText("lab")
    assert dialog.save_as_button is not None
    dialog.save_as_button.click()
    assert dialog.profile is None
    assert len(warnings) == 3
    assert all("already exists" in text for text in warnings)
    dialog.name.setText("New name")
    dialog.save_as_button.click()
    assert dialog.profile is not None
    assert dialog.profile.profile_id != first.profile_id
    assert dialog.profile.host == first.host


def test_save_as_preserves_original_and_does_not_copy_secrets_or_tofu(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(
        name="original", host="ldap.test", base_dn="dc=test", transport="starttls",
        authentication_mode="simple", bind_dn="cn=admin", tofu_fingerprint="ab" * 32,
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class Vault:
        def __init__(self):
            self.writes = []

        def get_password(self, _profile):
            return "original-secret"

        def get_client_key_passphrase(self, _profile):
            return "original-passphrase"

        def store_password(self, target, password):
            self.writes.append((target.profile_id, password))

        def store_client_key_passphrase(self, target, passphrase):
            self.writes.append((target.profile_id, passphrase))

    vault = Vault()
    window = MainWindow(store=store, vault=vault)
    qtbot.addWidget(window)
    window._active_profile = window._selected_profile()

    def save_as(dialog):
        assert dialog.password.text() == "original-secret"
        dialog.name.setText("copy")
        dialog.save_as_button.click()
        return dialog.result()

    monkeypatch.setattr(ProfileDialog, "exec", save_as)
    window.edit_profile_button.click()
    original, copied = store.load().profiles
    assert original == profile
    assert copied.name == "copy"
    assert copied.profile_id != profile.profile_id
    assert copied.tofu_fingerprint == ""
    assert vault.writes == []
    assert copied.profile_id not in window._session_passwords
    assert window._active_profile.profile_id == profile.profile_id
    assert window._selected_profile().profile_id == copied.profile_id
    window.close()


def test_save_as_stores_only_newly_entered_credentials(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(
        name="original", host="ldap.test", base_dn="dc=test",
        authentication_mode="simple", bind_dn="cn=admin",
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class Vault:
        def __init__(self):
            self.writes = []

        def get_password(self, _profile):
            return "original-secret"

        def get_client_key_passphrase(self, _profile):
            return "original-passphrase"

        def store_password(self, target, password):
            self.writes.append((target.profile_id, password))

        def store_client_key_passphrase(self, target, passphrase):
            self.writes.append((target.profile_id, passphrase))

    vault = Vault()
    window = MainWindow(store=store, vault=vault)
    qtbot.addWidget(window)

    def save_as(dialog):
        dialog.name.setText("copy")
        dialog.password.setText("new-secret")
        dialog.password.setModified(True)
        dialog.client_key_passphrase.setText("new-passphrase")
        dialog.client_key_passphrase.setModified(True)
        dialog.save_as_button.click()
        return dialog.result()

    monkeypatch.setattr(ProfileDialog, "exec", save_as)
    window.edit_profile_button.click()
    copied = store.load().profiles[1]
    assert vault.writes == [
        (copied.profile_id, "new-secret"), (copied.profile_id, "new-passphrase")
    ]
    assert "secret" not in store.path.read_text()
    window.close()


def test_bind_password_enabled_only_for_simple_authentication(qtbot):
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    assert dialog.authentication_mode.currentData() == "simple"
    assert dialog.password.isEnabled()
    assert dialog.bind_dn.isEnabled()
    dialog.authentication_mode.setCurrentIndex(
        dialog.authentication_mode.findData("anonymous")
    )
    assert not dialog.password.isEnabled()


def test_profile_password_save_choice_uses_keyring_or_session_only(qtbot, tmp_path):
    profile = DirectoryProfile(
        name="test",
        host="ldap.test",
        authentication_mode="simple",
        bind_dn="cn=manager",
    )

    class FakeVault:
        def __init__(self):
            self.password = None
            self.stored = []

        def get_password(self, _profile):
            return self.password

        def store_password(self, _profile, password):
            self.stored.append(password)
            self.password = password or None

    vault = FakeVault()
    window = MainWindow(
        store=ConfigurationStore(tmp_path / "profiles.json"),
        vault=vault,
    )
    qtbot.addWidget(window)

    window._save_profile_password(profile, "session-secret", remember=False)
    assert vault.stored == [""]
    assert window._profile_password(profile) == "session-secret"

    window._save_profile_password(profile, "remembered-secret", remember=True)
    assert vault.stored == ["", "remembered-secret"]
    assert vault.password == "remembered-secret"
    assert profile.profile_id not in window._session_passwords
    window.close()


def test_connect_prompts_for_unsaved_simple_bind_password(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(
        name="test",
        host="ldap.test",
        base_dn="dc=test",
        authentication_mode="simple",
        bind_dn="cn=manager",
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class FakeVault:
        def get_password(self, _profile):
            return None

        def get_client_key_passphrase(self, _profile):
            return None

    window = MainWindow(store=store, vault=FakeVault())
    qtbot.addWidget(window)
    operations = []
    seen_passwords = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("session-only-secret", True),
    )
    monkeypatch.setattr(
        window,
        "_run_worker",
        lambda operation, *_args: operations.append(operation),
    )
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.search_page",
        lambda service, *_args, **_kwargs: (
            seen_passwords.append(service.password) or [],
            None,
        ),
    )

    window._connect()

    assert window._connecting
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.data_lens_tab))
    assert not window.filter_edit.isEnabled()
    assert not window.filter_execute.isEnabled()
    assert not window.ai_generate.isEnabled()

    from threading import Event

    result = operations[0](Event(), lambda *_args: None)
    assert seen_passwords == ["session-only-secret"]
    assert result["profile_id"] == profile.profile_id
    assert window._session_passwords[profile.profile_id] == "session-only-secret"
    window.close()


def test_connect_uses_last_connected_profile_when_nothing_is_selected(
    qtbot, tmp_path, monkeypatch
):
    first = DirectoryProfile(name="first", host="first.ldap.test")
    last_connected = DirectoryProfile(name="last", host="last.ldap.test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(
        LocalConfiguration(
            profiles=[first, last_connected],
            last_connected_profile_id=last_connected.profile_id,
        )
    )

    window = MainWindow(store=store)
    qtbot.addWidget(window)
    assert window._selected_profile() == last_connected
    window.profiles.setCurrentIndex(-1)
    selected_profiles = []
    monkeypatch.setattr(window, "_connect_profile", selected_profiles.append)

    window._connect()

    assert selected_profiles == [last_connected]
    assert window._selected_profile() == last_connected
    window.close()


def test_successful_connection_remembers_profile(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(name="test", host="ldap.test", base_dn="dc=test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    window._active_profile = profile
    window._connecting = True
    monkeypatch.setattr(window, "_show_directory", lambda *_args: None)
    monkeypatch.setattr(window, "_service", lambda *_args: object())
    monkeypatch.setattr(window.data_lens_tab, "set_service", lambda _service: None)

    window._connection_result({"profile_id": profile.profile_id, "entries": []})

    assert window.configuration.last_connected_profile_id == profile.profile_id
    assert store.load().last_connected_profile_id == profile.profile_id
    window.close()


def test_profile_edit_saves_credentials_in_vault_not_json(qtbot, tmp_path, monkeypatch):
    profile = DirectoryProfile(
        name="test", host="ldap.test", base_dn="dc=test",
        authentication_mode="simple", bind_dn="cn=admin",
    )
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class FakeVault:
        def __init__(self):
            self.password = "old-secret"

        def get_password(self, profile):
            return self.password

        def get_client_key_passphrase(self, profile):
            return None

        def store_password(self, profile, password):
            self.password = password or None

        def store_client_key_passphrase(self, profile, password):
            self.passphrase = password

    vault = FakeVault()
    window = MainWindow(store=store, vault=vault)
    qtbot.addWidget(window)
    window._active_profile = window._selected_profile()

    def accept_edit(dialog):
        assert dialog.password.text() == "old-secret"
        assert dialog.save_password.isChecked()
        dialog.password.setText("new-secret")
        dialog.save_password.setChecked(False)
        dialog.name.setText("updated")
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ProfileDialog, "exec", accept_edit)
    window.edit_profile_button.click()
    assert vault.password is None
    assert window._profile_password(profile) == "new-secret"
    assert store.load().profiles[0].profile_id == profile.profile_id
    assert store.load().profiles[0].name == "updated"
    assert "secret" not in store.path.read_text()
    assert window._active_profile is None
    window.close()


def test_delete_profile_removes_saved_secrets_and_disconnects_active_profile(
    qtbot, tmp_path, monkeypatch
):
    profile = DirectoryProfile(name="test", host="ldap.test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(
        LocalConfiguration(
            profiles=[profile],
            last_connected_profile_id=profile.profile_id,
        )
    )

    class FakeVault:
        def __init__(self):
            self.deleted_passwords = []
            self.deleted_passphrases = []

        def delete_password(self, target):
            self.deleted_passwords.append(target.profile_id)

        def store_client_key_passphrase(self, target, passphrase):
            assert passphrase == ""
            self.deleted_passphrases.append(target.profile_id)

    class FakeTrustStore:
        def __init__(self):
            self.deleted_profiles = []

        def delete_tofu(self, target):
            self.deleted_profiles.append(target.profile_id)

    vault = FakeVault()
    window = MainWindow(store=store, vault=vault)
    qtbot.addWidget(window)
    trust_store = FakeTrustStore()
    window.trust_store = trust_store
    window._active_profile = window._selected_profile()
    window._session_passwords[profile.profile_id] = "session-secret"
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window.delete_profile_button.click()

    assert store.load().profiles == []
    assert store.load().last_connected_profile_id is None
    assert window.configuration.profiles == []
    assert vault.deleted_passwords == [profile.profile_id]
    assert vault.deleted_passphrases == [profile.profile_id]
    assert trust_store.deleted_profiles == [profile.profile_id]
    assert profile.profile_id not in window._session_passwords
    assert window._active_profile is None
    assert not window.delete_profile_button.isEnabled()
    window.close()


def test_delete_active_profile_stops_when_unsaved_changes_are_not_discarded(
    qtbot, tmp_path, monkeypatch
):
    profile = DirectoryProfile(name="test", host="ldap.test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class FakeVault:
        def delete_password(self, _profile):
            pytest.fail("profile credentials must not be deleted")

        def store_client_key_passphrase(self, _profile, _passphrase):
            pytest.fail("profile credentials must not be deleted")

    window = MainWindow(store=store, vault=FakeVault())
    qtbot.addWidget(window)
    window._active_profile = window._selected_profile()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(window, "_confirm_leave_entry", lambda: False)

    window.delete_profile_button.click()

    assert store.load().profiles == [profile]
    assert window._active_profile == profile
    monkeypatch.undo()
    window.close()


def test_delete_profile_keeps_saved_profile_when_credential_cleanup_fails(
    qtbot, tmp_path, monkeypatch
):
    profile = DirectoryProfile(name="test", host="ldap.test")
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.save(LocalConfiguration(profiles=[profile]))

    class FailingVault:
        def delete_password(self, _profile):
            raise ConfigurationError("keyring unavailable")

    window = MainWindow(store=store, vault=FailingVault())
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, title, text: messages.append((title, text)),
    )

    window.delete_profile_button.click()

    assert store.load().profiles == [profile]
    assert window.configuration.profiles == [profile]
    assert messages and messages[0][0] == "Profile deletion incomplete"
    window.close()


def test_main_window_does_not_show_profile_diagnostic_buttons(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)

    labels = [button.text() for button in window.findChildren(QPushButton)]
    assert "Test network" not in labels
    assert "Test authentication" not in labels
    assert not hasattr(window, "test_network_button")
    assert not hasattr(window, "test_authentication_button")
    window.close()


def test_add_profile_network_test_uses_unsaved_host_and_port(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        dialog.host.setText("127.0.0.1")
        dialog.port.setText(str(listener.getsockname()[1]))
        dialog.test_network_button.click()
        assert not dialog.host.isEnabled()
        assert not dialog.test_authentication_button.isEnabled()
        qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert "TCP connection to 127.0.0.1" in dialog.test_status.text()
    assert "authentication were not tested" in dialog.test_status.text()
    assert dialog.profile is None
    dialog.host.setText("other.test")
    assert "Settings changed" in dialog.test_status.text()


def test_dialog_authentication_uses_entered_password_without_saving(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    captured = []

    def authenticate(service, cancelled=None):
        captured.append((service.profile.host, service.profile.bind_dn, service.password))
        return "LDAP authentication succeeded (simple)."

    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.test_authentication", authenticate
    )
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.discover_base_dns",
        lambda self, cancelled=None: ["dc=draft", "dc=other"],
    )
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.host.setText("draft.test")
    dialog.transport.setCurrentIndex(0)
    dialog.authentication_mode.setCurrentIndex(1)
    dialog.bind_dn.setText("cn=draft")
    dialog.password.setText("draft-secret")
    dialog.test_authentication_button.click()
    qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert captured == [("draft.test", "cn=draft", "draft-secret")]
    assert dialog.profile is None
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.password.isEnabled()
    assert dialog.base_dn.count() == 2
    assert dialog.base_dn.currentText() == "dc=draft"
    dialog.base_dn.setCurrentIndex(1)
    assert dialog.base_dn.currentText() == "dc=other"
    dialog.host.setText("changed.test")
    assert dialog.base_dn.count() == 0
    assert dialog.base_dn.currentText() == "dc=other"


def test_dialog_missing_password_reports_failure_before_network(qtbot, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.host.setText("draft.test")
    dialog.authentication_mode.setCurrentIndex(1)
    dialog.bind_dn.setText("cn=draft")
    dialog.test_authentication_button.click()
    assert dialog._test_worker is None
    assert "Enter a bind password" in dialog.test_status.text()
    assert warnings


def test_dialog_cancel_waits_for_worker_and_does_not_show_success(qtbot, monkeypatch):
    from threading import Event

    release = Event()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))

    def delayed_network(service, cancelled=None):
        assert release.wait(5)
        return "TCP success"

    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.test_network", delayed_network
    )
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.host.setText("draft.test")
    dialog.test_network_button.click()
    try:
        assert dialog.cancel_test_button.isEnabled()
        dialog.cancel_test_button.click()
        assert dialog._test_worker.cancelled.is_set()
        assert "Cancellation requested" in dialog.test_status.text()
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert not messages
    assert dialog.test_network_button.isEnabled()
    assert not dialog.cancel_test_button.isEnabled()


@pytest.mark.parametrize("accept_trust", [True, False])
@pytest.mark.parametrize("dialog_class", [ProfileDialog, ProfileWizard])
def test_dialog_tls_trust_is_staged_until_save(
    qtbot, tmp_path, monkeypatch, accept_trust, dialog_class
):
    from test_certificates import make_certificate

    from datasifter.security.certificates import CertificateTrustStore

    certificate, _ = make_certificate("draft.test")
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(
        "datasifter.ui.main_window.confirm_certificate_trust",
        lambda *args: accept_trust,
    )
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.inspect_server_certificate",
        lambda *args: certificate,
    )
    bound = []

    def authenticate(service, cancelled=None):
        path = service.trust_store.certificate_path(service.profile)
        assert path.read_bytes() == certificate
        bound.append(path)
        return "LDAP authentication succeeded (simple)."

    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.test_authentication", authenticate
    )
    monkeypatch.setattr(
        "datasifter.ui.main_window.LDAPService.discover_base_dns",
        lambda self, cancelled=None: ["dc=test"],
    )
    dialog = dialog_class()
    qtbot.addWidget(dialog)
    dialog.name.setText("draft")
    dialog.host.setText("draft.test")
    dialog.transport.setCurrentIndex(1)
    dialog.base_dn.setEditText("dc=test")
    dialog.authentication_mode.setCurrentIndex(1)
    dialog.bind_dn.setText("cn=draft")
    dialog.password.setText("secret")
    if isinstance(dialog, ProfileWizard):
        dialog.next_button.click()
    dialog.test_authentication_button.click()
    qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert not bound
    if not accept_trust:
        assert dialog.trusted_certificate is None
        assert "not trusted" in dialog.test_status.text()
        return
    assert dialog.trusted_certificate == certificate
    dialog.test_authentication_button.click()
    qtbot.waitUntil(lambda: dialog._test_worker is None)
    assert len(bound) == 1
    assert not bound[0].exists()
    if isinstance(dialog, ProfileWizard):
        dialog.next_button.click()
    dialog.accept()
    assert dialog.profile.tofu_fingerprint
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.trust_store = CertificateTrustStore(tmp_path / "certificates")
    window._save_dialog_trust(dialog, dialog.profile)
    assert window.trust_store.certificate_path(dialog.profile).read_bytes() == certificate
    window.close()
    dialog.host.setText("other.test")
    assert dialog.trusted_certificate is None
    assert dialog._profile_from_fields().tofu_fingerprint == ""


def test_profile_save_failure_restores_original_trust(qtbot, tmp_path, monkeypatch):
    from test_certificates import make_certificate

    from datasifter.config import ConfigurationError
    from datasifter.security.certificates import CertificateTrustStore

    original, _ = make_certificate("draft.test")
    replacement, _ = make_certificate("draft.test")
    profile = DirectoryProfile(name="draft", host="draft.test", base_dn="dc=test")
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.trust_store = CertificateTrustStore(tmp_path / "certificates")
    window.trust_store.accept_tofu(profile, original)
    dialog = ProfileDialog(profile=profile)
    qtbot.addWidget(dialog)
    dialog.trusted_certificate = replacement

    def failed_save(configuration):
        raise ConfigurationError("save failed")

    monkeypatch.setattr(window.store, "save", failed_save)
    with pytest.raises(ConfigurationError, match="save failed"):
        window._save_profile_configuration(dialog, dialog._profile_from_fields())
    assert window.trust_store.certificate_path(profile).read_bytes() == original
    window.close()


def test_operational_toggle_reloads_entry_from_server(qtbot, tmp_path, monkeypatch):
    from threading import Event

    from PyQt6.QtCore import Qt

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="lab", host="ldap.test", base_dn="dc=test")
    dn = "uid=user.8,ou=People,dc=test"
    reads = []
    user = {"objectClass": ["person"], "uid": ["user.8"]}
    operational = {"entryUUID": ["1234"], "ds-entry-checksum": ["99"], "modifyTimestamp": ["x"]}

    class FakeService:
        def read_entry(self, distinguished_name, include_operational=False, cancelled=None):
            reads.append(include_operational)
            attributes = dict(user)
            if include_operational:
                attributes.update(operational)
            return {"dn": distinguished_name, "attributes": attributes}

    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(window, "_profile_password", lambda *_args: None)
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, success, *_args, **_kwargs: success(
            operation(Event(), lambda *_args: None)
        ),
    )
    window._show_directory([{"dn": dn, "attributes": user}])
    item = window.tree.topLevelItem(0).child(0)

    def shown():
        return {
            window.attributes.item(row, 0).text(): window.attributes.item(row, 1)
            for row in range(window.attributes.rowCount())
        }

    window._select_entry(item, 0)
    assert reads == []
    assert set(shown()) == {"objectClass", "uid"}

    window.operational_toggle.setChecked(True)
    assert reads == [True]
    rows = shown()
    assert set(rows) == {"objectClass", "uid", "entryUUID", "ds-entry-checksum", "modifyTimestamp"}
    for name in operational:
        assert not rows[name].flags() & Qt.ItemFlag.ItemIsEditable
    assert rows["uid"].flags() & Qt.ItemFlag.ItemIsEditable

    window._select_entry(item, 0)
    assert reads == [True]

    window.operational_toggle.setChecked(False)
    assert reads == [True, False]
    assert set(shown()) == {"objectClass", "uid"}
    window.close()


def _attribute_row(window, name):
    for row in range(window.attributes.rowCount()):
        if window.attributes.item(row, 0).text() == name:
            return row
    raise AssertionError(name)


def test_tab_commits_value_and_moves_to_next_editable_value(qtbot, tmp_path):
    from PyQt6.QtCore import Qt

    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    window.show()
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._selected_dn = "cn=test,dc=test"
    window._display_attributes(
        {"description": ["d"], "mail": ["m"], "entryUUID": ["u"], "cn": ["c"]}
    )
    description = _attribute_row(window, "description")
    mail = _attribute_row(window, "mail")
    window.attributes.setCurrentCell(description, 1)
    window.attributes.editItem(window.attributes.item(description, 1))
    editor = window.attributes.findChild(QPlainTextEdit)
    editor.selectAll()
    qtbot.keyClicks(editor, "new")
    qtbot.keyClick(editor, Qt.Key.Key_Tab)

    assert window.attributes.item(description, 1).text() == "new"
    assert window.attributes.currentRow() == mail
    editors = [
        child for child in window.attributes.findChildren(QPlainTextEdit) if child.isVisible()
    ]
    assert len(editors) == 1
    editor = editors[0]
    editor.selectAll()
    qtbot.keyClicks(editor, "x@test")
    qtbot.keyClick(editor, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)

    assert window.attributes.item(mail, 1).text() == "x@test"
    assert window.attributes.currentRow() == description
    assert window.save_entry_button.isEnabled()
    window._clear_entry_view()
    window.close()


def test_searched_attributes_are_highlighted_and_preference_is_saved(qtbot, tmp_path):
    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    window._active_profile = DirectoryProfile(name="test", host="ldap.test")
    window._show_directory(
        [{"dn": "cn=a,dc=test", "attributes": {}}],
        searched_attributes={"telephonenumber", "mobile"},
    )
    window._display_attributes(
        {"cn": ["a"], "telephoneNumber": ["732-555-0100"], "mobile": ["1"], "mail": ["a@t"]}
    )

    def bold(name):
        row = _attribute_row(window, name)
        return [window.attributes.item(row, column).font().bold() for column in (0, 1)]

    assert bold("telephoneNumber") == [True, True]
    assert bold("mobile") == [True, True]
    assert bold("cn") == [False, False]
    assert not window.save_entry_button.isEnabled()

    window.highlight_search_action.setChecked(False)
    assert bold("telephoneNumber") == [False, False]
    assert store.load().preferences.highlight_search_attributes is False

    window.highlight_search_action.setChecked(True)
    assert bold("mobile") == [True, True]
    window.close()
