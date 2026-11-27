"""Compact modal dialog for batch EEG session preprocessing (XDF -> FIF / cache)."""

from __future__ import annotations

import fnmatch
import sys
from typing import Any, Dict, List, Optional, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class ProcessSessionsDialog(QDialog):
    """Configure parameters and optional per-session selection for ``SpectogramPlottingHelper.process_XDFs_main``."""

    def __init__(self, defaults: Dict[str, Any], available_filenames: Optional[Sequence[str]] = None, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Process EEG Sessions")
        self.setStyleSheet(DesignTokens.app_stylesheet())
        self.resize(520, 420)

        self._available_filenames: List[str] = [str(f) for f in (available_filenames or []) if str(f)]

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        root.setSpacing(DesignTokens.Spacing.MD)

        subtitle = QLabel("Batch preprocess recorded .xdf sessions and sync catalog status.", self)
        subtitle.setObjectName("MutedLabel")
        root.addWidget(subtitle)

        form = QFormLayout()
        root.addLayout(form)

        self.n_recent_spin = QSpinBox(self)
        self.n_recent_spin.setRange(0, 1000)
        self.n_recent_spin.setSpecialValueText("All")
        raw_recent = defaults.get("n_most_recent_sessions_to_preprocess")
        self.n_recent_spin.setValue(0 if raw_recent is None else int(raw_recent))
        form.addRow("Most recent sessions (0 = All):", self.n_recent_spin)

        self.workers_spin = QSpinBox(self)
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(int(defaults.get("absolute_max_workers") or 2))
        form.addRow("Max workers:", self.workers_spin)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Optional wildcard (*2026-11*) or comma-separated .xdf names")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        form.addRow("Filter / filenames:", self.filter_edit)

        self.write_fif_check = QCheckBox("Write final merged EEG FIF", self)
        self.write_fif_check.setChecked(bool(defaults.get("should_write_final_merged_eeg_fif", True)))
        form.addRow("", self.write_fif_check)

        self.use_cache_check = QCheckBox("Use computation cache", self)
        self.use_cache_check.setChecked(bool(defaults.get("use_computation_cache", True)))
        form.addRow("", self.use_cache_check)

        self.use_mtime_check = QCheckBox("Include .xdf file mtime in cache key", self)
        self.use_mtime_check.setChecked(bool(defaults.get("use_mtime_in_cache_key", False)))
        form.addRow("", self.use_mtime_check)

        self.load_preproc_check = QCheckBox("Load existing preprocessed artifacts", self)
        self.load_preproc_check.setChecked(bool(defaults.get("should_load_preprocessed", False)))
        form.addRow("", self.load_preproc_check)

        list_hdr = QHBoxLayout()
        lbl = QLabel("Optional Specific Sessions (leave unchecked to use Most Recent):", self)
        lbl.setObjectName("MutedLabel")
        list_hdr.addWidget(lbl)
        list_hdr.addStretch(1)

        btn_check_visible = QPushButton("Check Shown", self)
        btn_check_visible.clicked.connect(lambda: self._set_visible_checked(True))
        list_hdr.addWidget(btn_check_visible)

        btn_clear_checks = QPushButton("Clear", self)
        btn_clear_checks.clicked.connect(lambda: self._set_visible_checked(False))
        list_hdr.addWidget(btn_clear_checks)
        root.addLayout(list_hdr)

        self.sessions_list = QListWidget(self)
        for fname in self._available_filenames:
            item = QListWidgetItem(fname, self.sessions_list)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
        ## END for fname in self._available_filenames...

        root.addWidget(self.sessions_list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_process = QPushButton("Process", self)
        self.btn_process.setObjectName("PrimaryButton")
        self.btn_process.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_process)
        root.addLayout(btn_row)


    def _on_filter_changed(self, text: str) -> None:
        pat = text.strip().lower()
        for idx in range(self.sessions_list.count()):
            item = self.sessions_list.item(idx)
            if item is None:
                continue
            name_low = item.text().lower()
            if not pat or "," in pat:
                item.setHidden(False)
            elif "*" in pat or "?" in pat:
                item.setHidden(not fnmatch.fnmatch(name_low, pat))
            else:
                item.setHidden(pat not in name_low)
        ## END for idx in range(self.sessions_list.count())...


    def _set_visible_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for idx in range(self.sessions_list.count()):
            item = self.sessions_list.item(idx)
            if item is not None and not item.isHidden():
                item.setCheckState(state)
        ## END for idx in range(self.sessions_list.count())...


    def get_params(self) -> Dict[str, Any]:
        checked_names: List[str] = []
        for idx in range(self.sessions_list.count()):
            item = self.sessions_list.item(idx)
            if item is not None and item.checkState() == Qt.Checked:
                checked_names.append(item.text().strip())
        ## END for idx in range(self.sessions_list.count())...

        raw_filter = self.filter_edit.text().strip()
        if raw_filter:
            if "*" in raw_filter or "?" in raw_filter:
                matched = [f for f in self._available_filenames if fnmatch.fnmatch(f.lower(), raw_filter.lower())]
                for m in matched:
                    if m not in checked_names:
                        checked_names.append(m)
                ## END for m in matched...
            else:
                for tok in raw_filter.split(","):
                    clean = tok.strip()
                    if clean and clean not in checked_names:
                        checked_names.append(clean)
                ## END for tok in raw_filter.split(",")...

        n_val = self.n_recent_spin.value()
        return {
            "n_most_recent_sessions_to_preprocess": None if n_val == 0 else n_val,
            "included_xdf_file_names": checked_names if checked_names else None,
            "should_write_final_merged_eeg_fif": self.write_fif_check.isChecked(),
            "use_computation_cache": self.use_cache_check.isChecked(),
            "use_mtime_in_cache_key": self.use_mtime_check.isChecked(),
            "should_load_preprocessed": self.load_preproc_check.isChecked(),
            "absolute_max_workers": self.workers_spin.value(),
        }


def create_preview_process_sessions_dialog() -> ProcessSessionsDialog:
    sample_files = [
        "recording_2026-11-12_183000.xdf",
        "recording_2026-11-12_140211.xdf",
        "recording_2026-11-10_093000.xdf",
    ]
    return ProcessSessionsDialog(
        defaults={"n_most_recent_sessions_to_preprocess": 5, "should_write_final_merged_eeg_fif": True, "use_computation_cache": True, "absolute_max_workers": 2},
        available_filenames=sample_files,
    )


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = create_preview_process_sessions_dialog()
    dlg.show()
    sys.exit(app.exec_())
