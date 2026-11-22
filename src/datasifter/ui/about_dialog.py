"""About, privacy, and licensing information for the desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from datasifter import __version__

GITHUB_ISSUES_URL = "https://github.com/mattpollicove/datasifter2/issues"


def _read_application_document(name: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    path = root / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise OSError(f"Could not read application document {path}: {error}") from error


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About DataSifter")
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        about_page = QWidget(self.tabs)
        about_layout = QVBoxLayout(about_page)
        about_layout.addWidget(
            QLabel(
                f"<h2>DataSifter 2.0</h2><p>Version {__version__}</p>"
                "<p>Local-first LDAP desktop client. Alpha software; not production-ready.</p>",
                about_page,
            )
        )
        self.support_link = QLabel(
            f'<a href="{GITHUB_ISSUES_URL}">Get support via GitHub Issues</a>'
            "<br>Redact credentials and directory data before posting.",
            about_page,
        )
        self.support_link.setOpenExternalLinks(True)
        about_layout.addWidget(self.support_link)
        about_layout.addStretch()
        self.tabs.addTab(about_page, "About")

        self.privacy_text = QTextBrowser(self.tabs)
        self.privacy_text.setReadOnly(True)
        self.privacy_text.setOpenExternalLinks(True)
        self.privacy_text.setMarkdown(_read_application_document("PRIVACY.md"))
        self.tabs.addTab(self.privacy_text, "Privacy")

        self.license_text = QPlainTextEdit(self.tabs)
        self.license_text.setReadOnly(True)
        self.license_text.setPlainText(_read_application_document("LICENSE"))
        self.tabs.addTab(self.license_text, "MIT License")

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
