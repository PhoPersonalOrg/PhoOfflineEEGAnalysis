#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PyQt month-grid calendar for recorded EEG sessions (and optional video overlay).

Preferred import::

    from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import (
        CalendarDatasource, CustomDataDisplayingCalendar, SessionCalendarPanel,
        SessionCalendarWindow, launch_session_calendar,
    )
"""

from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd
from attrs import define, field
from PyQt5.QtCore import QDate, QDateTime, QPoint, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

dt_col_names = ["recording_datetime", "recording_day_date"]
timestamp_column_names = ["created_at", "first_timestamp", "last_timestamp"]
timestamp_dt_column_names = ["created_at_dt", "first_timestamp_dt", "last_timestamp_dt"]

STATUS_COLORS = {
    "discovered": QColor(100, 149, 237, 120),  # cornflower
    "computing": QColor(255, 193, 7, 140),  # amber
    "computed": QColor(40, 167, 69, 140),  # green
    "exported": QColor(0, 123, 255, 150),  # blue
    "failed": QColor(220, 53, 69, 150),  # red
}

VIDEO_CATEGORY_COLORS = {
    "EyeRecordings": QColor(156, 39, 176, 175),  # purple
    "WebcamRecordings": QColor(0, 150, 136, 175),  # teal
    "ScreenRecordings": QColor(255, 111, 0, 175),  # orange
    "default": QColor(120, 144, 156, 165),  # slate
}


def _ensure_utc_day(day_date: datetime) -> datetime:
    if day_date.tzinfo is None:
        day_date = day_date.replace(tzinfo=timezone.utc)
    else:
        day_date = day_date.astimezone(timezone.utc)
    return day_date.replace(hour=0, minute=0, second=0, microsecond=0)


def _normalize_video_df(video_infos_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if video_infos_df is None or len(video_infos_df) == 0:
        return pd.DataFrame(columns=["video_path", "filename", "category", "recording_datetime", "recording_day_date", "duration_sec", "first_timestamp_rel", "duration_rel"])
    vdf = deepcopy(video_infos_df)
    for col in dt_col_names:
        if col in vdf.columns:
            vdf[col] = pd.to_datetime(vdf[col], utc=True, errors="coerce")
    ## END for col in dt_col_names....

    if "duration_rel" not in vdf.columns:
        vdf["duration_rel"] = 0.03
    if "first_timestamp_rel" not in vdf.columns:
        vdf["first_timestamp_rel"] = 0.1
    if "category" not in vdf.columns:
        vdf["category"] = "default"
    vdf["duration_rel"] = pd.to_numeric(vdf["duration_rel"], errors="coerce").fillna(0.03).clip(lower=0.01, upper=1.0)
    vdf["first_timestamp_rel"] = pd.to_numeric(vdf["first_timestamp_rel"], errors="coerce").fillna(0.1).clip(lower=0.0, upper=0.99)
    return vdf


@define(slots=False)
class CalendarDatasource:
    """Calendar data adapter over a session DataFrame and optional video DataFrame.

    from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import CalendarDatasource
    """

    xdf_stream_infos_df: pd.DataFrame = field()
    video_infos_df: Optional[pd.DataFrame] = field(default=None)
    eeg_only_stream_infos_df: pd.DataFrame = field(init=False)
    video_calendar_df: pd.DataFrame = field(init=False)

    def __attrs_post_init__(self):
        src = self.xdf_stream_infos_df
        if "idx_bak" in src.columns:
            df = deepcopy(src)
        else:
            df = deepcopy(src.reset_index(drop=False, inplace=False, names="idx_bak"))
        for col in dt_col_names:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        ## END for col in dt_col_names....

        if "name" in df.columns:
            eeg_df = df[df["name"] == "Epoc X"].copy()
            if len(eeg_df) == 0:
                eeg_df = df.copy()
        else:
            eeg_df = df.copy()

        for a_ts_col_name, a_ts_dt_col_name in zip(timestamp_column_names, timestamp_dt_column_names):
            if a_ts_dt_col_name not in eeg_df.columns and a_ts_col_name in eeg_df.columns and "recording_datetime" in eeg_df.columns:
                try:
                    eeg_df[a_ts_dt_col_name] = [pd.Timedelta(seconds=float(v)) if pd.notna(v) else pd.NaT for v in eeg_df[a_ts_col_name].to_numpy()]
                    eeg_df[a_ts_dt_col_name] = eeg_df["recording_datetime"] + eeg_df[a_ts_dt_col_name]
                except (ValueError, AttributeError, TypeError):
                    pass
            ## END if a_ts_dt_col_name not in eeg_df.columns....
        ## END for a_ts_col_name, a_ts_dt_col_name in zip(...)....

        if "duration_rel" not in eeg_df.columns:
            eeg_df["duration_rel"] = 0.05
        if "first_timestamp_rel" not in eeg_df.columns:
            eeg_df["first_timestamp_rel"] = 0.1
        if "status" not in eeg_df.columns:
            eeg_df["status"] = "discovered"

        eeg_df["duration_rel"] = pd.to_numeric(eeg_df["duration_rel"], errors="coerce").fillna(0.05).clip(lower=0.01, upper=1.0)
        eeg_df["first_timestamp_rel"] = pd.to_numeric(eeg_df["first_timestamp_rel"], errors="coerce").fillna(0.1).clip(lower=0.0, upper=0.99)

        self.eeg_only_stream_infos_df = eeg_df
        self.video_calendar_df = _normalize_video_df(self.video_infos_df)

    def set_video_dataframe(self, video_infos_df: Optional[pd.DataFrame]) -> None:
        self.video_infos_df = video_infos_df
        self.video_calendar_df = _normalize_video_df(video_infos_df)

    @property
    def records_per_day_date(self):
        if "recording_day_date" not in self.eeg_only_stream_infos_df.columns:
            return {}
        return self.eeg_only_stream_infos_df["recording_day_date"].value_counts().to_dict()

    def get_records_for_day(self, day_date: datetime) -> pd.DataFrame:
        day_date = _ensure_utc_day(day_date)
        if "recording_day_date" not in self.eeg_only_stream_infos_df.columns:
            return self.eeg_only_stream_infos_df.iloc[0:0]
        day_series = pd.to_datetime(self.eeg_only_stream_infos_df["recording_day_date"], utc=True, errors="coerce")
        mask = day_series.map(lambda v: _ensure_utc_day(v.to_pydatetime()) == day_date if pd.notna(v) else False)
        return self.eeg_only_stream_infos_df.loc[mask]

    def get_videos_for_day(self, day_date: datetime) -> pd.DataFrame:
        day_date = _ensure_utc_day(day_date)
        if len(self.video_calendar_df) == 0 or "recording_day_date" not in self.video_calendar_df.columns:
            return self.video_calendar_df.iloc[0:0]
        day_series = pd.to_datetime(self.video_calendar_df["recording_day_date"], utc=True, errors="coerce")
        mask = day_series.map(lambda v: _ensure_utc_day(v.to_pydatetime()) == day_date if pd.notna(v) else False)
        return self.video_calendar_df.loc[mask]


class CustomDataDisplayingCalendar(QCalendarWidget):
    """Month-grid calendar that paints session intervals colored by status and optional video bars."""

    daySelectionChanged = pyqtSignal(object)  # datetime day (UTC midnight) or None

    def __init__(self, datasource=None, parent=None, show_video_overlay: bool = True):
        super().__init__(parent=parent)
        self._datasource = datasource
        self._show_video_overlay = bool(show_video_overlay)
        self.color = QColor(self.palette().color(QPalette.Highlight))
        self.color.setAlpha(64)
        self.selectionChanged.connect(self._on_selection_changed)

    @property
    def datasource(self) -> Optional[CalendarDatasource]:
        return self._datasource

    @datasource.setter
    def datasource(self, value: CalendarDatasource):
        self._datasource = value
        self.updateCells()

    def set_datasource(self, data_source):
        self._datasource = data_source
        self.updateCells()

    @property
    def show_video_overlay(self) -> bool:
        return self._show_video_overlay

    def set_show_video_overlay(self, enabled: bool) -> None:
        self._show_video_overlay = bool(enabled)
        self.updateCells()

    def _on_selection_changed(self):
        self.updateCells()
        if self.datasource is None:
            self.daySelectionChanged.emit(None)
            return
        qdate: QDate = self.selectedDate()
        converted = QDateTime(qdate).toPyDateTime().replace(tzinfo=timezone.utc)
        self.daySelectionChanged.emit(_ensure_utc_day(converted))

    def paintCell(self, painter, rect, date):
        QCalendarWidget.paintCell(self, painter, rect, date)
        if self.datasource is None:
            return

        converted_date = QDateTime(date).toPyDateTime().replace(tzinfo=timezone.utc)
        found_df: pd.DataFrame = self.datasource.get_records_for_day(converted_date)
        videos_df: pd.DataFrame = self.datasource.get_videos_for_day(converted_date) if self._show_video_overlay else pd.DataFrame()

        has_videos = (videos_df is not None) and (len(videos_df) > 0)
        if found_df is not None and len(found_df) > 0:
            desired_width = rect.width() * (0.76 if has_videos else 0.90)
            for a_row in found_df.itertuples():
                status = getattr(a_row, "status", "discovered") or "discovered"
                fill = STATUS_COLORS.get(str(status), STATUS_COLORS["discovered"])
                duration_rel = float(getattr(a_row, "duration_rel", 0.05) or 0.05)
                first_rel = float(getattr(a_row, "first_timestamp_rel", 0.1) or 0.1)
                desired_height = max(2.0, float(rect.height()) * duration_rel)
                desired_start_y = float(rect.height()) * first_rel
                desired_top = float(rect.top()) + desired_start_y
                if desired_top + desired_height > rect.bottom():
                    desired_height = max(2.0, float(rect.bottom()) - desired_top)
                a_rect = QRectF(rect.left(), desired_top, desired_width, desired_height)
                painter.fillRect(a_rect, fill)
            ## END for a_row in found_df.itertuples()....

        if has_videos:
            video_left = float(rect.left()) + float(rect.width()) * 0.80
            video_width = max(3.0, float(rect.width()) * 0.16)
            for v_row in videos_df.itertuples():
                category = str(getattr(v_row, "category", "default") or "default")
                fill = VIDEO_CATEGORY_COLORS.get(category, VIDEO_CATEGORY_COLORS["default"])
                duration_rel = float(getattr(v_row, "duration_rel", 0.03) or 0.03)
                first_rel = float(getattr(v_row, "first_timestamp_rel", 0.1) or 0.1)
                desired_height = max(2.0, float(rect.height()) * duration_rel)
                desired_start_y = float(rect.height()) * first_rel
                desired_top = float(rect.top()) + desired_start_y
                if desired_top + desired_height > rect.bottom():
                    desired_height = max(2.0, float(rect.bottom()) - desired_top)
                v_rect = QRectF(video_left, desired_top, video_width, desired_height)
                painter.fillRect(v_rect, fill)
            ## END for v_row in videos_df.itertuples()....


class SessionCalendarPanel(QWidget):
    """Embeddable calendar + day detail list widget for sessions and optional videos."""

    statusMessage = pyqtSignal(str, int)

    def __init__(self, datasource: Optional[CalendarDatasource] = None, parent=None, show_video_overlay: bool = True):
        super().__init__(parent=parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        self.calendar = CustomDataDisplayingCalendar(datasource=datasource, show_video_overlay=show_video_overlay)
        splitter.addWidget(self.calendar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.day_label = QLabel("Select a day")
        self.day_label.setWordWrap(True)
        right_layout.addWidget(self.day_label)
        self.session_list = QListWidget()
        self.session_list.itemDoubleClicked.connect(self._on_session_activated)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._on_session_list_context_menu)
        right_layout.addWidget(self.session_list)
        self.open_timeline_btn = QPushButton("Open in Timeline")
        self.open_timeline_btn.clicked.connect(self._on_open_timeline_clicked)
        right_layout.addWidget(self.open_timeline_btn)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.calendar.daySelectionChanged.connect(self._on_day_selected)
        if datasource is not None:
            self.set_datasource(datasource)

    def selected_day_utc(self) -> datetime:
        return _ensure_utc_day(QDateTime(self.calendar.selectedDate()).toPyDateTime().replace(tzinfo=timezone.utc))

    def set_datasource(self, datasource: CalendarDatasource) -> None:
        self.calendar.set_datasource(datasource)
        self.refresh_selected_day()

    def set_show_video_overlay(self, enabled: bool) -> None:
        self.calendar.set_show_video_overlay(enabled)
        self.refresh_selected_day()

    def refresh_selected_day(self) -> None:
        self._on_day_selected(self.selected_day_utc())

    def _emit_status(self, message: str, timeout_ms: int = 3000) -> None:
        self.statusMessage.emit(message, timeout_ms)

    def _on_day_selected(self, day_date: Optional[datetime]):
        self.session_list.clear()
        if day_date is None or self.calendar.datasource is None:
            self.day_label.setText("Select a day")
            return

        records = self.calendar.datasource.get_records_for_day(day_date)
        videos = self.calendar.datasource.get_videos_for_day(day_date) if self.calendar.show_video_overlay else pd.DataFrame()
        n_sessions = len(records)
        n_videos = len(videos) if videos is not None else 0
        if self.calendar.show_video_overlay and n_videos > 0:
            self.day_label.setText(f"{day_date.strftime('%Y-%m-%d')} — {n_sessions} session(s), {n_videos} video(s)")
        else:
            self.day_label.setText(f"{day_date.strftime('%Y-%m-%d')} — {n_sessions} session(s)")

        for a_row in records.itertuples():
            fname = getattr(a_row, "xdf_filename", None) or getattr(a_row, "name", "session")
            status = getattr(a_row, "status", "discovered")
            rec_dt = getattr(a_row, "recording_datetime", None)
            time_str = rec_dt.strftime("%H:%M:%S") if isinstance(rec_dt, datetime) else "?"
            dur = getattr(a_row, "duration_sec", None)
            if dur is not None and pd.notna(dur):
                dur_str = f"{float(dur) / 60.0:.1f} min"
                dur_val = float(dur)
            else:
                dur_str = "duration unknown"
                dur_val = None
            text = f"{time_str}  [{status}]  {fname}  ({dur_str})"
            item = QListWidgetItem(text)
            item.setData(
                Qt.UserRole,
                {
                    "kind": "session",
                    "xdf_path": getattr(a_row, "xdf_path", None),
                    "recording_datetime": rec_dt,
                    "duration_sec": dur_val,
                    "status": status,
                },
            )
            self.session_list.addItem(item)
        ## END for a_row in records.itertuples()....

        if self.calendar.show_video_overlay and videos is not None and len(videos) > 0:
            for v_row in videos.itertuples():
                fname = getattr(v_row, "filename", None) or Path(str(getattr(v_row, "video_path", "video"))).name
                category = getattr(v_row, "category", "Video")
                rec_dt = getattr(v_row, "recording_datetime", None)
                time_str = rec_dt.strftime("%H:%M:%S") if isinstance(rec_dt, datetime) else "?"
                dur = getattr(v_row, "duration_sec", None)
                if dur is not None and pd.notna(dur):
                    dur_str = f"{float(dur) / 60.0:.1f} min"
                    dur_val = float(dur)
                else:
                    dur_str = "duration unknown"
                    dur_val = None
                text = f"{time_str}  [Video:{category}]  {fname}  ({dur_str})"
                item = QListWidgetItem(text)
                item.setData(
                    Qt.UserRole,
                    {
                        "kind": "video",
                        "video_path": getattr(v_row, "video_path", None),
                        "category": category,
                        "recording_datetime": rec_dt,
                        "duration_sec": dur_val,
                    },
                )
                self.session_list.addItem(item)
            ## END for v_row in videos.itertuples()....

    def _session_payload(self, item: Optional[QListWidgetItem] = None) -> Optional[object]:
        if item is None:
            item = self.session_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _current_item_path(self, item: Optional[QListWidgetItem] = None) -> Optional[Path]:
        payload = self._session_payload(item)
        if payload is None:
            return None
        if isinstance(payload, dict):
            raw_path = payload.get("video_path") if payload.get("kind") == "video" else payload.get("xdf_path")
        else:
            raw_path = payload  # Legacy: bare path string
        if not raw_path:
            return None
        return Path(raw_path)

    def _current_session_xdf_path(self, item: Optional[QListWidgetItem] = None) -> Optional[Path]:
        return self._current_item_path(item)

    def _on_session_list_context_menu(self, pos: QPoint):
        item = self.session_list.itemAt(pos)
        if item is None:
            return
        self.session_list.setCurrentItem(item)
        payload = self._session_payload(item)
        is_video = isinstance(payload, dict) and payload.get("kind") == "video"
        menu = QMenu(self)
        open_action = None if is_video else menu.addAction("Open in Timeline")
        reveal_action = menu.addAction("Reveal in Explorer")
        copy_action = menu.addAction("Copy Path")
        chosen = menu.exec_(self.session_list.mapToGlobal(pos))
        if open_action is not None and chosen == open_action:
            self._on_session_activated(item)
        elif chosen == reveal_action:
            self._on_reveal_in_explorer(item)
        elif chosen == copy_action:
            self._on_copy_path(item)

    def _on_open_timeline_clicked(self):
        item = self.session_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Open in Timeline", "Select a session in the list first.")
            return
        payload = self._session_payload(item)
        if isinstance(payload, dict) and payload.get("kind") == "video":
            QMessageBox.information(self, "Open in Timeline", "Select an EEG session row to open in Timeline (videos support Reveal in Explorer / Copy Path).")
            return
        self._on_session_activated(item)

    def _on_reveal_in_explorer(self, item: Optional[QListWidgetItem] = None):
        path = self._current_item_path(item)
        if path is None:
            QMessageBox.warning(self, "Reveal in Explorer", "Selected item has no file path.")
            return
        if not path.exists():
            QMessageBox.warning(self, "Reveal in Explorer", f"File not found:\n{path}")
            return
        # `/select,` and path must be separate argv entries so spaces/parens in the path work
        resolved = str(path.resolve())
        subprocess.run(["explorer", "/select,", resolved], check=False)
        self._emit_status(f"Revealed: {path.name}", 3000)

    def _on_copy_path(self, item: Optional[QListWidgetItem] = None):
        path = self._current_item_path(item)
        if path is None:
            QMessageBox.warning(self, "Copy Path", "Selected item has no file path.")
            return
        text = str(path)
        QApplication.clipboard().setText(text)
        self._emit_status(f"Copied: {text}", 3000)

    def _on_session_activated(self, item: QListWidgetItem):
        payload = self._session_payload(item)
        if isinstance(payload, dict) and payload.get("kind") == "video":
            self._on_reveal_in_explorer(item)
            return
        if not isinstance(payload, dict):
            # Legacy: bare path string
            xdf_path = payload
            recording_datetime = None
            duration_sec = None
        else:
            xdf_path = payload.get("xdf_path")
            recording_datetime = payload.get("recording_datetime")
            duration_sec = payload.get("duration_sec")
        if not xdf_path:
            QMessageBox.warning(self, "Open in Timeline", "Session has no xdf_path.")
            return
        try:
            from phoofflineeeganalysis.analysis.timeline_handoff import open_session_in_timeline

            status = payload.get("status") if isinstance(payload, dict) else None
            mode = open_session_in_timeline(xdf_path, start=recording_datetime, duration_sec=duration_sec, status=status)
            cache_note = " (prefer cached spectrograms)" if str(status or "").lower() in ("computed", "exported") else ""
            if mode == "ipc":
                self._emit_status(f"Zoomed/loaded in running timeline: {Path(xdf_path).name}{cache_note}", 5000)
            elif mode == "ipc_error":
                QMessageBox.warning(self, "Open in Timeline", f"Running timeline rejected show_session for:\n{Path(xdf_path).name}\n\nNo new viewer was started.")
                self._emit_status(f"Timeline IPC error for: {Path(xdf_path).name}", 5000)
            else:
                self._emit_status(f"Started offline timeline for: {Path(xdf_path).name}{cache_note}", 5000)
        except Exception as e:
            QMessageBox.warning(self, "Open in Timeline", str(e))


class SessionCalendarWindow(QMainWindow):
    """Standalone main window wrapping ``SessionCalendarPanel`` (notebook/legacy entrypoint)."""

    def __init__(self, datasource: Optional[CalendarDatasource] = None, parent=None, show_video_overlay: bool = True):
        super().__init__(parent=parent)
        self.setWindowTitle("EEG Session Calendar")
        self.resize(900, 560)

        self.panel = SessionCalendarPanel(datasource=datasource, parent=self, show_video_overlay=show_video_overlay)
        self.setCentralWidget(self.panel)
        self.panel.statusMessage.connect(self.statusBar().showMessage)

        # Backwards-compatible widget aliases
        self.calendar = self.panel.calendar
        self.day_label = self.panel.day_label
        self.session_list = self.panel.session_list
        self.open_timeline_btn = self.panel.open_timeline_btn

    def set_datasource(self, datasource: CalendarDatasource) -> None:
        self.panel.set_datasource(datasource)

    def _on_day_selected(self, day_date: Optional[datetime]):
        self.panel._on_day_selected(day_date)

    def _on_session_list_context_menu(self, pos: QPoint):
        self.panel._on_session_list_context_menu(pos)

    def _on_open_timeline_clicked(self):
        self.panel._on_open_timeline_clicked()

    def _on_reveal_in_explorer(self, item: Optional[QListWidgetItem] = None):
        self.panel._on_reveal_in_explorer(item)

    def _on_copy_path(self, item: Optional[QListWidgetItem] = None):
        self.panel._on_copy_path(item)

    def _on_session_activated(self, item: QListWidgetItem):
        self.panel._on_session_activated(item)


def launch_session_calendar(catalog_path: Optional[Union[str, Path]] = None, discovery_dirs: Optional[Sequence[Union[str, Path]]] = None, pickled_data_path: Optional[Path] = None, datasource: Optional[CalendarDatasource] = None, video_catalog_path: Optional[Union[str, Path]] = None, block: bool = True) -> SessionCalendarWindow:
    """Open the standalone session calendar window, optionally discovering into the SQLite catalog first.

    Args:
        catalog_path: SQLite catalog path (default under PICKLED_COLLECTION/session_catalog).
        discovery_dirs: If provided, scan these dirs and upsert into the catalog before showing.
        pickled_data_path: Used when resolving the default catalog path.
        datasource: Optional pre-built CalendarDatasource (skips catalog load when set).
        video_catalog_path: Optional VideoCatalog SQLite path for video overlay bars.
        block: If True, run ``app.exec_()`` when this call created the QApplication.
    """
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    if datasource is None:
        from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog

        catalog = get_or_create_catalog(db_path=catalog_path, pickled_data_path=pickled_data_path)
        if discovery_dirs is not None:
            catalog.discover_and_upsert(discovery_dirs)
        df = catalog.load_calendar_dataframe()
        video_df = None
        if video_catalog_path is not None and Path(video_catalog_path).exists():
            from phoofflineeeganalysis.analysis.video_catalog import VideoCatalog

            video_df = VideoCatalog(video_catalog_path).load_calendar_dataframe()
        datasource = CalendarDatasource(xdf_stream_infos_df=df, video_infos_df=video_df)

    window = SessionCalendarWindow(datasource=datasource)
    window.show()
    if created_app and block:
        app.exec_()
    return window


if __name__ == "__main__":
    launch_session_calendar(block=True)
