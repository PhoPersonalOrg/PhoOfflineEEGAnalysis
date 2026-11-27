"""Right-column Day Detail inspector: tab filters, stream chips, context menu, Compute, and Open in Timeline CTA."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens
from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, sample_day_records


class _ComputePushButton(QPushButton):
    """Push button distinguishing regular LMB click (run compute) from Alt+LMB click (open options panel)."""

    computeClicked = pyqtSignal(bool)  # True -> Alt+LMB (open options), False -> regular LMB (start compute)

    def __init__(self, text: str = "Compute", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._alt_held_on_mouse: Optional[bool] = None
        self.clicked.connect(self._on_clicked)


    def mousePressEvent(self, event) -> None:
        if event is not None and event.button() == Qt.LeftButton:
            self._alt_held_on_mouse = bool(event.modifiers() & Qt.AltModifier)
        super().mousePressEvent(event)


    def mouseReleaseEvent(self, event) -> None:
        if event is not None and event.button() == Qt.LeftButton:
            self._alt_held_on_mouse = bool(self._alt_held_on_mouse or (event.modifiers() & Qt.AltModifier))
        super().mouseReleaseEvent(event)


    def _on_clicked(self) -> None:
        if self._alt_held_on_mouse is not None:
            open_options = bool(self._alt_held_on_mouse)
            self._alt_held_on_mouse = None
        else:
            open_options = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
        self.computeClicked.emit(open_options)


class _AltAwareMenu(QMenu):
    """Popup menu that stays open when pressing Alt and tracks Alt+LMB clicks on menu items."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.alt_clicked: bool = False


    def keyPressEvent(self, event) -> None:
        if event is not None and event.key() == Qt.Key_Alt:
            event.accept()
            return
        super().keyPressEvent(event)


    def keyReleaseEvent(self, event) -> None:
        if event is not None and event.key() == Qt.Key_Alt:
            event.accept()
            return
        super().keyReleaseEvent(event)


    def mousePressEvent(self, event) -> None:
        if event is not None and event.button() == Qt.LeftButton:
            self.alt_clicked = bool(event.modifiers() & Qt.AltModifier)
        super().mousePressEvent(event)


    def mouseReleaseEvent(self, event) -> None:
        if event is not None and event.button() == Qt.LeftButton:
            self.alt_clicked = bool(self.alt_clicked or (event.modifiers() & Qt.AltModifier))
        super().mouseReleaseEvent(event)


