"""Left navigation/filter rail for Manager Home: aggregate stats, modality checklist, and quick actions."""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Set

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.media_modalities import ALL_MODALITY_KEYS, MODALITY_DISPLAY_LABELS
from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens
from phoofflineeeganalysis.offline_data_manager.ui.sample_data import sample_stats


class LeftRailPanel(QFrame):
    """Narrow left rail (~240px) with stats, Recording Modalities checklist, and quick actions."""

    modalityFiltersChanged = pyqtSignal(object)  # Set[str]
    discoverRequested = pyqtSignal()
    ingestMediaRequested = pyqtSignal()
    duplicateReportRequested = pyqtSignal()
    processRequested = pyqtSignal()
    consolidateMediaRequested = pyqtSignal()
    sourcesPathsRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setObjectName("LeftRailPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self.setMaximumWidth(285)

        self._updating_checks: bool = False
        self._modality_checks: Dict[str, QCheckBox] = {}
        self._modality_counts: Dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        root.setSpacing(DesignTokens.Spacing.MD)

        self.title_label = QLabel("Pho Offline Data Manager", self)
        self.title_label.setObjectName("PanelTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)

        self.stats_label = QLabel("0 sessions · 0 videos\n0 audio · 0 screenshots", self)
        self.stats_label.setObjectName("StatsSummary")
        self.stats_label.setFont(DesignTokens.mono_font(9))
        self.stats_label.setWordWrap(True)
        root.addWidget(self.stats_label)

        hdr_row = QHBoxLayout()
        mod_header = QLabel("Recording Modalities", self)
        mod_header.setFont(DesignTokens.ui_font(10, bold=True))
        hdr_row.addWidget(mod_header)
        hdr_row.addStretch(1)

        self.btn_all_mods = QPushButton("All", self)
        self.btn_all_mods.setFixedWidth(36)
        self.btn_all_mods.clicked.connect(lambda: self._set_all_modalities(True))
        hdr_row.addWidget(self.btn_all_mods)

        self.btn_none_mods = QPushButton("None", self)
        self.btn_none_mods.setFixedWidth(44)
        self.btn_none_mods.clicked.connect(lambda: self._set_all_modalities(False))
        hdr_row.addWidget(self.btn_none_mods)
        root.addLayout(hdr_row)

        mod_card = QFrame(self)
        mod_card.setObjectName("SectionCard")
        mod_card.setAttribute(Qt.WA_StyledBackground, True)
        mod_vbox = QVBoxLayout(mod_card)
        mod_vbox.setContentsMargins(DesignTokens.Spacing.MD, DesignTokens.Spacing.SM, DesignTokens.Spacing.MD, DesignTokens.Spacing.SM)
        mod_vbox.setSpacing(DesignTokens.Spacing.XS)

        for key in ALL_MODALITY_KEYS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 1, 0, 1)
            row.setSpacing(DesignTokens.Spacing.SM)

            swatch = QFrame(mod_card)
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(DesignTokens.modality_swatch_qss(key))
            row.addWidget(swatch)

            chk = QCheckBox(MODALITY_DISPLAY_LABELS.get(key, key), mod_card)
            chk.setChecked(True)
            chk.toggled.connect(self._on_check_toggled)
            row.addWidget(chk, stretch=1)

            cnt_lbl = QLabel("0", mod_card)
            cnt_lbl.setObjectName("MutedLabel")
            cnt_lbl.setFont(DesignTokens.mono_font(8))
            cnt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(cnt_lbl)

            self._modality_checks[key] = chk
            self._modality_counts[key] = cnt_lbl
            mod_vbox.addLayout(row)
        ## END for key in ALL_MODALITY_KEYS...

        root.addWidget(mod_card)

        actions_header = QLabel("Quick Actions", self)
        actions_header.setFont(DesignTokens.ui_font(10, bold=True))
        root.addWidget(actions_header)

        self.btn_discover = QPushButton("Discover Sessions", self)
        self.btn_discover.setToolTip("Scan configured LabRecorder folders for .xdf files")
        self.btn_discover.clicked.connect(self.discoverRequested.emit)
        root.addWidget(self.btn_discover)

        self.btn_ingest_media = QPushButton("Ingest Media Metadata", self)
        self.btn_ingest_media.setToolTip("Scan Video, Screenshot, and Audio source roots")
        self.btn_ingest_media.clicked.connect(self.ingestMediaRequested.emit)
        root.addWidget(self.btn_ingest_media)

        self.btn_dup_report = QPushButton("Duplicate Report", self)
        self.btn_dup_report.clicked.connect(self.duplicateReportRequested.emit)
        root.addWidget(self.btn_dup_report)

        self.btn_process = QPushButton("Process Recent EEG...", self)
        self.btn_process.clicked.connect(self.processRequested.emit)
        root.addWidget(self.btn_process)

        self.btn_consolidate = QPushButton("Consolidate Media...", self)
        self.btn_consolidate.clicked.connect(self.consolidateMediaRequested.emit)
        root.addWidget(self.btn_consolidate)

        root.addStretch(1)

        self.btn_sources_paths = QPushButton("Config / Sources && Paths...", self)
        self.btn_sources_paths.setToolTip("Manage multi-folder sources, SQLite catalogs, and defaults")
        self.btn_sources_paths.clicked.connect(self.sourcesPathsRequested.emit)
        root.addWidget(self.btn_sources_paths)

        self._job_buttons: List[QPushButton] = [
            self.btn_discover,
            self.btn_ingest_media,
            self.btn_dup_report,
            self.btn_process,
            self.btn_consolidate,
            self.btn_sources_paths,
        ]


    def active_modality_filters(self) -> Set[str]:
        return {key for key, chk in self._modality_checks.items() if chk.isChecked()}


    def set_active_modality_filters(self, active: Set[str]) -> None:
        self._updating_checks = True
        try:
            for key, chk in self._modality_checks.items():
                chk.setChecked(key in active)
            ## END for key, chk in self._modality_checks.items()...
        finally:
            self._updating_checks = False
        self.modalityFiltersChanged.emit(self.active_modality_filters())


    def _set_all_modalities(self, checked: bool) -> None:
        self._updating_checks = True
        try:
            for chk in self._modality_checks.values():
                chk.setChecked(checked)
            ## END for chk in self._modality_checks.values()...
        finally:
            self._updating_checks = False
        self.modalityFiltersChanged.emit(self.active_modality_filters())


    def _on_check_toggled(self, _checked: bool) -> None:
        if self._updating_checks:
            return
        self.modalityFiltersChanged.emit(self.active_modality_filters())


    def update_stats(self, stats: Dict[str, Any]) -> None:
        n_sess = int(stats.get("total_sessions") or 0)
        by_kind = stats.get("media_by_kind") or {}
        by_cat = stats.get("media_by_category") or {}
        n_vid = int(by_kind.get("video") or 0)
        n_aud = int(by_kind.get("audio") or 0)
        n_shot = int(by_kind.get("screenshot") or 0)
        self.stats_label.setText(f"{n_sess:,} sessions · {n_vid:,} videos\n{n_aud:,} audio · {n_shot:,} screenshots")

        if "EEG" in self._modality_counts:
            self._modality_counts["EEG"].setText(f"{n_sess:,}")
        for cat, lbl in self._modality_counts.items():
            if cat == "EEG":
                continue
            lbl.setText(f"{int(by_cat.get(cat) or 0):,}")
        ## END for cat, lbl in self._modality_counts.items()...


    def set_job_running(self, running: bool) -> None:
        for btn in self._job_buttons:
            btn.setEnabled(not running)
        ## END for btn in self._job_buttons...


def create_preview_left_rail() -> LeftRailPanel:
    panel = LeftRailPanel()
    panel.setStyleSheet(DesignTokens.app_stylesheet())
    panel.update_stats(sample_stats())
    panel.resize(250, 760)
    return panel


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    preview = create_preview_left_rail()
    preview.show()
    sys.exit(app.exec_())
