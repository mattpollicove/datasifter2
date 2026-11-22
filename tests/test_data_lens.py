"""Unit tests for :class:`datasifter.ui.data_lens.DataLens`.

The tests use a lightweight mock of :class:`datasifter.directory.service.LDAPService`
to verify that CRUD operations correctly start a :class:`DirectoryWorker` and
emit the expected signals.
"""

import pytest
from PyQt6.QtTest import QSignalSpy

from datasifter.config import DirectoryProfile
from datasifter.directory.service import LDAPService
from datasifter.ui.data_lens import DataLens


class MockLDAPService(LDAPService):
    """A minimal mock that records calls and returns deterministic results."""

    def __init__(self, profile, password=None, client_key_passphrase=None, trust_store=None):
        super().__init__(profile, password, client_key_passphrase, trust_store)
        self.calls = []
        self.search_results = []

    def add_entry(self, dn, attributes, cancelled=None):
        self.calls.append(("add_entry", dn, attributes))
        return {"dn": dn, "attributes": attributes}

    def modify_entry(self, dn, changes, cancelled=None):
        self.calls.append(("modify_entry", dn, changes))
        return {"dn": dn, "changes": changes}

    def delete_entry(self, dn, recursive=False, cancelled=None):
        self.calls.append(("delete_entry", dn, recursive))
        return {"dn": dn, "recursive": recursive}

    def move_entry(self, dn, relative_dn, new_superior=None, delete_old_rdn=True, cancelled=None):
        self.calls.append(("move_entry", dn, relative_dn, new_superior))
        return {"dn": dn, "relative_dn": relative_dn, "new_superior": new_superior}

    def search_subtree(self, base_dn: str, filter: str, attributes: list[str] | None = None, recursive: bool = True):
        # Return an empty list to avoid network calls during tests.
        return []

    def search(self, *args, **kwargs):
        return self.search_results


def test_nlp_create_command(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    tab = DataLensTab(lens.service)
    tab.cmd_input.setText('create entry cn=Test,dc=example,dc=com with attributes {"objectClass":["inetOrgPerson"],"cn":["Test"]}')
    tab._execute_command()
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)
    assert lens.service.calls[-1] == (
        "add_entry",
        "cn=Test,dc=example,dc=com",
        {"objectClass": ["inetOrgPerson"], "cn": ["Test"]},
    )
    tab.close()


def test_nlp_create_user_builds_entry_without_logging_password(lens, qtbot, monkeypatch, caplog):
    import logging

    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    caplog.set_level(logging.DEBUG, logger="datasifter")
    tab = DataLensTab(lens.service)
    tab.submit_command(
        "create user Matt Pollicove in the people OU, cn is mattp, password is test-secret"
    )
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)

    assert lens.service.calls[-1] == (
        "add_entry",
        "cn=mattp,ou=people,dc=example,dc=com",
        {
            "cn": ["mattp"],
            "sn": ["Pollicove"],
            "givenName": ["Matt"],
            "userPassword": ["test-secret"],
            "objectClass": ["inetOrgPerson"],
        },
    )
    assert "test-secret" not in caplog.text
    tab.close()


def test_nlp_update_command(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    tab = DataLensTab(lens.service)
    tab.cmd_input.setText(
        'update entry cn=alexsmith,ou=people,dc=example,dc=com '
        'changes={"sn":[["replace",["Smith"]]]}'
    )
    tab._execute_command()
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)
    assert lens.service.calls[-1] == (
        "modify_entry",
        "cn=alexsmith,ou=people,dc=example,dc=com",
        {"sn": [["replace", ["Smith"]]]},
    )
    tab.close()


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("_create_entry", "Invalid attributes JSON:"),
        ("_update_entry", "Invalid changes JSON:"),
    ],
)
def test_crud_dialog_reports_invalid_json(lens, qtbot, monkeypatch, method, message):
    from PyQt6.QtWidgets import QInputDialog

    from datasifter.ui import data_lens_tab

    tab = data_lens_tab.DataLensTab(lens.service)
    qtbot.addWidget(tab)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("cn=test", True))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *_args: ("{bad}", True))
    monkeypatch.setattr(tab, "_selected_entry_dn", lambda: "cn=test")
    logged = []
    monkeypatch.setattr(data_lens_tab, "log_exception", logged.append)

    getattr(tab, method)()

    assert message in tab.log.toPlainText()
    assert len(logged) == 1 and isinstance(logged[0], ValueError)
    assert not lens.service.calls


