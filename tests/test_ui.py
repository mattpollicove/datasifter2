import socket

import pytest
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from datasifter import __version__
from datasifter.build_info import build_label
from datasifter.config import ConfigurationStore, DirectoryProfile, LocalConfiguration, Preferences
from datasifter.ui.main_window import (
    AttributeValueDelegate,
    CommandReviewDialog,
    LDAPFilterHighlighter,
    MainWindow,
    ProfileDialog,
)


def test_ui_displays_current_build_version(qtbot, tmp_path):
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    assert window.windowTitle() == f"DataSifter 2.0 - v{__version__} ({build_label()})"
    assert build_label().startswith("source 20")
    assert window.version_label.text() == f"Version {__version__}"
    window.statusBar().showMessage("Operation failed")
    assert window.version_label.text() == f"Version {__version__}"
    window.close()


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
    assert not window.ai_generate.isEnabled()
    preferences = window.configuration.preferences
    preferences.model_path = str(tmp_path / "approved.gguf")
    window._refresh_model_status()
    assert not window.ai_generate.isEnabled()
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


def test_settings_save_hide_reenable_and_persist_lens(qtbot, tmp_path):
    from datasifter.ui.settings_dialog import SettingsDialog

    store = ConfigurationStore(tmp_path / "profiles.json")
    window = MainWindow(store=store)
    qtbot.addWidget(window)
    window._loaded_model = ("approved.gguf", "a" * 64, object())
    dialog = SettingsDialog(window.configuration.preferences, window)
    qtbot.addWidget(dialog)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Directory Lens", "Safety", "Appearance"
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
    assert "Directory Lens disabled" in window.ai_prompt_log.toPlainText()
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

    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: messages.append((title, text)),
    )
    window.configuration.profiles.append(profile)
    window._refresh_profiles()
    assert window.list_profiles_button.isEnabled()
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

    class FakeService:
        def modify_entry(self, dn, changes, cancelled):
            captured.append(changes)

    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    monkeypatch.setattr(
        window, "_run_worker",
        lambda operation, *_args: operation(Event(), lambda *_args: None),
    )
    window._save_entry_changes()
    assert captured == [{"cn": [("replace", ["updated"])]}]
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

    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUFapproved")
    created = []
    received = []

    class FakeGenerator:
        def __init__(self, path, sha256):
            assert sha256 == model_sha256(path)
            created.append(path)

        def generate_filter(
            self, request, classes, attributes, on_token=None, cancelled=None, on_prompt=None
        ):
            received.append((classes, attributes))
            on_prompt(f"PROMPT for {request}")
            on_token("(cn=a)")
            return "(cn=a)"

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

    monkeypatch.setattr(window, "_review_generated_filter", reviewed.append)
    monkeypatch.setattr(window, "_profile_password", lambda _profile: "secret")
    monkeypatch.setattr(window, "_service", lambda *_args: FakeService())
    window.ai_request.setPlainText("people named a")
    for _attempt in range(2):
        window._generate_filter()
        qtbot.waitUntil(lambda: not window._workers)
    assert reviewed == ["(cn=a)", "(cn=a)"]
    assert created == [str(model_path)]
    assert schema_requests == [True]
    assert received[-1] == (["inetOrgPerson"], ["telephoneNumber"])
    log = window.ai_prompt_log.toPlainText()
    assert log.count("Request: people named a") == 2
    assert log.count("PROMPT for people named a") == 2
    assert "model.gguf" in log
    assert "Model output: (cn=a)" in log and "Filter: (cn=a)" in log
    assert window.tabs.tabText(window.tabs.count() - 1) == "Directory Lens log"
    window.clear_prompts_button.click()
    assert window.ai_prompt_log.toPlainText() == ""
    window.close()


def test_failed_generation_is_logged_with_prompt(qtbot, tmp_path, monkeypatch):
    class FakeGenerator:
        def __init__(self, *_args):
            pass

        def generate_filter(self, request, *_args, on_prompt=None, **_kwargs):
            on_prompt("PROMPT TEXT")
            raise RuntimeError("unusable filter (state=pa)")

    class FakeService:
        def schema_options(self, _cancelled):
            return {"object_classes": {}, "attributes": ["st"]}

    monkeypatch.setattr("datasifter.ui.main_window.LlamaCppFilterGenerator", FakeGenerator)
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
    assert "Request: people in pa" in log
    assert "PROMPT TEXT" in log
    assert "Error:" in log and "unusable filter" in log
    assert errors
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
    assert "loads on first use" in window.model_status.text()
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
    assert authentication_row == password_row + 1
    assert dialog.transport.currentData() == "plain"
    assert not dialog.plain_warning.isHidden()
    dialog.transport.setCurrentIndex(1)
    assert dialog.plain_warning.isHidden()


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

    from threading import Event

    result = operations[0](Event(), lambda *_args: None)
    assert seen_passwords == ["session-only-secret"]
    assert result["profile_id"] == profile.profile_id
    assert window._session_passwords[profile.profile_id] == "session-only-secret"
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
def test_dialog_tls_trust_is_staged_until_save(qtbot, tmp_path, monkeypatch, accept_trust):
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
    dialog = ProfileDialog()
    qtbot.addWidget(dialog)
    dialog.name.setText("draft")
    dialog.host.setText("draft.test")
    dialog.transport.setCurrentIndex(1)
    dialog.base_dn.setEditText("dc=test")
    dialog.authentication_mode.setCurrentIndex(1)
    dialog.bind_dn.setText("cn=draft")
    dialog.password.setText("secret")
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
