"""Grouped application preferences; model selection retains its separate approval flow."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from datasifter.config import Preferences


class SettingsDialog(QDialog):
    save_requested = pyqtSignal()
    choose_model_requested = pyqtSignal()
    manage_models_requested = pyqtSignal()
    load_model_requested = pyqtSignal()

    def __init__(self, preferences: Preferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DataSifter settings")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        lens = QWidget()
        lens_layout = QVBoxLayout(lens)
        self.ai_enabled = QCheckBox("Enable Directory Lens (local AI)")
        self.ai_enabled.setChecked(preferences.ai_enabled)
        lens_layout.addWidget(self.ai_enabled)
        explanation = QLabel(
            "Directory Lens translates descriptions into LDAP filters locally. "
            "Only GGUF model files through llama.cpp are supported. "
            "Disabling it hides the workspace panel and releases the loaded model. "
            "Your approved model file is retained."
        )
        explanation.setWordWrap(True)
        lens_layout.addWidget(explanation)
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        self.model_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lens_layout.addWidget(self.model_status)
        model_buttons = QHBoxLayout()
        self.choose_model = QPushButton("Choose GGUF file...")
        self.manage_models = QPushButton("Download / select models...")
        self.load_model = QPushButton("Load approved model")
        self.choose_model.clicked.connect(self.choose_model_requested)
        self.manage_models.clicked.connect(self.manage_models_requested)
        self.load_model.clicked.connect(self.load_model_requested)
        for button in (self.choose_model, self.manage_models, self.load_model):
            model_buttons.addWidget(button)
        lens_layout.addLayout(model_buttons)
        notice = QLabel(
            "Model selection and source approval are saved separately when confirmed, "
            "even if you cancel this settings screen. Enable and save Directory Lens "
            "before loading a model."
        )
        notice.setWordWrap(True)
        lens_layout.addWidget(notice)
        lens_layout.addStretch()
        self.tabs.addTab(lens, "Directory Lens")

        safety = QWidget()
        safety_layout = QVBoxLayout(safety)
        self.ai_confirmation = QCheckBox("Review Directory Lens filters before execution")
        self.ai_confirmation.setChecked(preferences.require_ai_confirmation)
        self.delete_confirmation = QCheckBox("Confirm before deleting entries")
        self.delete_confirmation.setChecked(preferences.require_delete_confirmation)
        self.rename_confirmation = QCheckBox("Confirm before renaming or moving entries")
        self.rename_confirmation.setChecked(preferences.require_rename_confirmation)
        for checkbox in (
            self.ai_confirmation, self.delete_confirmation, self.rename_confirmation
        ):
            safety_layout.addWidget(checkbox)
        safety_notice = QLabel(
            "Directory Lens session confirmation overrides remain available in the Settings "
            "menu. Filter validation and model integrity checks always remain active."
        )
        safety_notice.setWordWrap(True)
        safety_layout.addWidget(safety_notice)
        safety_layout.addStretch()
        self.tabs.addTab(safety, "Safety")

        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        self.highlight_search = QCheckBox("Highlight attributes used in the search filter")
        self.highlight_search.setChecked(preferences.highlight_search_attributes)
        appearance_layout.addWidget(self.highlight_search)
        appearance_layout.addWidget(
            QLabel("Operational attributes are italicized and read-only.")
        )
        appearance_layout.addStretch()
        self.tabs.addTab(appearance, "Appearance")

        self.busy_status = QLabel()
        layout.addWidget(self.busy_status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save_requested)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def set_busy(self, busy: bool, ai_enabled: bool, approved: bool) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not busy)
        self.choose_model.setEnabled(not busy)
        self.manage_models.setEnabled(not busy)
        self.load_model.setEnabled(not busy and ai_enabled and approved)
        self.busy_status.setText("Wait for active operations to finish before saving." if busy else "")
