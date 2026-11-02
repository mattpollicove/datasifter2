"""Qt worker wrapper for blocking directory operations."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


class WorkerSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    finished = pyqtSignal()


class DirectoryWorker(QRunnable):
    def __init__(
        self,
        operation: Callable[[Event, Callable[[int, str], None]], Any],
    ) -> None:
        super().__init__()
        self.operation = operation
        self.cancelled = Event()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            if self.cancelled.is_set():
                self.signals.failed.emit("Operation cancelled before it started.")
                return
            result = self.operation(self.cancelled, self.signals.progress.emit)
            if not self.cancelled.is_set():
                self.signals.succeeded.emit(result)
            else:
                self.signals.failed.emit(
                    "Cancellation was requested after the operation returned. "
                    "A submitted directory change may already have completed; verify "
                    "the directory before retrying."
                )
        except Exception as error:  # noqa: BLE001
            self.signals.failed.emit(str(error))
        finally:
            self.signals.finished.emit()
