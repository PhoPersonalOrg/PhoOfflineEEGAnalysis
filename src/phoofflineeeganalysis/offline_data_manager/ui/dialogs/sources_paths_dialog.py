"""Multi-root Sources & Paths configuration dialog for Pho Offline Data Manager."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths
from phoofflineeeganalysis.offline_data_manager.media_catalog import DEFAULT_CONSOLIDATE_ROOT, SourceSpec
from phoofflineeeganalysis.offline_data_manager.media_modalities import MEDIA_CATEGORIES
from phoofflineeeganalysis.offline_data_manager.service import OfflineDataManagerService
from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class SourcesPathsDialog(QDialog):
    """Four-section configuration screen for LabRecorder folders, multimodal media roots, catalogs, and defaults."""

    def __init__(self, service: OfflineDataManagerService, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Sources & Paths — Pho Offline Data Manager")
        self.setStyleSheet(DesignTokens.app_stylesheet())
        self.resize(1020, 830)

        self._service = service
        self._base_paths = service.paths
        source_rows = service.list_sources(probe_filesystem_counts=False)
        _specs, default_consolidate = service.load_media_source_specs()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        outer.setSpacing(DesignTokens.Spacing.MD)

        note = QLabel(f"Overrides save to: {self._base_paths.local_yaml_path.as_posix()}  |  Media sources YAML: {self._base_paths.media_sources_yaml.as_posix()}", self)
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        outer.addWidget(note)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(scroll)
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(0, 0, 4, 0)
        vbox.setSpacing(DesignTokens.Spacing.LG)

        vbox.addWidget(self._build_lab_section(source_rows))
        vbox.addWidget(self._build_media_section(source_rows))
        vbox.addWidget(self._build_catalogs_section(default_consolidate))
        vbox.addWidget(self._build_defaults_section())

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save Sources && Paths", self)
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_save)
        outer.addLayout(btn_row)


    def _build_lab_section(self, source_rows: List[Dict[str, Any]]) -> QFrame:
        card = QFrame(self)
        card.setObjectName("SectionCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.MD, DesignTokens.Spacing.LG, DesignTokens.Spacing.MD)
        lay.setSpacing(DesignTokens.Spacing.SM)

        hdr = QHBoxLayout()
        title = QLabel("1. LabRecorder / XDF Directories", card)
        title.setObjectName("PanelTitle")
        hdr.addWidget(title)
        hdr.addStretch(1)

        btn_add = QPushButton("Add LabRecorder Folder...", card)
        btn_add.clicked.connect(self._on_add_lab_folder)
        hdr.addWidget(btn_add)

        btn_remove = QPushButton("Remove Selected", card)
        btn_remove.clicked.connect(self._on_remove_lab_folder)
        hdr.addWidget(btn_remove)
        lay.addLayout(hdr)

        self.lab_table = QTableWidget(0, 3, card)
        self.lab_table.setHorizontalHeaderLabels(["Status", "Directory Path", "Indexed Sessions"])
        self.lab_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lab_table.verticalHeader().setVisible(False)
        self.lab_table.setMinimumHeight(115)
        h_hdr = self.lab_table.horizontalHeader()
        h_hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h_hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        h_hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        for row in source_rows:
            if row.get("kind") == "lab_recorder":
                self._append_lab_row(str(row.get("path") or ""), exists=bool(row.get("exists")), count=int(row.get("file_count_estimate") or 0))
        ## END for row in source_rows...

        lay.addWidget(self.lab_table)
        return card


    def _append_lab_row(self, path_str: str, *, exists: Optional[bool] = None, count: int = 0) -> None:
        r = self.lab_table.rowCount()
        self.lab_table.insertRow(r)
        is_online = Path(path_str).is_dir() if exists is None else exists
        badge = QLabel("Online" if is_online else "Missing", self.lab_table)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(DesignTokens.health_badge_qss(is_online))
        self.lab_table.setCellWidget(r, 0, badge)

        path_item = QTableWidgetItem(path_str)
        path_item.setToolTip(path_str)
        self.lab_table.setItem(r, 1, path_item)

        cnt_item = QTableWidgetItem(str(count))
        cnt_item.setFlags(cnt_item.flags() & ~Qt.ItemIsEditable)
        cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lab_table.setItem(r, 2, cnt_item)


    def _on_add_lab_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add LabRecorder XDF Directory")
        if chosen:
            self._append_lab_row(Path(chosen).as_posix(), exists=True, count=0)


    def _on_remove_lab_folder(self) -> None:
        rows = sorted({idx.row() for idx in self.lab_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.lab_table.removeRow(r)
        ## END for r in rows...


    def _build_media_section(self, source_rows: List[Dict[str, Any]]) -> QFrame:
        card = QFrame(self)
        card.setObjectName("SectionCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.MD, DesignTokens.Spacing.LG, DesignTokens.Spacing.MD)
        lay.setSpacing(DesignTokens.Spacing.SM)

        hdr = QHBoxLayout()
        title = QLabel("2. Media Sources by Category (Multiple Folders per Modality)", card)
        title.setObjectName("PanelTitle")
        hdr.addWidget(title)
        hdr.addStretch(1)

        self.default_add_category = QComboBox(card)
        for cat in MEDIA_CATEGORIES:
            self.default_add_category.addItem(cat)
        ## END for cat in MEDIA_CATEGORIES...

        hdr.addWidget(self.default_add_category)

        btn_add = QPushButton("Add Media Folder...", card)
        btn_add.clicked.connect(self._on_add_media_folder)
        hdr.addWidget(btn_add)

        btn_remove = QPushButton("Remove Selected", card)
        btn_remove.clicked.connect(self._on_remove_media_folder)
        hdr.addWidget(btn_remove)
        lay.addLayout(hdr)

        self.media_table = QTableWidget(0, 6, card)
        self.media_table.setHorizontalHeaderLabels(["Status", "Category", "Source Path", "Recursive", "Exclude Subpaths (comma-sep)", "Indexed"])
        self.media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.setMinimumHeight(180)
        m_hdr = self.media_table.horizontalHeader()
        m_hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        m_hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        m_hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        m_hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        m_hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        m_hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        for row in source_rows:
            if row.get("kind") != "lab_recorder":
                self._append_media_row(
                    category=str(row.get("kind") or "WebcamRecordings"),
                    path_str=str(row.get("path") or ""),
                    recursive=bool(row.get("recursive")),
                    excludes=[str(x) for x in (row.get("exclude") or [])],
                    exists=bool(row.get("exists")),
                    count=int(row.get("file_count_estimate") or 0),
                )
        ## END for row in source_rows...

        lay.addWidget(self.media_table)
        return card


    def _append_media_row(self, *, category: str, path_str: str, recursive: bool = False, excludes: Optional[List[str]] = None, exists: Optional[bool] = None, count: int = 0) -> None:
        r = self.media_table.rowCount()
        self.media_table.insertRow(r)
        is_online = Path(path_str).is_dir() if exists is None else exists

        badge = QLabel("Online" if is_online else "Offline", self.media_table)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(DesignTokens.health_badge_qss(is_online))
        self.media_table.setCellWidget(r, 0, badge)

        combo = QComboBox(self.media_table)
        for cat in MEDIA_CATEGORIES:
            combo.addItem(cat)
        ## END for cat in MEDIA_CATEGORIES...

        if category not in MEDIA_CATEGORIES:
            combo.addItem(category)
        combo.setCurrentText(category)
        self.media_table.setCellWidget(r, 1, combo)

        p_item = QTableWidgetItem(path_str)
        p_item.setToolTip(path_str)
        self.media_table.setItem(r, 2, p_item)

        chk_wrap = QWidget(self.media_table)
        chk_lay = QHBoxLayout(chk_wrap)
        chk_lay.setContentsMargins(0, 0, 0, 0)
        chk_lay.setAlignment(Qt.AlignCenter)
        chk = QCheckBox(chk_wrap)
        chk.setChecked(bool(recursive))
        chk_lay.addWidget(chk)
        self.media_table.setCellWidget(r, 3, chk_wrap)

        ex_text = ", ".join(excludes or [])
        ex_item = QTableWidgetItem(ex_text)
        ex_item.setToolTip(ex_text or "Optional excluded subdirectories, separated by commas")
        self.media_table.setItem(r, 4, ex_item)

        cnt_item = QTableWidgetItem(str(count))
        cnt_item.setFlags(cnt_item.flags() & ~Qt.ItemIsEditable)
        cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.media_table.setItem(r, 5, cnt_item)


    def _on_add_media_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add Media Source Directory")
        if chosen:
            cat = self.default_add_category.currentText()
            self._append_media_row(category=cat, path_str=Path(chosen).as_posix(), recursive=(cat == "AudioRecordings"), excludes=[], exists=True, count=0)


    def _on_remove_media_folder(self) -> None:
        rows = sorted({idx.row() for idx in self.media_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.media_table.removeRow(r)
        ## END for r in rows...


    def _build_catalogs_section(self, default_consolidate: Path) -> QFrame:
        card = QFrame(self)
        card.setObjectName("SectionCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.MD, DesignTokens.Spacing.LG, DesignTokens.Spacing.MD)

        title = QLabel("3. Catalog Databases & Storage Roots", card)
        title.setObjectName("PanelTitle")
        lay.addWidget(title)

        form = QFormLayout()
        lay.addLayout(form)

        self.session_db_edit = self._make_path_row(form, "Session catalog SQLite (optional):", self._base_paths.session_catalog_path, is_dir=False)
        self.media_db_edit = self._make_path_row(form, "Media catalog SQLite:", self._base_paths.media_catalog_path, is_dir=False)
        self.media_yaml_edit = self._make_path_row(form, "Media sources YAML:", self._base_paths.media_sources_yaml, is_dir=False)
        self.pickled_edit = self._make_path_row(form, "Pickled / cache root:", self._base_paths.pickled_data_path, is_dir=True)
        self.consolidate_dest_edit = self._make_path_row(form, "Consolidate destination root:", default_consolidate, is_dir=True)
        return card


    def _build_defaults_section(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("SectionCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.MD, DesignTokens.Spacing.LG, DesignTokens.Spacing.MD)

        title = QLabel("4. EEG Processing Defaults", card)
        title.setObjectName("PanelTitle")
        lay.addWidget(title)

        defs = self._base_paths.process_defaults
        row = QHBoxLayout()
        row.setSpacing(DesignTokens.Spacing.LG)

        row.addWidget(QLabel("Most recent N sessions (0 = All):", card))
        self.n_recent_spin = QSpinBox(card)
        self.n_recent_spin.setRange(0, 1000)
        self.n_recent_spin.setSpecialValueText("All")
        self.n_recent_spin.setValue(int(defs.get("n_most_recent_sessions_to_preprocess") or 0))
        row.addWidget(self.n_recent_spin)

        self.write_fif_check = QCheckBox("Write merged EEG FIF", card)
        self.write_fif_check.setChecked(bool(defs.get("should_write_final_merged_eeg_fif", True)))
        row.addWidget(self.write_fif_check)

        self.use_cache_check = QCheckBox("Use computation cache", card)
        self.use_cache_check.setChecked(bool(defs.get("use_computation_cache", True)))
        row.addWidget(self.use_cache_check)

        row.addWidget(QLabel("Max workers:", card))
        self.workers_spin = QSpinBox(card)
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(int(defs.get("absolute_max_workers") or 2))
        row.addWidget(self.workers_spin)
        row.addStretch(1)

        lay.addLayout(row)
        return card


    def _make_path_row(self, form: QFormLayout, label: str, initial: Optional[Path], is_dir: bool) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(initial.as_posix() if initial is not None else "", self)
        btn = QPushButton("Browse...", self)
        btn.clicked.connect(lambda: self._browse_into(edit, is_dir=is_dir))
        row.addWidget(edit, stretch=1)
        row.addWidget(btn)
        form.addRow(label, row)
        return edit


    def _browse_into(self, edit: QLineEdit, *, is_dir: bool) -> None:
        if is_dir:
            chosen = QFileDialog.getExistingDirectory(self, "Select Directory", edit.text())
        else:
            chosen, _ = QFileDialog.getSaveFileName(self, "Select File Path", edit.text())
        if chosen:
            edit.setText(Path(chosen).as_posix())


    def build_result(self) -> Dict[str, Any]:
        """Collect edited ``AppPaths``, ``media_specs``, and ``consolidate_dest``."""
        lab_dirs: List[Path] = []
        for r in range(self.lab_table.rowCount()):
            item = self.lab_table.item(r, 1)
            text = item.text().strip() if item is not None else ""
            if text:
                lab_dirs.append(Path(text))
        ## END for r in range(self.lab_table.rowCount())...

        specs: List[SourceSpec] = []
        for r in range(self.media_table.rowCount()):
            combo = self.media_table.cellWidget(r, 1)
            cat = combo.currentText().strip() if isinstance(combo, QComboBox) else "WebcamRecordings"
            p_item = self.media_table.item(r, 2)
            p_text = p_item.text().strip() if p_item is not None else ""
            if not p_text:
                continue
            chk_wrap = self.media_table.cellWidget(r, 3)
            chk = chk_wrap.findChild(QCheckBox) if chk_wrap is not None else None
            rec_val = bool(chk.isChecked()) if chk is not None else False
            ex_item = self.media_table.item(r, 4)
            ex_raw = ex_item.text().strip() if ex_item is not None else ""
            excludes = [Path(tok.strip()) for tok in ex_raw.split(",") if tok.strip()]
            specs.append(SourceSpec(category=cat, path=Path(p_text), recursive=rec_val, exclude=excludes))
        ## END for r in range(self.media_table.rowCount())...

        n_rec = self.n_recent_spin.value()
        proc_defaults = dict(self._base_paths.process_defaults)
        proc_defaults.update(
            {
                "n_most_recent_sessions_to_preprocess": None if n_rec == 0 else n_rec,
                "should_write_final_merged_eeg_fif": self.write_fif_check.isChecked(),
                "use_computation_cache": self.use_cache_check.isChecked(),
                "absolute_max_workers": self.workers_spin.value(),
            }
        )

        pickled_txt = self.pickled_edit.text().strip()
        sess_db_txt = self.session_db_edit.text().strip()
        media_db_txt = self.media_db_edit.text().strip()
        media_yaml_txt = self.media_yaml_edit.text().strip()
        cons_txt = self.consolidate_dest_edit.text().strip()

        updated_paths = AppPaths(
            lab_recorder_dirs=lab_dirs,
            pickled_data_path=Path(pickled_txt) if pickled_txt else None,
            session_catalog_path=Path(sess_db_txt) if sess_db_txt else None,
            media_catalog_path=Path(media_db_txt) if media_db_txt else self._base_paths.media_catalog_path,
            media_sources_yaml=Path(media_yaml_txt) if media_yaml_txt else self._base_paths.media_sources_yaml,
            process_defaults=proc_defaults,
            config_yaml_path=self._base_paths.config_yaml_path,
            local_yaml_path=self._base_paths.local_yaml_path,
        )
        return {
            "paths": updated_paths,
            "media_specs": specs,
            "consolidate_dest": Path(cons_txt) if cons_txt else DEFAULT_CONSOLIDATE_ROOT,
        }


def create_preview_sources_paths_dialog() -> SourcesPathsDialog:
    from phoofflineeeganalysis.offline_data_manager.ui.sample_data import build_populated_sample_service

    tmp_root = Path(tempfile.mkdtemp(prefix="pho_odm_src_preview_"))
    svc = build_populated_sample_service(tmp_root)
    return SourcesPathsDialog(service=svc)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = create_preview_sources_paths_dialog()
    dlg.show()
    sys.exit(app.exec_())
