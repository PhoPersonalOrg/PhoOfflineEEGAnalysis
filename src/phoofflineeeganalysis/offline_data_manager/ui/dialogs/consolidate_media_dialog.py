"""Consolidate Media modal dialog: destination root, dry-run toggle, and preview summary."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class ConsolidateMediaDialog(QDialog):
    """Configure destination directory and dry-run flag for ``OfflineDataManagerService.consolidate_media``."""

    def __init__(self, default_dest: Optional[Path] = None, canonical_count: int = 0, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Consolidate Media")
        self.setStyleSheet(DesignTokens.app_stylesheet())
        self.resize(520, 210)
        self._canonical_count = int(canonical_count)

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        root.setSpacing(DesignTokens.Spacing.MD)

        form = QFormLayout()
        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit(default_dest.as_posix() if default_dest is not None else "", self)
        self.dest_edit.textChanged.connect(self._refresh_summary)
        btn_browse = QPushButton("Browse...", self)
        btn_browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.dest_edit, stretch=1)
        dest_row.addWidget(btn_browse)
        form.addRow("Destination root:", dest_row)

        self.dry_run_check = QCheckBox("Dry run (preview planned copies without writing files)", self)
        self.dry_run_check.setChecked(True)
        self.dry_run_check.toggled.connect(self._refresh_summary)
        form.addRow("", self.dry_run_check)
        root.addLayout(form)

        self.summary_label = QLabel("", self)
        self.summary_label.setObjectName("StatsSummary")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_run = QPushButton("Run Consolidate", self)
        self.btn_run.setObjectName("PrimaryButton")
        self.btn_run.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_run)
        root.addLayout(btn_row)

        self._refresh_summary()


    def _browse_dest(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Consolidation Destination", self.dest_edit.text())
        if chosen:
            self.dest_edit.setText(Path(chosen).as_posix())


    def _refresh_summary(self) -> None:
        dest = self.dest_edit.text().strip() or "{dest}"
        verb = "Would copy" if self.dry_run_check.isChecked() else "Will copy"
        self.summary_label.setText(f"{verb} up to {self._canonical_count:,} canonical media file(s) into {dest}/{{category}}/")


    def get_params(self) -> Dict[str, Any]:
        dest_text = self.dest_edit.text().strip()
        return {
            "dest_root": Path(dest_text) if dest_text else None,
            "dry_run": self.dry_run_check.isChecked(),
        }


def create_preview_consolidate_media_dialog() -> ConsolidateMediaDialog:
    return ConsolidateMediaDialog(default_dest=Path("H:/PhoBehaviorProject/RECORDINGS"), canonical_count=128)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = create_preview_consolidate_media_dialog()
    dlg.show()
    sys.exit(app.exec_())