def test_command_reports_failed_ldap_lookup(lens, qtbot, monkeypatch):
    from datasifter.directory.service import LDAPOperationError
    from datasifter.ui import data_lens_tab

    tab = data_lens_tab.DataLensTab(lens.service)
    qtbot.addWidget(tab)
    error = LDAPOperationError("directory unavailable")

    def fail_search(*_args, **_kwargs):
        raise error

    logged = []
    monkeypatch.setattr(lens.service, "search", fail_search)
    monkeypatch.setattr(data_lens_tab, "log_exception", logged.append)
    tab.submit_command("delete entry alexsmith")

    assert "Command error: directory unavailable" in tab.log.toPlainText()
    assert logged == [error]
    assert not lens.service.calls


def test_command_reports_unrecognized_input(lens, qtbot, monkeypatch):
    from datasifter.ui import data_lens_tab

    tab = data_lens_tab.DataLensTab(lens.service)
    qtbot.addWidget(tab)
    logged = []
    monkeypatch.setattr(data_lens_tab, "log_exception", logged.append)

    tab.submit_command("unrecognized command")

    assert "Command error: Unrecognized command" in tab.log.toPlainText()
    assert len(logged) == 1 and isinstance(logged[0], ValueError)
    assert not lens.service.calls


@pytest.mark.parametrize(
    ("attribute_name", "expected_attribute"),
    [
        ("telephonenumber", "telephoneNumber"),
        ("telephone number", "telephoneNumber"),
        ("phone number", "telephoneNumber"),
        ("phonenumber", "telephoneNumber"),
        ("mobile phone", "mobile"),
        ("mobile number", "mobile"),
        ("cell phone", "mobile"),
        ("cell number", "mobile"),
        ("cellular", "mobile"),
        ("cellular phone", "mobile"),
    ],
)
def test_add_attribute_to_user_maps_phone_aliases(
    lens, qtbot, monkeypatch, attribute_name, expected_attribute
):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    lens.service.search_results = [{"dn": "uid=mattp,ou=people,dc=example,dc=com"}]
    tab = DataLensTab(lens.service)
    tab.submit_command(f"add {attribute_name} to mattp 732 555 1212")
    qtbot.waitUntil(
        lambda: any(call[0] == "modify_entry" for call in lens.service.calls),
        timeout=1000,
    )

    assert lens.service.calls[-1] == (
        "modify_entry",
        "uid=mattp,ou=people,dc=example,dc=com",
        {expected_attribute: [("add", ["732 555 1212"])]},
    )
    tab.close()


def test_add_attribute_to_user_rejects_ambiguous_identifier(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    lens.service.search_results = [
        {"dn": "uid=mattp,ou=people,dc=example,dc=com"},
        {"dn": "cn=mattp,ou=contractors,dc=example,dc=com"},
    ]
    tab = DataLensTab(lens.service)
    tab.submit_command("add phone to mattp 7325551212")
    qtbot.waitUntil(lambda: "Multiple users matched" in tab.log.toPlainText(), timeout=1000)

    assert not any(call[0] == "modify_entry" for call in lens.service.calls)
    tab.close()


def test_nlp_delete_command(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    tab = DataLensTab(lens.service)
    tab.cmd_input.setText("delete entry dn=cn=alexsmith,ou=people,dc=example,dc=com")
    tab._execute_command()
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)
    assert lens.service.calls[-1][0] == "delete_entry"


def test_nlp_update_user_command(lens, qtbot, monkeypatch):
    """Verify that the new ``update user`` natural‑language syntax works.

    The command should resolve the identifier to a DN, construct a replace
    change for the attribute, and invoke :meth:`DataLens.modify_entry`.
    """
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    # Provide a deterministic search result for the identifier ``mattp``.
    lens.service.search_results = [{"dn": "uid=mattp,ou=people,dc=example,dc=com"}]
    tab = DataLensTab(lens.service)
    tab.submit_command("update user mattp password to @newpass")
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)
    # The last call should be a modify_entry with a replace change.
    assert lens.service.calls[-1][0] == "modify_entry"
    assert lens.service.calls[-1][1] == "uid=mattp,ou=people,dc=example,dc=com"
    assert lens.service.calls[-1][2] == {"userPassword": [("replace", ["@newpass"])]}
    tab.close()


