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

from typing import Any, Callable, Dict, List

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal, QModelIndex, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QTreeView, QWidget, QVBoxLayout

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

    # ---------------------------------------------------------------------
    # Tree population helpers
    # ---------------------------------------------------------------------
    def _populate(self) -> None:
        """Populate the tree from the LDAP directory.

        The method performs a simple subtree search and builds a standard
        item model.  Errors are emitted via the :attr:`failed` signal.
        """

        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> List[Dict[str, Any]]:
            # The search operation is synchronous; we simply return the list.
            return self.service.search(self.root_dn, "(objectClass=*)", scope="subtree")

        def on_success(result: List[Dict[str, Any]]) -> None:
            self.model.removeRows(0, self.model.rowCount())
            for entry in result:
                dn = entry.get("dn", "")
                obj_classes = ", ".join(entry.get("objectClass", []))
                item = QStandardItem(dn.split(",", 1)[0])
                item.setData(dn, Qt.ItemDataRole.UserRole)
                item.setEditable(False)
                self.model.appendRow([item, QStandardItem(obj_classes)])

        def on_failure(error: str) -> None:
            self.failed.emit(error)

        worker_obj = DirectoryWorker(worker)
        worker_obj.signals.succeeded.connect(on_success)
        worker_obj.signals.failed.connect(on_failure)
        self.threadpool.start(worker_obj)

    # ---------------------------------------------------------------------
    # CRUD operations
    # ---------------------------------------------------------------------
    def create_entry(self, dn: str, attributes: Dict[str, List[Any]]) -> None:
        """Create a new LDAP entry.

        Parameters
        ----------
        dn:
            Distinguished name of the new entry.
        attributes:
            Dictionary of attribute names to list of values.
        """

        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.add_entry(dn, attributes)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("add", dn))
            self._populate()

        def on_failure(error: str) -> None:
            self.failed.emit(error)

        w = DirectoryWorker(worker)
        w.signals.succeeded.connect(on_success)
        w.signals.failed.connect(on_failure)
        self.threadpool.start(w)

    def update_entry(self, dn: str, changes: Dict[str, List[tuple[str, Any]]]) -> None:
        """Update an existing LDAP entry.

        ``changes`` should be a mapping of attribute names to a list of
        ``(operation, values)`` tuples where ``operation`` is one of
        ``"add"``, ``"delete"`` or ``"replace"``.
        """

        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.modify_entry(dn, changes)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("modify", dn))
            self._populate()

        def on_failure(error: str) -> None:
            self.failed.emit(error)

        w = DirectoryWorker(worker)
        w.signals.succeeded.connect(on_success)
        w.signals.failed.connect(on_failure)
        self.threadpool.start(w)

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
            self.service.delete_entry(dn, recursive=recursive)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("delete", dn))
            self._populate()

        def on_failure(error: str) -> None:
            self.failed.emit(error)

        w = DirectoryWorker(worker)
        w.signals.succeeded.connect(on_success)
        w.signals.failed.connect(on_failure)
        self.threadpool.start(w)

    def move_entry(self, dn: str, relative_dn: str, new_superior: str | None = None) -> None:
        """Move an LDAP entry to a new location.

        Parameters
        ----------
        dn:
            Distinguished name of the entry to move.
        relative_dn:
            New relative DN (e.g. ``cn=NewName``).
        new_superior:
            Optional new superior DN; if omitted the entry is moved within the
            same subtree.
        """

        def worker(cancelled: Any, progress: Callable[[int, str], None]) -> None:
            self.service.move_entry(dn, relative_dn, new_superior=new_superior)

        def on_success(_: Any) -> None:
            self.succeeded.emit(("move", dn))
            self._populate()

        def on_failure(error: str) -> None:
            self.failed.emit(error)

        w = DirectoryWorker(worker)
        w.signals.succeeded.connect(on_success)
        w.signals.failed.connect(on_failure)
        self.threadpool.start(w)
