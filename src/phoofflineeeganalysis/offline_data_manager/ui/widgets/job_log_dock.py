"""Collapsible Job Log console panel with progress bar, active session display, and cooperative Cancel."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class JobLogPanel(QFrame):
    """Collapsible bottom console showing background job progress, active session names, and timestamped logs."""

    cancelRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setObjectName("JobLogPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._collapsed: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.MD, DesignTokens.Spacing.SM, DesignTokens.Spacing.MD, DesignTokens.Spacing.SM)
        root.setSpacing(DesignTokens.Spacing.XS)

        header_row = QHBoxLayout()
        header_row.setSpacing(DesignTokens.Spacing.SM)

        self.btn_toggle_collapse = QPushButton("v  Job Log", self)
        self.btn_toggle_collapse.setFixedWidth(105)
        self.btn_toggle_collapse.clicked.connect(self.toggle_collapsed)
        header_row.addWidget(self.btn_toggle_collapse)

        self.job_progress_bar = QProgressBar(self)
        self.job_progress_bar.setMinimum(0)
        self.job_progress_bar.setMaximum(1)
        self.job_progress_bar.setValue(0)
        self.job_progress_bar.setTextVisible(True)
        self.job_progress_bar.setFormat("Idle")
        header_row.addWidget(self.job_progress_bar, stretch=1)

        self.btn_cancel_job = QPushButton("Cancel", self)
        self.btn_cancel_job.setEnabled(False)
        self.btn_cancel_job.setToolTip("Cancel running job after in-flight session(s) complete (Esc)")
        self.btn_cancel_job.clicked.connect(self.cancelRequested.emit)
        header_row.addWidget(self.btn_cancel_job)

        self.btn_clear_log = QPushButton("Clear", self)
        self.btn_clear_log.setFixedWidth(58)
        self.btn_clear_log.clicked.connect(self.clear_log)
        header_row.addWidget(self.btn_clear_log)
        root.addLayout(header_row)

        self.job_progress_label = QLabel("No job running.", self)
        self.job_progress_label.setObjectName("MutedLabel")
        self.job_progress_label.setWordWrap(True)
        self.job_progress_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.job_progress_label)

        self.job_log = QPlainTextEdit(self)
        self.job_log.setReadOnly(True)
        self.job_log.setMaximumBlockCount(1500)
        self.job_log.setMinimumHeight(95)
        self.job_log.setFont(DesignTokens.mono_font(9))
        root.addWidget(self.job_log, stretch=1)


    def append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.job_log.appendPlainText(f"[{stamp}] {message}")


    def clear_log(self) -> None:
        self.job_log.clear()


    def is_collapsed(self) -> bool:
        return self._collapsed


    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.job_log.setVisible(not self._collapsed)
        self.btn_toggle_collapse.setText(">  Job Log" if self._collapsed else "v  Job Log")
        if self._collapsed:
            self.setMaximumHeight(64)
        else:
            self.setMaximumHeight(16777215)


    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)


def create_preview_job_log() -> JobLogPanel:
    panel = JobLogPanel()
    panel.setStyleSheet(DesignTokens.app_stylesheet())
    panel.job_progress_bar.setMaximum(12)
    panel.job_progress_bar.setValue(5)
    panel.job_progress_bar.setFormat("%v/%m sessions (%p%)")
    panel.job_progress_label.setText("Process EEG Sessions — 5/12 done — active: recording_2026-11-12_140211.xdf")
    panel.btn_cancel_job.setEnabled(True)
    panel.append_log("Started: Process EEG Sessions")
    panel.append_log("Synced cached spectrograms for recording_2026-11-10_093000.xdf")
    panel.resize(780, 190)
    return panel


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    preview = create_preview_job_log()
    preview.show()
    sys.exit(app.exec_())
