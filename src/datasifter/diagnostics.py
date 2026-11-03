"""Persistent, bounded application diagnostics without exception payloads."""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from datasifter.config import configuration_directory

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
logger = logging.getLogger("datasifter.diagnostics")


def log_exception(error: BaseException, traceback_obj: TracebackType | None = None) -> None:
    """Record exception types and frames, not values, source lines, or locals."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frames = "\n".join(
            f"  {Path(frame.f_code.co_filename).name}:{line} in {frame.f_code.co_name}"
            for frame, line in traceback.walk_tb(traceback_obj or current.__traceback__)
        )
        logger.error("Exception %s\n%s", type(current).__name__, frames)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
        traceback_obj = None


class DiagnosticLog:
    def __init__(self, directory: Path | None = None) -> None:
        directory = directory if directory is not None else configuration_directory() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "datasifter.log"
        self.handler = RotatingFileHandler(
            self.path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        self._loggers = [
            logging.getLogger(name)
            for name in ("datasifter.protocol", "datasifter.diagnostics", "datasifter.lens")
        ]
        self._levels = [item.level for item in self._loggers]
        for item in self._loggers:
            item.setLevel(logging.INFO)
            item.addHandler(self.handler)
        self._sys_hook = sys.excepthook
        self._thread_hook = threading.excepthook
        self._installed_sys_hook: (
            Callable[[type[BaseException], BaseException, TracebackType | None], None] | None
        ) = None
        self._installed_thread_hook: Callable[[threading.ExceptHookArgs], None] | None = None

    def install_exception_hooks(self, notify: Callable[[], None]) -> None:
        def sys_hook(
            error_type: type[BaseException],
            error: BaseException,
            traceback_obj: TracebackType | None,
        ) -> None:
            log_exception(error, traceback_obj)
            self._sys_hook(error_type, error, traceback_obj)
            if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                notify()

        def thread_hook(args: threading.ExceptHookArgs) -> None:
            log_exception(args.exc_value, args.exc_traceback)
            self._thread_hook(args)

        self._installed_sys_hook = sys_hook
        self._installed_thread_hook = thread_hook
        sys.excepthook = sys_hook
        threading.excepthook = thread_hook

    def close(self) -> None:
        if self._installed_sys_hook is not None and sys.excepthook is self._installed_sys_hook:
            sys.excepthook = self._sys_hook
        if (
            self._installed_thread_hook is not None
            and threading.excepthook is self._installed_thread_hook
        ):
            threading.excepthook = self._thread_hook
        for item, level in zip(self._loggers, self._levels):
            item.removeHandler(self.handler)
            item.setLevel(level)
        self.handler.close()
