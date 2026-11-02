"""Application entry point."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from datasifter import __version__
from datasifter.config import ConfigurationError
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
        window = MainWindow()
    except ConfigurationError as error:
        QMessageBox.critical(None, "Configuration error", str(error))
        return 1
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