class DayRecordRowWidget(QWidget):
    """Rich row widget displaying tabular time, status/category badge, filename, duration, and stream chips."""

    def __init__(self, record: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.record = record
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(6, 5, 6, 5)
        vbox.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        rec_dt = record.get("recording_datetime")
        time_str = rec_dt.strftime("%H:%M:%S") if isinstance(rec_dt, datetime) else "--:--:--"
        time_lbl = QLabel(time_str, self)
        time_lbl.setFont(DesignTokens.mono_font(9, bold=True))
        top_row.addWidget(time_lbl)

        kind = str(record.get("kind") or "session")
        if kind == "session":
            status = str(record.get("status") or "discovered")
            badge = QLabel(status, self)
            badge.setFont(DesignTokens.mono_font(8, bold=True))
            badge.setStyleSheet(DesignTokens.status_badge_qss(status))
        else:
            category = str(record.get("category") or kind)
            label_map = {
                "Screenshots": "Screenshot",
                "AudioRecordings": "Audio",
            }
            badge_text = label_map.get(category, f"Video:{category}")
            badge = QLabel(badge_text, self)
            badge.setFont(DesignTokens.mono_font(8))
            badge.setStyleSheet(DesignTokens.category_badge_qss(category))
        top_row.addWidget(badge)

        top_row.addStretch(1)

        dur = record.get("duration_sec")
        if dur is not None:
            dur_str = f"{float(dur) / 60.0:.1f} min"
        elif kind == "screenshot":
            dur_str = "still"
        else:
            dur_str = "dur ?"
        dur_lbl = QLabel(dur_str, self)
        dur_lbl.setObjectName("MutedLabel")
        dur_lbl.setFont(DesignTokens.mono_font(8))
        top_row.addWidget(dur_lbl)
        vbox.addLayout(top_row)

        fname = str(record.get("filename") or "item")
        fname_lbl = QLabel(fname, self)
        fname_lbl.setFont(DesignTokens.ui_font(9, bold=(kind == "session")))
        fname_lbl.setToolTip(str(record.get("path") or fname))
        vbox.addWidget(fname_lbl)

        chips: List[str] = list(record.get("streams") or [])
        if record.get("fif_path"):
            chips.append("FIF")
        if record.get("mat_path"):
            chips.append("MAT")
        if chips:
            chip_row = QHBoxLayout()
            chip_row.setContentsMargins(0, 1, 0, 0)
            chip_row.setSpacing(4)
            for chip_txt in chips:
                chip = QLabel(chip_txt, self)
                chip.setFont(DesignTokens.mono_font(8))
                chip.setStyleSheet(DesignTokens.stream_chip_qss())
                chip_row.addWidget(chip)
            ## END for chip_txt in chips...

            chip_row.addStretch(1)
            vbox.addLayout(chip_row)


class DayDetailPanel(QFrame):
    """Right-hand inspector (~360px) listing selected-day recordings with tab filters, Compute, and Timeline CTA."""

    computeSessionRequested = pyqtSignal(object, bool)  # (record: Dict[str, Any], open_options: bool)
    openInTimelineRequested = pyqtSignal(object)  # Dict[str, Any] (legacy single-session signal)
    openSessionsInTimelineRequested = pyqtSignal(list, str)  # (records: List[Dict[str, Any]], mode: 'append' | 'replace')
    statusMessage = pyqtSignal(str, int)

    _TABS: Tuple[Tuple[str, str], ...] = (
        ("all", "All"),
        ("session", "EEG"),
        ("video", "Video"),
        ("screenshot", "Screenshots"),
        ("audio", "Audio"),
    )

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setObjectName("DayDetailPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(310)

        self._current_day: Optional[datetime] = None
        self._all_records: List[Dict[str, Any]] = []
        self._active_tab: str = "all"
        self._job_running: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG, DesignTokens.Spacing.LG)
        root.setSpacing(DesignTokens.Spacing.MD)

        self.day_header_label = QLabel("Select a day", self)
        self.day_header_label.setObjectName("PanelTitle")
        self.day_header_label.setWordWrap(True)
        root.addWidget(self.day_header_label)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons: Dict[str, QPushButton] = {}
        for tab_key, tab_title in self._TABS:
            btn = QPushButton(tab_title, self)
            btn.setObjectName("TabFilterButton")
            btn.setCheckable(True)
            if tab_key == "all":
                btn.setChecked(True)
            btn.clicked.connect(lambda _checked=False, k=tab_key: self._on_tab_selected(k))
            self._tab_group.addButton(btn)
            self._tab_buttons[tab_key] = btn
            tabs_row.addWidget(btn)
        ## END for tab_key, tab_title in self._TABS...

        root.addLayout(tabs_row)

        self.records_list = QListWidget(self)
        self.records_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.records_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.records_list.customContextMenuRequested.connect(self._on_context_menu)
        self.records_list.currentItemChanged.connect(self._on_selection_changed)
        self.records_list.itemSelectionChanged.connect(self._on_item_selection_changed)
        self.records_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self.records_list, stretch=1)

        sec_row = QHBoxLayout()
        sec_row.setSpacing(DesignTokens.Spacing.SM)
        self.btn_reveal = QPushButton("Reveal in Explorer", self)
        self.btn_reveal.setEnabled(False)
        self.btn_reveal.clicked.connect(lambda: self._reveal_record(self.selected_record()))
        sec_row.addWidget(self.btn_reveal)

        self.btn_copy_path = QPushButton("Copy Path", self)
        self.btn_copy_path.setEnabled(False)
        self.btn_copy_path.clicked.connect(lambda: self._copy_record_path(self.selected_record()))
        sec_row.addWidget(self.btn_copy_path)

        self.btn_open_day_timeline = QPushButton("Open Day", self)
        self.btn_open_day_timeline.setEnabled(False)
        self.btn_open_day_timeline.setToolTip("LMB: Add/merge all EEG sessions from this day into pyPhoTimeline\nShift+LMB: Replace timeline with all EEG sessions from this day")
        self.btn_open_day_timeline.clicked.connect(self._on_open_day_timeline_clicked)
        sec_row.addWidget(self.btn_open_day_timeline)
        root.addLayout(sec_row)

        cta_row = QHBoxLayout()
        cta_row.setSpacing(DesignTokens.Spacing.SM)

        self.btn_compute = _ComputePushButton("Compute", self)
        self.btn_compute.setObjectName("ComputeButton")
        self.btn_compute.setEnabled(False)
        self.btn_compute.setToolTip("LMB: Compute selected .xdf session with current options\nAlt+LMB: Open Computation Options panel")
        self.btn_compute.computeClicked.connect(self._on_compute_clicked)
        cta_row.addWidget(self.btn_compute)

        self.btn_open_timeline = QPushButton("Open in Timeline", self)
        self.btn_open_timeline.setObjectName("PrimaryButton")
        self.btn_open_timeline.setEnabled(False)
        self.btn_open_timeline.setToolTip("LMB: Add/merge selected EEG session(s) (or whole day if none selected) into pyPhoTimeline\nShift+LMB: Replace running timeline with selection")
        self.btn_open_timeline.clicked.connect(self._on_open_timeline_clicked)
        cta_row.addWidget(self.btn_open_timeline, stretch=1)
        root.addLayout(cta_row)


    def set_job_running(self, running: bool) -> None:
        self._job_running = bool(running)
        self._update_action_states()


    def set_day_records(self, day: Optional[datetime], records: List[Dict[str, Any]]) -> None:
        self._current_day = day
        self._all_records = list(records)
        if day is None:
            self.day_header_label.setText("Select a day")
        else:
            n_sess = sum(1 for r in self._all_records if r.get("kind") == "session")
            n_media = len(self._all_records) - n_sess
            self.day_header_label.setText(f"{day.strftime('%Y-%m-%d')} — {n_sess} session(s), {n_media} media")
        self._rebuild_list()


    def _on_tab_selected(self, tab_key: str) -> None:
        self._active_tab = tab_key
        self._rebuild_list()


    def _filtered_records(self) -> List[Dict[str, Any]]:
        if self._active_tab == "all":
            return list(self._all_records)
        return [r for r in self._all_records if str(r.get("kind") or "") == self._active_tab]


    def _rebuild_list(self) -> None:
        self.records_list.clear()
        visible = self._filtered_records()
        for record in visible:
            item = QListWidgetItem(self.records_list)
            item.setData(Qt.UserRole, record)
            row_widget = DayRecordRowWidget(record, parent=self.records_list)
            hint = row_widget.sizeHint()
            item.setSizeHint(QSize(max(240, hint.width()), max(52, hint.height())))
            self.records_list.addItem(item)
            self.records_list.setItemWidget(item, row_widget)
        ## END for record in visible...

        self._update_action_states()


    def selected_record(self) -> Optional[Dict[str, Any]]:
        item = self.records_list.currentItem()
        if item is None:
            sel = self.records_list.selectedItems()
            item = sel[0] if sel else None
        if item is None:
            return None
        payload = item.data(Qt.UserRole)
        return payload if isinstance(payload, dict) else None


    def selected_records(self) -> List[Dict[str, Any]]:
        """Return selected records in top-to-bottom list order (falls back to currentItem when single-focused)."""
        out: List[Dict[str, Any]] = []
        for row_idx in range(self.records_list.count()):
            it = self.records_list.item(row_idx)
            if it is not None and it.isSelected():
                payload = it.data(Qt.UserRole)
                if isinstance(payload, dict):
                    out.append(payload)
        ## END for row_idx in range(self.records_list.count())...

        if out:
            return out
        cur = self.selected_record()
        return [cur] if cur is not None else []


    def selected_eeg_records(self) -> List[Dict[str, Any]]:
        return [r for r in self.selected_records() if r.get("kind") == "session"]


    def day_eeg_records(self) -> List[Dict[str, Any]]:
        return [r for r in self._all_records if r.get("kind") == "session"]


    def _emit_open_sessions(self, records: List[Dict[str, Any]], mode: str = "append") -> None:
        eeg_recs = [r for r in records if isinstance(r, dict) and r.get("kind") == "session"]
        if not eeg_recs:
            return
        mode_norm = "replace" if str(mode).lower() == "replace" else "append"
        if self.receivers(self.openSessionsInTimelineRequested) > 0:
            self.openSessionsInTimelineRequested.emit(eeg_recs, mode_norm)
        elif len(eeg_recs) == 1:
            self.openInTimelineRequested.emit(eeg_recs[0])
        else:
            self.openSessionsInTimelineRequested.emit(eeg_recs, mode_norm)


    def _on_selection_changed(self, _current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        self._update_action_states()


    def _on_item_selection_changed(self) -> None:
        self._update_action_states()


    def _update_action_states(self) -> None:
        sel_all = self.selected_records()
        sel_eeg = self.selected_eeg_records()
        day_eeg = self.day_eeg_records()
        rec = self.selected_record()
        has_rec = rec is not None
        is_single_eeg = bool(has_rec and rec.get("kind") == "session")

        self.btn_reveal.setEnabled(has_rec)
        self.btn_copy_path.setEnabled(has_rec)
        self.btn_compute.setEnabled(is_single_eeg and not self._job_running)

        has_day_eeg = len(day_eeg) > 0
        self.btn_open_day_timeline.setEnabled(has_day_eeg)
        self.btn_open_day_timeline.setText(f"Open Day ({len(day_eeg)})" if has_day_eeg else "Open Day")

        if len(sel_eeg) > 1:
            self.btn_open_timeline.setEnabled(True)
            self.btn_open_timeline.setText(f"Open {len(sel_eeg)} in Timeline")
        elif len(sel_eeg) == 1:
            self.btn_open_timeline.setEnabled(True)
            self.btn_open_timeline.setText("Open in Timeline")
        elif len(sel_all) == 0 and has_day_eeg:
            self.btn_open_timeline.setEnabled(True)
            self.btn_open_timeline.setText(f"Open All ({len(day_eeg)}) in Timeline" if len(day_eeg) > 1 else "Open in Timeline")
        else:
            self.btn_open_timeline.setEnabled(False)
            self.btn_open_timeline.setText("Open in Timeline")


    def _on_compute_clicked(self, open_options: bool) -> None:
        rec = self.selected_record()
        if rec is None or rec.get("kind") != "session":
            return
        self.computeSessionRequested.emit(rec, bool(open_options))


    def _on_open_day_timeline_clicked(self) -> None:
        day_eeg = self.day_eeg_records()
        if not day_eeg:
            return
        mode = "replace" if bool(QApplication.keyboardModifiers() & Qt.ShiftModifier) else "append"
        self._emit_open_sessions(day_eeg, mode=mode)


    def _on_open_timeline_clicked(self) -> None:
        mode = "replace" if bool(QApplication.keyboardModifiers() & Qt.ShiftModifier) else "append"
        sel_eeg = self.selected_eeg_records()
        if sel_eeg:
            self._emit_open_sessions(sel_eeg, mode=mode)
            return
        if not self.selected_records():
            day_eeg = self.day_eeg_records()
            if day_eeg:
                self._emit_open_sessions(day_eeg, mode=mode)


    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.UserRole)
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "session":
            self._emit_open_sessions([payload], mode="append")
        else:
            self._reveal_record(payload)


    def build_context_menu_for_record(self, rec: Dict[str, Any]) -> _AltAwareMenu:
        """Create the right-click context menu for *rec*, supporting multi-selected and full-day timeline handoff."""
        is_eeg = rec.get("kind") == "session"
        sel_eeg = self.selected_eeg_records()
        day_eeg = self.day_eeg_records()
        target_eeg = sel_eeg if (is_eeg and len(sel_eeg) > 1 and rec in sel_eeg) else ([rec] if is_eeg else [])

        menu = _AltAwareMenu(self)
        if is_eeg:
            act_compute = menu.addAction("Compute\tAlt+Click: Options")
            act_compute.setEnabled(not self._job_running)
            act_compute.setToolTip("LMB: Compute with current options | Alt+LMB: Open Computation Options")
            act_compute.triggered.connect(
                lambda _checked=False: self.computeSessionRequested.emit(
                    rec,
                    bool(menu.alt_clicked or (QApplication.keyboardModifiers() & Qt.AltModifier)),
                )
            )
            open_lbl = f"Open {len(target_eeg)} Selected in Timeline" if len(target_eeg) > 1 else "Open in Timeline"
            act_open = menu.addAction(open_lbl)
            act_open.triggered.connect(lambda _checked=False, recs=list(target_eeg): self._emit_open_sessions(recs, mode="append"))

            replace_lbl = f"Replace Timeline with {len(target_eeg)} Selected" if len(target_eeg) > 1 else "Replace Timeline with Session"
            act_replace = menu.addAction(replace_lbl)
            act_replace.triggered.connect(lambda _checked=False, recs=list(target_eeg): self._emit_open_sessions(recs, mode="replace"))

            if len(day_eeg) > 1:
                act_open_day = menu.addAction(f"Open All Day Sessions in Timeline ({len(day_eeg)})")
                act_open_day.triggered.connect(lambda _checked=False, recs=list(day_eeg): self._emit_open_sessions(recs, mode="append"))
                act_replace_day = menu.addAction(f"Replace Timeline with All Day Sessions ({len(day_eeg)})")
                act_replace_day.triggered.connect(lambda _checked=False, recs=list(day_eeg): self._emit_open_sessions(recs, mode="replace"))
            menu.addSeparator()
        elif len(day_eeg) >= 1:
            act_open_day = menu.addAction(f"Open All Day EEG Sessions in Timeline ({len(day_eeg)})")
            act_open_day.triggered.connect(lambda _checked=False, recs=list(day_eeg): self._emit_open_sessions(recs, mode="append"))
            menu.addSeparator()

        act_reveal = menu.addAction("Reveal in Explorer")
        act_reveal.triggered.connect(lambda _checked=False: self._reveal_record(rec))
        act_copy = menu.addAction("Copy Path")
        act_copy.triggered.connect(lambda _checked=False: self._copy_record_path(rec))
        act_source = menu.addAction("Show Containing Source Folder")
        act_source.triggered.connect(lambda _checked=False: self._show_source_folder(rec))
        return menu


    def _on_context_menu(self, pos: QPoint) -> None:
        item = self.records_list.itemAt(pos)
        if item is None:
            day_eeg = self.day_eeg_records()
            if not day_eeg:
                return
            menu = QMenu(self)
            act_all = menu.addAction(f"Open All Day Sessions in Timeline ({len(day_eeg)})")
            act_all.triggered.connect(lambda _checked=False, recs=list(day_eeg): self._emit_open_sessions(recs, mode="append"))
            act_rep = menu.addAction(f"Replace Timeline with All Day Sessions ({len(day_eeg)})")
            act_rep.triggered.connect(lambda _checked=False, recs=list(day_eeg): self._emit_open_sessions(recs, mode="replace"))
            menu.exec_(self.records_list.mapToGlobal(pos))
            return
        if not item.isSelected():
            self.records_list.clearSelection()
            item.setSelected(True)
            self.records_list.setCurrentItem(item)
        payload = item.data(Qt.UserRole)
        rec = payload if isinstance(payload, dict) else self.selected_record()
        if rec is None:
            return
        menu = self.build_context_menu_for_record(rec)
        menu.exec_(self.records_list.mapToGlobal(pos))


    def _reveal_record(self, rec: Optional[Dict[str, Any]]) -> None:
        if rec is None:
            return
        raw_path = rec.get("path") or rec.get("xdf_path") or rec.get("video_path")
        if not raw_path:
            QMessageBox.warning(self, "Reveal in Explorer", "Selected item has no file path.")
            return
        path = Path(str(raw_path))
        if not path.exists():
            QMessageBox.warning(self, "Reveal in Explorer", f"File not found on disk:\n{path}")
            return
        subprocess.run(["explorer", "/select,", str(path.resolve())], check=False)
        self.statusMessage.emit(f"Revealed: {path.name}", 3000)


    def _copy_record_path(self, rec: Optional[Dict[str, Any]]) -> None:
        if rec is None:
            return
        raw_path = rec.get("path") or rec.get("xdf_path") or rec.get("video_path")
        if not raw_path:
            return
        text = str(raw_path)
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(f"Copied: {text}", 3000)


    def _show_source_folder(self, rec: Dict[str, Any]) -> None:
        raw_src = rec.get("source_root")
        if not raw_src:
            raw_path = rec.get("path") or rec.get("xdf_path") or rec.get("video_path")
            raw_src = str(Path(str(raw_path)).parent) if raw_path else None
        if not raw_src:
            return
        folder = Path(str(raw_src))
        if folder.exists():
            subprocess.run(["explorer", str(folder.resolve())], check=False)
            self.statusMessage.emit(f"Opened source folder: {folder}", 3000)
        else:
            QApplication.clipboard().setText(str(folder))
            QMessageBox.information(self, "Containing Source Folder", f"Source folder is currently offline (copied path to clipboard):\n{folder}")


def create_preview_day_detail() -> DayDetailPanel:
    panel = DayDetailPanel()
    panel.setStyleSheet(DesignTokens.app_stylesheet())
    panel.set_day_records(SAMPLE_ANCHOR_DAY, sample_day_records())
    if panel.records_list.count() > 0:
        panel.records_list.setCurrentRow(0)
    panel.resize(370, 760)
    return panel


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    preview = create_preview_day_detail()
    preview.show()
    sys.exit(app.exec_())
