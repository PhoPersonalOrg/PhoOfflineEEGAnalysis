"""Top desktop action toolbar for Pho Offline Data Manager."""

from __future__ import annotations

import sys
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QAction, QApplication, QLabel, QSizePolicy, QToolBar, QWidget

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class ManagerToolBar(QToolBar):
    """Primary action toolbar with keyboard hints, media overlay toggle, and right-aligned status summary."""

    discoverRequested = pyqtSignal()
    processRequested = pyqtSignal()
    ingestMediaRequested = pyqtSignal()
    duplicateReportRequested = pyqtSignal()
    consolidateMediaRequested = pyqtSignal()
    mediaOverlayToggled = pyqtSignal(bool)
    sourcesPathsRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Actions", parent=parent)
        self.setObjectName("ManagerToolBar")
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)

        self.action_discover = QAction("Discover / Refresh", self)
        self.action_discover.setShortcut("Ctrl+R")
        self.action_discover.setToolTip("Scan LabRecorder folders for .xdf sessions (Ctrl+R)")
        self.action_discover.triggered.connect(self.discoverRequested.emit)
        self.addAction(self.action_discover)

        self.action_process = QAction("Process Recent...", self)
        self.action_process.setToolTip("Preprocess EEG sessions and update catalog status")
        self.action_process.triggered.connect(self.processRequested.emit)
        self.addAction(self.action_process)

        self.addSeparator()

        self.action_ingest_media = QAction("Ingest Media Metadata", self)
        self.action_ingest_media.setToolTip("Scan configured Video, Screenshot, and Audio folders")
        self.action_ingest_media.triggered.connect(self.ingestMediaRequested.emit)
        self.addAction(self.action_ingest_media)

        self.action_duplicate_report = QAction("Duplicate Report", self)
        self.action_duplicate_report.setToolTip("Summarize canonical vs duplicate media groups in the Job Log")
        self.action_duplicate_report.triggered.connect(self.duplicateReportRequested.emit)
        self.addAction(self.action_duplicate_report)

        self.action_consolidate_media = QAction("Consolidate Media...", self)
        self.action_consolidate_media.setToolTip("Copy canonical media files into destination category folders")
        self.action_consolidate_media.triggered.connect(self.consolidateMediaRequested.emit)
        self.addAction(self.action_consolidate_media)

        self.action_toggle_media_overlay = QAction("Show Media Overlay", self)
        self.action_toggle_media_overlay.setCheckable(True)
        self.action_toggle_media_overlay.setChecked(True)
        self.action_toggle_media_overlay.setToolTip("Show or hide video, screenshot, and audio interval bars on the calendar")
        self.action_toggle_media_overlay.toggled.connect(self.mediaOverlayToggled.emit)
        self.addAction(self.action_toggle_media_overlay)

        self.addSeparator()

        self.action_sources_paths = QAction("Sources && Paths...", self)
        self.action_sources_paths.setToolTip("Configure LabRecorder and multimodal source directories, catalogs, and defaults")
        self.action_sources_paths.triggered.connect(self.sourcesPathsRequested.emit)
        self.addAction(self.action_sources_paths)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addWidget(spacer)

        self.summary_label = QLabel("Loaded 0 session(s), 0 media", self)
        self.summary_label.setObjectName("MutedLabel")
        self.summary_label.setFont(DesignTokens.mono_font(9))
        self.addWidget(self.summary_label)

        self._job_actions: List[QAction] = [
            self.action_discover,
            self.action_process,
            self.action_ingest_media,
            self.action_duplicate_report,
            self.action_consolidate_media,
            self.action_sources_paths,
        ]


    def set_summary_text(self, text: str) -> None:
        self.summary_label.setText(text)


    def set_job_running(self, running: bool) -> None:
        for act in self._job_actions:
            act.setEnabled(not running)
        ## END for act in self._job_actions...


def create_preview_toolbar() -> ManagerToolBar:
    bar = ManagerToolBar()
    bar.setStyleSheet(DesignTokens.app_stylesheet())
    bar.set_summary_text("Loaded 142 session(s), 5,434 media")
    bar.resize(1200, 42)
    return bar


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    preview = create_preview_toolbar()
    preview.show()
    sys.exit(app.exec_())
