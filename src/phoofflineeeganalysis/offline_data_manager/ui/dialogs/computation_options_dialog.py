"""Computation Options panel opened on Alt+LMB clicking Compute for a selected .xdf session."""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class ComputationOptionsDialog(QDialog):
    """Edit current EEG computation options, with Save Options vs Save & Compute actions."""

    def __init__(self, options: Dict[str, Any], *, target_record: Optional[Dict[str, Any]] = None, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Computation Options")
        self.setStyleSheet(DesignTokens.app_stylesheet())
        self.resize(500, 340)

        self._target_record = target_record
        self.run_requested: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        root.setSpacing(DesignTokens.Spacing.MD)

        if target_record is not None:
            card = QFrame(self)
            card.setObjectName("SectionCard")
            card.setAttribute(Qt.WA_StyledBackground, True)
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(DesignTokens.Spacing.MD, DesignTokens.Spacing.SM, DesignTokens.Spacing.MD, DesignTokens.Spacing.SM)
            card_lay.setSpacing(3)

            top = QHBoxLayout()
            fname = str(target_record.get("filename") or "session.xdf")
            fname_lbl = QLabel(fname, card)
            fname_lbl.setFont(DesignTokens.ui_font(10, bold=True))
            top.addWidget(fname_lbl)

            status = str(target_record.get("status") or "discovered")
            badge = QLabel(status, card)
            badge.setFont(DesignTokens.mono_font(8, bold=True))
            badge.setStyleSheet(DesignTokens.status_badge_qss(status))
            top.addWidget(badge)
            top.addStretch(1)
            card_lay.addLayout(top)

            raw_path = str(target_record.get("xdf_path") or target_record.get("path") or "")
            if raw_path:
                path_lbl = QLabel(raw_path, card)
                path_lbl.setObjectName("MutedLabel")
                path_lbl.setFont(DesignTokens.mono_font(8))
                path_lbl.setWordWrap(True)
                card_lay.addWidget(path_lbl)
            root.addWidget(card)

        hint = QLabel("Regular LMB on Compute runs immediately with these current options; Alt+LMB opens this panel.", self)
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(DesignTokens.Spacing.MD)
        root.addLayout(form)

        self.write_fif_check = QCheckBox("Write final merged EEG FIF", self)
        self.write_fif_check.setChecked(bool(options.get("should_write_final_merged_eeg_fif", True)))
        form.addRow("Export:", self.write_fif_check)

        self.use_cache_check = QCheckBox("Use disk computation cache (uncheck to force fresh recompute)", self)
        self.use_cache_check.setChecked(bool(options.get("use_computation_cache", True)))
        form.addRow("Cache:", self.use_cache_check)

        self.use_mtime_check = QCheckBox("Include .xdf file mtime in cache fingerprint", self)
        self.use_mtime_check.setChecked(bool(options.get("use_mtime_in_cache_key", False)))
        form.addRow("", self.use_mtime_check)

        self.load_preproc_check = QCheckBox("Load existing preprocessed artifacts when present", self)
        self.load_preproc_check.setChecked(bool(options.get("should_load_preprocessed", False)))
        form.addRow("Preprocessed:", self.load_preproc_check)

        self.workers_spin = QSpinBox(self)
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(int(options.get("absolute_max_workers") or 2))
        form.addRow("Max workers:", self.workers_spin)

        root.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save_only = QPushButton("Save Options", self)
        self.btn_save_only.setToolTip("Save as current computation options without starting computation")
        self.btn_save_only.clicked.connect(self._on_save_only_clicked)
        btn_row.addWidget(self.btn_save_only)

        self.btn_compute_now = QPushButton("Compute", self)
        self.btn_compute_now.setObjectName("ComputeButton")
        self.btn_compute_now.setToolTip("Save options and start computing the selected .xdf session now")
        self.btn_compute_now.setEnabled(target_record is not None)
        self.btn_compute_now.clicked.connect(self._on_compute_now_clicked)
        btn_row.addWidget(self.btn_compute_now)

        root.addLayout(btn_row)


    def _on_save_only_clicked(self) -> None:
        self.run_requested = False
        self.accept()


    def _on_compute_now_clicked(self) -> None:
        self.run_requested = True
        self.accept()


    def get_options(self) -> Dict[str, Any]:
        """Return updated computation option dictionary."""
        return {
            "should_write_final_merged_eeg_fif": self.write_fif_check.isChecked(),
            "use_computation_cache": self.use_cache_check.isChecked(),
            "use_mtime_in_cache_key": self.use_mtime_check.isChecked(),
            "should_load_preprocessed": self.load_preproc_check.isChecked(),
            "absolute_max_workers": self.workers_spin.value(),
        }


def create_preview_computation_options_dialog() -> ComputationOptionsDialog:
    sample_target = {
        "kind": "session",
        "filename": "recording_2026-11-12_183000.xdf",
        "status": "discovered",
        "xdf_path": "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/recording_2026-11-12_183000.xdf",
    }
    return ComputationOptionsDialog(
        options={
            "should_write_final_merged_eeg_fif": True,
            "use_computation_cache": True,
            "use_mtime_in_cache_key": False,
            "should_load_preprocessed": False,
            "absolute_max_workers": 2,
        },
        target_record=sample_target,
    )


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = create_preview_computation_options_dialog()
    dlg.show()
    sys.exit(app.exec_())