def test_attribute_first_update_command_uses_prompt_data_without_followups(
    lens, qtbot, monkeypatch
):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: pytest.fail("The command contains all required data"),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *_args, **_kwargs: pytest.fail("The command should not request changes JSON"),
    )
    lens.service.search_results = [{"dn": "uid=mattp,ou=people,dc=example,dc=com"}]
    tab = DataLensTab(lens.service)

    tab.submit_command("update phone number to mattp 732 555 1414")
    qtbot.waitUntil(lambda: any(call[0] == "modify_entry" for call in lens.service.calls), timeout=1000)

    assert lens.service.calls[-1] == (
        "modify_entry",
        "uid=mattp,ou=people,dc=example,dc=com",
        {"telephoneNumber": [("replace", ["732 555 1414"])]},
    )
    tab.close()


def test_update_attribute_for_user_confirms_prompt_fields_and_replaces_value(
    lens, qtbot, monkeypatch
):
    from PyQt6.QtWidgets import QInputDialog

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: pytest.fail("The command contains all required data"),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *_args, **_kwargs: pytest.fail("The command should not request changes JSON"),
    )
    lens.service.search_results = [{"dn": "uid=mattp,ou=people,dc=example,dc=com"}]
    tab = DataLensTab(lens.service)
    confirmations = []

    def confirm(action, operation, confirmation_message=None):
        confirmations.append(confirmation_message)
        operation()

    monkeypatch.setattr(tab, "_confirm_and_execute", confirm)
    tab.submit_command("update phone for mattp to 732 9951757")
    qtbot.waitUntil(
        lambda: any(call[0] == "modify_entry" for call in lens.service.calls),
        timeout=1000,
    )

    assert confirmations == ["Update telephoneNumber for user mattp to 732 9951757?"]
    assert lens.service.calls[-1] == (
        "modify_entry",
        "uid=mattp,ou=people,dc=example,dc=com",
        {"telephoneNumber": [("replace", ["732 9951757"])]},
    )
    tab.close()


def test_unrecognized_commands_show_crud_examples_and_search_guidance(lens):
    from datasifter.ui.data_lens_tab import DataLensTab

    tab = DataLensTab(lens.service)
    tab.submit_command("show user mattp")
    message = tab.log.toPlainText()

    assert "Directory Workbench search prompt" in message
    assert "show user mattp" in message
    assert "Create (prompts for password): create user" in message
    assert "Add phone: add phone number" in message
    assert "Add mobile: add cell phone" in message
    assert "Update: update entry" in message
    assert "Move: move entry" in message
    assert "Delete: delete entry" in message
    assert lens.service.calls == []
    tab.close()


def test_shown_crud_examples_are_accepted_by_parser(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("test-password", True))
    tab = DataLensTab(lens.service)
    examples = (
        "create user Alex Smith in the people OU, cn is alexsmith",
        'update entry cn=alexsmith,ou=people,dc=example,dc=com changes={"sn":[["replace","Smith"]]}',
        "move entry cn=alexsmith,ou=people,dc=example,dc=com to cn=alexsmith,ou=staff,dc=example,dc=com",
        "delete entry dn=cn=alexsmith,ou=people,dc=example,dc=com",
    )
    expected_operations = ("add_entry", "modify_entry", "move_entry", "delete_entry")

    for example, expected_operation in zip(examples, expected_operations):
        previous_call_count = len(lens.service.calls)
        tab.submit_command(example)
        qtbot.waitUntil(
            lambda previous_call_count=previous_call_count: (
                len(lens.service.calls) > previous_call_count
            ),
            timeout=1000,
        )
        assert lens.service.calls[-1][0] == expected_operation

    tab.close()


