"""Directory Lens tab – a lightweight UI that exposes CRUD operations via :class:`DirectoryLens`.

The tab contains:

* A :class:`DirectoryLens` widget that displays the LDAP tree.
* A set of CRUD buttons that open simple dialogs to collect the required
  parameters and then invoke the corresponding :class:`DirectoryLens`
  methods.
* A log widget that shows the result of each operation.

This module is intentionally minimal – it is meant to demonstrate that the
``DirectoryLens`` component can be used programmatically.  The dialogs use
``QInputDialog`` for simplicity; in a full application you would replace these
with richer forms.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from datasifter.diagnostics import log_exception
from datasifter.directory.service import LDAPService
from datasifter.ui.directory_lens import DirectoryLens


class DirectoryLensTab(QWidget):
    """A tab that hosts :class:`DirectoryLens` and CRUD controls.

    Parameters
    ----------
    service:
        The :class:`LDAPService` instance used by the lens.
    """

    def __init__(self, service: LDAPService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service: LDAPService | None = None
        self._lens: DirectoryLens | None = None
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.connection_status = QLabel("Connect to a directory to use Directory Lens.")

        # CRUD buttons
        self.create_btn = QPushButton("Create")
        self.update_btn = QPushButton("Update")
        self.delete_btn = QPushButton("Delete")
        self.move_btn = QPushButton("Move")
        self.create_btn.clicked.connect(self._create_entry)
        self.update_btn.clicked.connect(self._update_entry)
        self.delete_btn.clicked.connect(self._delete_entry)
        self.move_btn.clicked.connect(self._move_entry)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.create_btn)
        btn_layout.addWidget(self.update_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.move_btn)

        # Main layout: lens, CRUD button bar, command input, log
        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self.connection_status)
        self._layout.addLayout(btn_layout)
        self._layout.addWidget(self.log)
        # Natural‑language command input
        cmd_layout = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Enter natural‑language command…")
        self.cmd_execute = QPushButton("Execute")
        self.cmd_execute.clicked.connect(self._execute_command)
        cmd_layout.addWidget(QLabel("Command:"))
        cmd_layout.addWidget(self.cmd_input)
        cmd_layout.addWidget(self.cmd_execute)
        self._layout.addLayout(cmd_layout)
        self.set_service(service)

    @property
    def lens(self) -> DirectoryLens:
        if self._service is None:
            raise RuntimeError("Connect to a directory before using Directory Lens.")
        if self._lens is None:
            self._lens = DirectoryLens(self._service, root_dn=self._service.profile.base_dn)
            self._lens.succeeded.connect(self._on_success)
            self._lens.failed.connect(self._on_failure)
            self._layout.insertWidget(0, self._lens)
            self.connection_status.hide()
        return self._lens

    def set_service(self, service: LDAPService | None) -> None:
        if self._lens is not None:
            self._layout.removeWidget(self._lens)
            self._lens.deleteLater()
            self._lens = None
        self._service = service
        self.connection_status.show()
        for button in (self.create_btn, self.update_btn, self.delete_btn, self.move_btn):
            button.setEnabled(service is not None and not service.profile.read_only)
        self.cmd_execute.setEnabled(service is not None and not service.profile.read_only)
        self.cmd_input.setEnabled(service is not None and not service.profile.read_only)
        if service is not None and self.isVisible():
            self.lens.view.show()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._service is not None:
            self.lens.view.show()

    # ---------------------------------------------------------------------
    # Signal handlers
    # ---------------------------------------------------------------------
    def _on_success(self, info: Any) -> None:
        self.log.appendPlainText(f"Success: {info}")

    def _on_failure(self, error: str) -> None:
        self.log.appendPlainText(f"Error: {error}")

    # ---------------------------------------------------------------------
    # CRUD dialog helpers
    # ---------------------------------------------------------------------
    def _create_entry(self) -> None:
        dn, ok = QInputDialog.getText(self, "Create Entry", "Distinguished name:")
        if not ok or not dn:
            return
        attrs_text, ok = QInputDialog.getMultiLineText(
            self, "Create Entry", "Attributes (JSON dict):"
        )
        if not ok:
            return
        try:
            attributes: Dict[str, List[Any]] = json.loads(attrs_text)
        except Exception as exc:  # pragma: no cover - defensive
            log_exception(exc)
            self.log.appendPlainText(f"Invalid attributes JSON: {exc}")
            return
        self.lens.create_entry(dn, attributes)

    def _update_entry(self) -> None:
        dn, ok = QInputDialog.getText(self, "Update Entry", "Distinguished name:")
        if not ok or not dn:
            return
        changes_text, ok = QInputDialog.getMultiLineText(
            self, "Update Entry", "Changes (JSON dict of attr->[op, values]):"
        )
        if not ok:
            return
        try:
            changes: Dict[str, List[tuple[str, Any]]] = json.loads(changes_text)
        except Exception as exc:  # pragma: no cover - defensive
            log_exception(exc)
            self.log.appendPlainText(f"Invalid changes JSON: {exc}")
            return
        self.lens.update_entry(dn, changes)

    def _delete_entry(self) -> None:
        dn, ok = QInputDialog.getText(self, "Delete Entry", "Distinguished name:")
        if not ok or not dn:
            return
        recursive, ok = QInputDialog.getItem(
            self,
            "Delete Entry",
            "Recursive?",
            ["No", "Yes"],
            0,
            False,
        )
        if not ok:
            return
        self.lens.delete_entry(dn, recursive=recursive == "Yes")

    def _move_entry(self) -> None:
        dn, ok = QInputDialog.getText(self, "Move Entry", "Distinguished name:")
        if not ok or not dn:
            return
        relative_dn, ok = QInputDialog.getText(self, "Move Entry", "New relative DN (e.g., cn=NewName):")
        if not ok:
            return
        new_superior, ok = QInputDialog.getText(
            self, "Move Entry", "New superior DN (leave blank to stay in same subtree):"
        )
        if not ok:
            return
        self.lens.move_entry(dn, relative_dn, new_superior or None)

    # ---------------------------------------------------------------------
    # Natural‑language command handling
    # ---------------------------------------------------------------------
    def _execute_command(self) -> None:
        text = self.cmd_input.text().strip()
        if not text:
            return
        try:
            self._parse_and_execute(text)
        except Exception as exc:  # pragma: no cover - defensive
            log_exception(exc)
            self.log.appendPlainText(f"Command error: {exc}")

    def _parse_and_execute(self, text: str) -> None:
        """Parse a simple natural‑language command and invoke the appropriate CRUD method.

        Supported verbs: create, update, delete, move.
        The command format is intentionally simple and uses JSON for attribute
        specifications.
        """
        import json
        import re

        patterns = {
            "create": r"^create entry (?P<dn>[^ ]+) with attributes (?P<attrs>.+)$",
            "update": r"^update entry (?P<dn>[^ ]+) with changes (?P<changes>.+)$",
            "delete": r"^delete entry (?P<dn>[^ ]+)$",
            "move": r"^move entry (?P<dn>[^ ]+) to (?P<relative_dn>[^ ]+)( under (?P<new_superior>.+))?$",
        }
        for verb, pattern in patterns.items():
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                if verb == "create":
                    dn = match.group("dn")
                    attrs = json.loads(match.group("attrs"))
                    self.lens.create_entry(dn, attrs)
                elif verb == "update":
                    dn = match.group("dn")
                    changes = json.loads(match.group("changes"))
                    self.lens.update_entry(dn, changes)
                elif verb == "delete":
                    dn = match.group("dn")
                    self.lens.delete_entry(dn)
                elif verb == "move":
                    dn = match.group("dn")
                    relative_dn = match.group("relative_dn")
                    new_superior = match.group("new_superior")
                    self.lens.move_entry(dn, relative_dn, new_superior)
                return
        raise ValueError("Unrecognized command format")
