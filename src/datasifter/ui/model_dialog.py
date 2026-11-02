"""Explicit model downloads and local GGUF selection."""

from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from datasifter.ai.model_catalog import MODEL_CATALOG, download_model
from datasifter.directory.worker import DirectoryWorker


class ModelDialog(QDialog):
    model_selected = pyqtSignal(str, bool)

    def __init__(self, directory: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.directory = directory
        self.worker: DirectoryWorker | None = None
        self.setWindowTitle("GGUF model manager")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Downloads contact Hugging Face and its HTTPS file hosts only on request.\n"
            "No directory data or credentials are sent. Inference stays local.\n"
            "Verify publisher, license, and organizational approval before downloading."
        ))
        self.catalog = QComboBox()
        for index, model in enumerate(MODEL_CATALOG):
            self.catalog.addItem(f"{model.name} ({model.size / 1024**3:.2f} GiB)")
            self.catalog.setItemData(index, model.description, Qt.ItemDataRole.ToolTipRole)
        layout.addWidget(self.catalog)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.details.setOpenExternalLinks(True)
        layout.addWidget(self.details)
        self.download = QPushButton("Download selected model...")
        self.local = QPushButton("Select local GGUF file...")
        self.cancel_download = QPushButton("Cancel download")
        self.cancel_download.setEnabled(False)
        self.load_after = QCheckBox("Load model into memory after download or selection")
        self.load_after.setChecked(True)
        self.load_after.setToolTip(
            "After you approve the model's source, load it so the first AI request starts "
            "immediately. Leave unchecked to load on first use."
        )
        self.status = QLabel("Choose a catalog model or an existing local GGUF file.")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        for widget in (
            self.download, self.local, self.cancel_download, self.load_after,
            self.progress, self.status,
        ):
            layout.addWidget(widget)
        self.catalog.currentIndexChanged.connect(self._details)
        self.download.clicked.connect(self._download)
        self.local.clicked.connect(self._local)
        self.cancel_download.clicked.connect(self._cancel)
        self._details()

    def _details(self) -> None:
        model = MODEL_CATALOG[self.catalog.currentIndex()]
        quantized = (
            f"<br>GGUF quantized by: {escape(model.quantized_by)}" if model.quantized_by else ""
        )
        self.details.setText(
            f"<p>{escape(model.description)}</p>"
            f"<p><b>Publisher:</b> {escape(model.publisher)} | <b>License:</b> "
            f"{escape(model.license)} | <b>Size:</b> {model.size / 1024**3:.2f} GiB{quantized}"
            f"<br><b>SHA-256:</b> {model.sha256}</p>"
            f'<p><a href="{escape(model.model_card, quote=True)}">Model card and license</a>'
            f' &nbsp;|&nbsp; <a href="{escape(model.source, quote=True)}">'
            "Pinned GGUF download repository</a></p>"
        )

    def _local(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select GGUF model", str(self.directory), "GGUF (*.gguf)")
        if path:
            self.accept()
            self.model_selected.emit(path, self.load_after.isChecked())

    def _download(self) -> None:
        model = MODEL_CATALOG[self.catalog.currentIndex()]
        if QMessageBox.question(
            self, "Confirm model download",
            f"Download {model.name} ({model.size / 1024**3:.2f} GiB)?\n"
            f"Publisher: {model.publisher}\nLicense: {model.license}\n"
            f"GGUF quantized by: {model.quantized_by or model.publisher}\n"
            "Review the publisher's license and approve this source before continuing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        directory = QFileDialog.getExistingDirectory(self, "Model download folder", str(self.directory.parent))
        if not directory:
            return
        worker = DirectoryWorker(
            lambda cancelled, progress: download_model(model, Path(directory), cancelled, progress)
        )
        self.worker = worker
        worker.signals.progress.connect(self._progress)
        worker.signals.failed.connect(self._failed)
        worker.signals.succeeded.connect(self._downloaded)
        worker.signals.finished.connect(self._finished)
        self.catalog.setEnabled(False)
        self.download.setEnabled(False)
        self.local.setEnabled(False)
        self.cancel_download.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("Connecting to model download host...")
        QThreadPool.globalInstance().start(worker)

    def _progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.status.setText(message)

    def _failed(self, message: str) -> None:
        self.status.setText(message)
        if self.worker is not None and self.worker.cancelled.is_set():
            return
        QMessageBox.warning(self, "Model download failed", message)

    def _downloaded(self, path: str) -> None:
        self.status.setText("Download verified. Model approval is required before inference.")
        self.accept()
        self.model_selected.emit(path, self.load_after.isChecked())

    def _finished(self) -> None:
        self.worker = None
        self.catalog.setEnabled(True)
        self.download.setEnabled(True)
        self.local.setEnabled(True)
        self.cancel_download.setEnabled(False)

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_download.setEnabled(False)
            self.status.setText("Cancellation requested; waiting for the current read (10s timeout).")

    def reject(self) -> None:
        if self.worker:
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker:
            self._cancel()
            event.ignore()
            return
        super().closeEvent(event)