def test_nlp_move_command(lens, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    tab = DataLensTab(lens.service)
    tab.cmd_input.setText('move entry cn=Test,dc=example,dc=com to cn=NewName under ou=Users,dc=example,dc=com')
    tab._execute_command()
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)
    assert lens.service.calls[-1] == (
        "move_entry",
        "cn=Test,dc=example,dc=com",
        "cn=NewName",
        "ou=Users,dc=example,dc=com",
    )
    tab.close()


def test_unclear_crud_command_shows_copyable_query_and_example(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from datasifter.ui.data_lens_tab import CommandClarificationDialog, DataLensTab

    dialogs = []

    def capture_dialog(dialog):
        dialogs.append((dialog.original.toPlainText(), dialog.example.toPlainText()))
        assert dialog.original.isReadOnly()
        assert dialog.example.isReadOnly()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(CommandClarificationDialog, "exec", capture_dialog)
    tab = DataLensTab()
    query = "create"
    tab.cmd_input.setText(query)

    tab._parse_and_execute(query)

    assert dialogs == [("create", "create user Alex Smith in the people OU, cn is alexsmith")]
    tab.close()


def test_clarification_rewrite_is_editable_and_not_applied_until_selected(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from datasifter.ui.data_lens_tab import CommandClarificationDialog

    callbacks = []
    dialog = CommandClarificationDialog(
        "create", "create user example", "create someone",
        lambda query, example, success, failure: callbacks.append((query, example, success, failure)),
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.original.selectAll()
    qtbot.keyClick(dialog.original, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "create someone"
    dialog.example.selectAll()
    qtbot.keyClick(dialog.example, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "create user example"
    dialog.rewrite_button.click()
    assert callbacks[0][:2] == ("create someone", "create user example")
    assert not dialog.rewrite_button.isEnabled()
    callbacks[0][2]("create user generated")
    assert dialog.selected_command() == ""
    dialog.draft.setText("create user reviewed")
    dialog.use_draft.click()
    assert dialog.selected_command() == "create user reviewed"
    callbacks[0][3]("late failure")
    assert "late failure" not in dialog.status.text()


def test_clarification_reports_rewrite_failure_and_allows_retry(qtbot):
    from datasifter.ui.data_lens_tab import CommandClarificationDialog

    dialog = CommandClarificationDialog(
        "delete", "delete entry example", "delete",
        lambda _query, _example, _success, failure: failure("model unavailable"),
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.rewrite_button.click()
    assert "model unavailable" in dialog.status.text()
    assert dialog.rewrite_button.isEnabled()
    assert not dialog.use_draft.isEnabled()
    dialog.close()


@pytest.mark.parametrize(
    ("button_name", "dialog_answers", "expected_call"),
    [
        (
            "update_btn",
            {"changes": '{"description":[["replace",["updated"]]]}'},
            (
                "modify_entry",
                "cn=selected,ou=people,dc=example,dc=com",
                {"description": [["replace", ["updated"]]]},
            ),
        ),
        (
            "delete_btn",
            {"recursive": "No"},
            ("delete_entry", "cn=selected,ou=people,dc=example,dc=com", False),
        ),
        (
            "move_btn",
            {"text": ["cn=renamed", "ou=staff,dc=example,dc=com"]},
            (
                "move_entry",
                "cn=selected,ou=people,dc=example,dc=com",
                "cn=renamed",
                "ou=staff,dc=example,dc=com",
            ),
        ),
    ],
)
def test_entry_action_buttons_use_highlighted_entry(
    lens, qtbot, monkeypatch, button_name, dialog_answers, expected_call
):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    from datasifter.ui.data_lens_tab import DataLensTab

    distinguished_name = "cn=selected,ou=people,dc=example,dc=com"
    lens.service.search_results = [{"dn": distinguished_name, "objectClass": ["person"]}]
    tab = DataLensTab(lens.service)
    qtbot.addWidget(tab)
    data_lens = tab.lens
    qtbot.waitUntil(lambda: data_lens.model.rowCount() == 1, timeout=1000)
    data_lens.view.setCurrentIndex(data_lens.model.index(0, 0))

    confirmations = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, message, *_args: (
            confirmations.append(message) or QMessageBox.StandardButton.Yes
        ),
    )
    text_answers = iter(dialog_answers.get("text", []))
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *_args, **_kwargs: (dialog_answers.get("changes", ""), True),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: (dialog_answers.get("recursive", "No"), True),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: (next(text_answers), True),
    )

    getattr(tab, button_name).click()
    qtbot.waitUntil(lambda: bool(lens.service.calls), timeout=1000)

    assert lens.service.calls[-1] == expected_call
    assert confirmations and distinguished_name in confirmations[0]
    tab.close()


def test_refresh_tree_reloads_entries_and_preserves_highlighted_dn(lens, qtbot):
    from PyQt6.QtCore import Qt

    from datasifter.ui.data_lens_tab import DataLensTab

    distinguished_name = "cn=selected,ou=people,dc=example,dc=com"
    lens.service.search_results = [{"dn": distinguished_name, "objectClass": ["person"]}]
    tab = DataLensTab(lens.service)
    qtbot.addWidget(tab)
    tree = tab.lens
    qtbot.waitUntil(lambda: tree.model.rowCount() == 1, timeout=1000)
    tree.view.setCurrentIndex(tree.model.index(0, 0))

    lens.service.search_results = [
        {"dn": distinguished_name, "objectClass": ["inetOrgPerson"]},
        {"dn": "cn=other,ou=people,dc=example,dc=com", "objectClass": ["person"]},
    ]
    tab.refresh_btn.click()

    qtbot.waitUntil(lambda: tree.model.rowCount() == 2, timeout=1000)
    current_item = tree.model.itemFromIndex(tree.view.currentIndex())
    assert current_item.data(Qt.ItemDataRole.UserRole) == distinguished_name
    assert tree.model.item(0, 1).text() == "inetOrgPerson"
    tab.close()


@pytest.fixture
def profile():
    return DirectoryProfile(
        name="test", host="ldap.test", port=389, transport="plain",
        authentication_mode="anonymous", bind_dn="", base_dn="dc=example,dc=com",
        ca_certificate="", client_certificate="", client_key="", read_only=False,
        profile_id="00000000-0000-0000-0000-000000000001"
    )


@pytest.fixture
def lens(profile, qapp):
    service = MockLDAPService(profile)
    return DataLens(service, root_dn="dc=example,dc=com")


def test_create_entry_triggers_worker_and_succeeds(lens, qtbot):
    spy = QSignalSpy(lens.succeeded)
    lens.create_entry("cn=NewUser,dc=example,dc=com", {"objectClass": ["inetOrgPerson"]})
    qtbot.waitUntil(lambda: len(spy) > 0, timeout=1000)
    assert spy[0][0] == ("add", "cn=NewUser,dc=example,dc=com")


def test_update_entry_triggers_worker_and_succeeds(lens, qtbot):
    spy = QSignalSpy(lens.succeeded)
    lens.update_entry("cn=Existing,dc=example,dc=com", {"sn": [("replace", ["Updated"])]})
    qtbot.waitUntil(lambda: len(spy) > 0, timeout=1000)
    assert spy[0][0] == ("modify", "cn=Existing,dc=example,dc=com")


def test_delete_entry_triggers_worker_and_succeeds(lens, qtbot):
    spy = QSignalSpy(lens.succeeded)
    lens.delete_entry("cn=ToDelete,dc=example,dc=com", recursive=True)
    qtbot.waitUntil(lambda: len(spy) > 0, timeout=1000)
    assert spy[0][0] == ("delete", "cn=ToDelete,dc=example,dc=com")


def test_move_entry_triggers_worker_and_succeeds(lens, qtbot):
    spy = QSignalSpy(lens.succeeded)
    lens.move_entry("cn=MoveMe,dc=example,dc=com", "cn=NewName", new_superior="ou=Users,dc=example,dc=com")
    qtbot.waitUntil(lambda: len(spy) > 0, timeout=1000)
    assert spy[0][0] == ("move", "cn=MoveMe,dc=example,dc=com")
