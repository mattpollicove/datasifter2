import logging
import sys
import threading

import pytest

from datasifter import __main__, diagnostics
from datasifter.diagnostics import DiagnosticLog, log_exception
from datasifter.directory.worker import DirectoryWorker


def test_logs_append_across_sessions_and_exclude_unrelated_loggers(tmp_path):
    for message in ("first session", "second session"):
        session = DiagnosticLog(tmp_path)
        try:
            logging.getLogger("datasifter.protocol").info(message)
            logging.getLogger("ldap3").warning("unfiltered third-party payload")
        finally:
            session.close()
    text = (tmp_path / "datasifter.log").read_text(encoding="utf-8")
    assert "first session" in text
    assert "second session" in text
    assert "unfiltered third-party payload" not in text


def test_logs_rotate_at_configured_limit_and_bound_backups(tmp_path, monkeypatch):
    assert diagnostics.LOG_MAX_BYTES == 5 * 1024 * 1024
    monkeypatch.setattr(diagnostics, "LOG_MAX_BYTES", 300)
    session = DiagnosticLog(tmp_path)
    try:
        for index in range(100):
            logging.getLogger("datasifter.protocol").info("operation %03d %s", index, "x" * 40)
    finally:
        session.close()
    files = list(tmp_path.glob("datasifter.log*"))
    assert len(files) == diagnostics.LOG_BACKUP_COUNT + 1
    assert all(path.stat().st_size <= 300 for path in files)
    assert "operation 099" in (tmp_path / "datasifter.log").read_text(encoding="utf-8")
    assert diagnostics.LOG_BACKUP_COUNT == 3


def test_exception_frames_and_chains_exclude_messages_and_locals(tmp_path):
    session = DiagnosticLog(tmp_path)
    try:
        try:
            try:
                raise ValueError("secret-inner-value")
            except ValueError as error:
                raise RuntimeError("secret-outer-value") from error
        except RuntimeError as error:
            log_exception(error)
    finally:
        session.close()
    text = session.path.read_text(encoding="utf-8")
    assert "RuntimeError" in text
    assert "ValueError" in text
    assert "test_diagnostics.py:" in text
    assert "test_exception_frames_and_chains" in text
    assert "secret-inner-value" not in text
    assert "secret-outer-value" not in text
    assert "raise RuntimeError" not in text


def test_uncaught_hooks_log_and_restore_previous_hooks(tmp_path, monkeypatch):
    sys_calls = []
    thread_calls = []
    notices = []
    previous_sys = lambda *args: sys_calls.append(args)
    previous_thread = lambda args: thread_calls.append(args)
    monkeypatch.setattr(sys, "excepthook", previous_sys)
    monkeypatch.setattr(threading, "excepthook", previous_thread)
    session = DiagnosticLog(tmp_path)
    session.install_exception_hooks(lambda: notices.append(True))
    try:
        try:
            raise RuntimeError("private payload")
        except RuntimeError as error:
            sys.excepthook(type(error), error, error.__traceback__)
            threading.excepthook(
                threading.ExceptHookArgs((type(error), error, error.__traceback__, None))
            )
    finally:
        session.close()
    assert sys.excepthook is previous_sys
    assert threading.excepthook is previous_thread
    assert len(sys_calls) == len(thread_calls) == 1
    assert notices == [True]
    text = session.path.read_text(encoding="utf-8")
    assert text.count("Exception RuntimeError") == 2
    assert "private payload" not in text


def test_worker_failure_is_persisted_without_changing_failure_signal(tmp_path):
    def fail(_cancelled, _progress):
        raise ValueError("private worker payload")

    session = DiagnosticLog(tmp_path)
    failures = []
    worker = DirectoryWorker(fail)
    worker.signals.failed.connect(failures.append)
    try:
        worker.run()
    finally:
        session.close()
    assert failures == ["private worker payload"]
    text = session.path.read_text(encoding="utf-8")
    assert "ValueError" in text
    assert "in fail" in text
    assert "private worker payload" not in text


def test_logging_directory_failure_is_explicit(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        DiagnosticLog(blocked)


def test_default_log_location_uses_configuration_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "configuration_directory", lambda: tmp_path)
    session = DiagnosticLog()
    try:
        assert session.path == tmp_path / "logs" / "datasifter.log"
    finally:
        session.close()


def test_entry_point_reports_logging_failure(qapp, monkeypatch):
    def fail():
        raise PermissionError("Cannot write log")

    messages = []
    monkeypatch.setattr(__main__, "QApplication", lambda _args: qapp)
    monkeypatch.setattr(__main__, "DiagnosticLog", fail)
    monkeypatch.setattr(__main__.QMessageBox, "critical", lambda *args: messages.append(args))
    monkeypatch.setattr(
        __main__, "MainWindow", lambda: pytest.fail("Must not start without logging")
    )
    assert __main__.main() == 1
    assert messages[0][1] == "Logging unavailable"


def test_entry_point_logs_startup_exception_and_closes_log(qapp, tmp_path, monkeypatch):
    session = DiagnosticLog(tmp_path)
    previous_hook = sys.excepthook
    messages = []

    def fail():
        raise ValueError("private startup payload")

    monkeypatch.setattr(__main__, "DiagnosticLog", lambda: session)
    monkeypatch.setattr(__main__, "QApplication", lambda _args: qapp)
    monkeypatch.setattr(__main__, "MainWindow", fail)
    monkeypatch.setattr(__main__.QMessageBox, "critical", lambda *args: messages.append(args))
    assert __main__.main() == 1
    assert sys.excepthook is previous_hook
    assert messages[0][1] == "Startup failed"
    text = session.path.read_text(encoding="utf-8")
    assert "Application started" in text
    assert "Exception ValueError" in text
    assert "Application stopped" in text
    assert "private startup payload" not in text


def test_nlp_diagnostics_persist_only_outcomes(qtbot, tmp_path):
    from datasifter.config import ConfigurationStore
    from datasifter.ui.main_window import MainWindow

    session = DiagnosticLog(tmp_path / "logs")
    window = MainWindow(store=ConfigurationStore(tmp_path / "profiles.json"))
    qtbot.addWidget(window)
    try:
        window._log_ai_prompt(
            {
                "time": "test",
                "model": "private-model",
                "request": "private-request",
                "prompt": "private-prompt",
                "response": "private-response",
            },
            result="private-filter",
        )
        assert "private-request" in window.ai_prompt_log.toPlainText()
    finally:
        window.close()
        session.close()
    text = session.path.read_text(encoding="utf-8")
    assert "outcome=completed used_model=True" in text
    assert "private-" not in text


def test_invalid_crud_command_logs_failure_without_request(qtbot, tmp_path):
    from datasifter.ui.directory_lens_tab import DirectoryLensTab

    session = DiagnosticLog(tmp_path)
    tab = DirectoryLensTab()
    qtbot.addWidget(tab)
    try:
        tab.cmd_input.setText("unrecognized command with private-password")
        tab._execute_command()
        assert "Command error" in tab.log.toPlainText()
    finally:
        tab.close()
        session.close()
    text = session.path.read_text(encoding="utf-8")
    assert "Exception ValueError" in text
    assert "in _parse_and_execute" in text
    assert "private-password" not in text
