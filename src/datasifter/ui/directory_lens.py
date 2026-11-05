"""DirectoryLens – a simple UI component for browsing and editing LDAP entries.

The component is intentionally lightweight; it is designed to be dropped into the
existing application without affecting the current test suite.  The class
provides a :class:`QTreeView` that displays the directory hierarchy and a set of
public methods that perform CRUD operations via :class:`~datasifter.directory.service.LDAPService`.

The operations are executed in a background thread using :class:`~datasifter.directory.worker.DirectoryWorker`
to keep the UI responsive.  Each operation emits a ``succeeded`` or ``failed``
signal that callers can connect to.

Only the minimal functionality required by the user story is implemented – the
component can be extended later to support richer UI features such as
attribute editing or filtering.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List

from PyQt6.QtCore import QThreadPool, QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QGuiApplication, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout

from datasifter.directory.filters import USER_ID_ATTRIBUTES, escape_filter_value
from datasifter.directory.service import LDAPService
from datasifter.directory.worker import DirectoryWorker


class DirectoryLens(QWidget):
    """A widget that displays LDAP entries and exposes CRUD operations.

    Parameters
    ----------
    service:
        An instance of :class:`~datasifter.directory.service.LDAPService` used
        to perform directory operations.
    root_dn:
        The distinguished name of the root entry to display.  The tree will
        show all entries under this DN.
    """

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service: LDAPService, root_dn: str = "dc=example,dc=com") -> None:
        super().__init__()
        self.service = service
        self.root_dn = root_dn
        self.threadpool = QThreadPool.globalInstance()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Name", "Object Class"])
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setHeaderHidden(False)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        self.setLayout(layout)
        self._populate()

    def refresh(self) -> None:
        """Reload the tree and keep the selected DN highlighted when it remains."""
        index = self.view.currentIndex()
        item = self.model.itemFromIndex(index.siblingAtColumn(0)) if index.isValid() else None
        distinguished_name = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._populate(str(distinguished_name) if distinguished_name else None)

    def _populate(self, preserve_dn: str | None = None) -> None:
        """Populate the tree from the LDAP directory."""
        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> List[Dict[str, Any]]:
            if cancelled is None:
                return self.service.search(self.root_dn, "(objectClass=*)", scope="subtree")
            return self.service.search(
                self.root_dn,
                "(objectClass=*)",
                scope="subtree",
                cancelled=cancelled,
                progress=progress,
            )

        def on_success(result: List[Dict[str, Any]]) -> None:
            self.model.removeRows(0, self.model.rowCount())
            for entry in result:
                dn = entry.get("dn", "")
                obj_classes = ", ".join(entry.get("objectClass", []))
                item = QStandardItem(dn.split(",", 1)[0])
                item.setData(dn, Qt.ItemDataRole.UserRole)
                item.setEditable(False)
                self.model.appendRow([item, QStandardItem(obj_classes)])
            if preserve_dn:
                for row in range(self.model.rowCount()):
                    item = self.model.item(row, 0)
                    if item.data(Qt.ItemDataRole.UserRole) == preserve_dn:
                        self.view.setCurrentIndex(item.index())
                        break

        self._run_operation(worker, on_success)

    def _run_operation(
        self,
        operation: Callable[[Any, Callable[[int, str], None]], Any],
        on_success: Callable[[Any], None],
    ) -> None:
        application = QGuiApplication.instance()
        offscreen = os.getenv("QT_QPA_PLATFORM", "").casefold() == "offscreen"
        if application is not None:
            offscreen = offscreen or QGuiApplication.platformName().casefold() == "offscreen"
        if offscreen:
            try:
                result = operation(None, lambda *_args: None)
            except Exception as error:  # noqa: BLE001
                self.failed.emit(str(error))
                return
            on_success(result)
            return

        worker_obj = DirectoryWorker(operation)
        worker_obj.signals.succeeded.connect(on_success)
        worker_obj.signals.failed.connect(self.failed.emit)
        self.threadpool.start(worker_obj)

    # ---------------------------------------------------------------------
    # CRUD operations
    # ---------------------------------------------------------------------
    def create_entry(self, dn: str, attributes: Dict[str, List[Any]]) -> None:
        """Create a new LDAP entry."""
        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            if cancelled is None:
                self.service.add_entry(dn, attributes)
            else:
                self.service.add_entry(dn, attributes, cancelled=cancelled)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("add", dn))
            self._populate()

        self._run_operation(worker, on_success)

    def add_attribute_to_user(self, identifier: str, attribute: str, value: str) -> None:
        """Resolve one user by identifier and add an attribute value to that entry."""
        self._modify_user_attribute(identifier, attribute, value, "add")

    def replace_user_attribute(self, identifier: str, attribute: str, value: str) -> None:
        """Resolve one user by identifier and replace an attribute value."""
        self._modify_user_attribute(identifier, attribute, value, "replace")

    def _modify_user_attribute(
        self, identifier: str, attribute: str, value: str, operation: str
    ) -> None:
        # Resolve the user using only universally supported identifiers.
        # ``sAMAccountName`` is specific to Active Directory and may not be
        # present in other LDAP implementations.  To keep the command
        # functional across environments we restrict the search to ``uid``
        # (the canonical LDAP user ID) and ``cn`` (common name).  The order
        # ``uid`` first ensures that the most unique attribute is used when
        # available.
        escaped_identifier = escape_filter_value(identifier)
        preferred_attrs = ["uid", "cn"]
        identity_filter = "(|" + "".join(
            f"({name}={escaped_identifier})" for name in preferred_attrs
        ) + ")"
        search_filter = f"(&(objectClass=person){identity_filter})"

        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> str:
            matches = self.service.search(
                self.root_dn,
                search_filter,
                attributes=["1.1"],
                scope="subtree",
                cancelled=cancelled,
                progress=progress,
            )
            if not matches:
                raise ValueError(f"No user matched identifier '{identifier}'.")
            if len(matches) > 1:
                distinguished_names = ", ".join(
                    str(entry.get("dn", "(unknown DN)")) for entry in matches[:5]
                )
                raise ValueError(
                    f"Multiple users matched '{identifier}'. Specify a unique identifier or DN. "
                    f"Matches: {distinguished_names}"
                )
            distinguished_name = str(matches[0]["dn"])
            self.service.modify_entry(
                distinguished_name,
                {attribute: [(operation, [value])]},
                cancelled=cancelled,
            )

            return distinguished_name

        def on_success(distinguished_name: str) -> None:
            self.succeeded.emit(("modify", distinguished_name))
            self._populate()

        self._run_operation(worker, on_success)

    def delete_entry(self, dn: str, recursive: bool = False) -> None:
        """Delete an LDAP entry.

        Parameters
        ----------
        dn:
            Distinguished name of the entry to delete.
        recursive:
            If ``True`` all descendant entries are deleted as well.
        """
        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.delete_entry(dn, recursive=recursive, cancelled=cancelled)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("delete", dn))
            self._populate()

        self._run_operation(worker, on_success)

    def update_entry(self, dn: str, changes: Dict[str, List[tuple[str, Any]]]) -> None:
        """Update an existing LDAP entry.

        Parameters
        ----------
        dn:
            Distinguished name of the entry to update.
        changes:
            Mapping of attribute names to a list of ``(operation, values)``
            tuples where ``operation`` is one of ``"add"``, ``"delete"`` or
            ``"replace"``.
        """
        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.modify_entry(dn, changes, cancelled=cancelled)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("modify", dn))
            self._populate()

        self._run_operation(worker, on_success)

    def move_entry(self, dn: str, relative_dn: str, new_superior: str | None = None) -> None:
        """Move an LDAP entry."""
        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.move_entry(
                dn,
                relative_dn,
                new_superior=new_superior,
                cancelled=cancelled,
            )

        def on_success(_: Any) -> None:
            self.succeeded.emit(("move", dn))
            self._populate()

        self._run_operation(worker, on_success)
