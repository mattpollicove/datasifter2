"""Qt worker wrapper for blocking directory operations."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from datasifter.diagnostics import log_exception
from datasifter.remediation import translate_error


class WorkerSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    diagnostic = pyqtSignal(object)
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
                    "A submitted operation may already have completed; verify its "
                    "outcome before retrying."
                )
        except Exception as error:  # noqa: BLE001
            log_exception(error)
            diagnostic = translate_error(error)
            self.signals.diagnostic.emit(diagnostic)
            self.signals.failed.emit(diagnostic.as_text())
        finally:
            self.signals.finished.emit()
