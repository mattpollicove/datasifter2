"""Application entry point."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from datasifter import __version__
from datasifter.config import ConfigurationError
from datasifter.diagnostics import DiagnosticLog, log_exception
from datasifter.ui.main_window import MainWindow


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    application = QApplication(sys.argv)
    application.setApplicationName("DataSifter 2.0")
    application.setApplicationVersion(__version__)
    try:
        diagnostics = DiagnosticLog()
    except (OSError, ConfigurationError) as error:
        QMessageBox.critical(None, "Logging unavailable", f"Cannot create diagnostic logs: {error}")
        return 1
    diagnostics.install_exception_hooks(
        lambda: QMessageBox.critical(
            None,
            "Unexpected application error",
            f"An unexpected error occurred. Diagnostics were written to:\n{diagnostics.path}\n\n"
            "Exception messages are omitted from the file to protect credentials. "
            "The terminal may contain additional details.",
        )
    )
    logging.getLogger("datasifter.diagnostics").info("Application started version=%s", __version__)
    try:
        try:
            window = MainWindow()
        except ConfigurationError as error:
            log_exception(error)
            QMessageBox.critical(None, "Configuration error", str(error))
            return 1
        except Exception as error:  # noqa: BLE001
            log_exception(error)
            QMessageBox.critical(
                None, "Startup failed", f"Application startup failed. See:\n{diagnostics.path}"
            )
            return 1
        window.show()
        return application.exec()
    finally:
        logging.getLogger("datasifter.diagnostics").info("Application stopped")
        diagnostics.close()


if __name__ == "__main__":
    raise SystemExit(main())
