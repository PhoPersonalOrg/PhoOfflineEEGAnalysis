#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PyQt month-grid calendar for recorded EEG sessions.

Preferred import::

    from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import (
        CalendarDatasource, CustomDataDisplayingCalendar, launch_session_calendar,
    )
"""

from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Union

import pandas as pd
from attrs import define, field
from PyQt5.QtCore import QDate, QDateTime, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
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


def _ensure_utc_day(day_date: datetime) -> datetime:
    if day_date.tzinfo is None:
        day_date = day_date.replace(tzinfo=timezone.utc)
    else:
        day_date = day_date.astimezone(timezone.utc)
    return day_date.replace(hour=0, minute=0, second=0, microsecond=0)


@define(slots=False)
class CalendarDatasource:
    """Calendar data adapter over a session / stream-infos DataFrame.

    from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import CalendarDatasource
    """

    xdf_stream_infos_df: pd.DataFrame = field()
    eeg_only_stream_infos_df: pd.DataFrame = field(init=False)

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


class CustomDataDisplayingCalendar(QCalendarWidget):
    """Month-grid calendar that paints session intervals colored by processing status."""

    daySelectionChanged = pyqtSignal(object)  # datetime day (UTC midnight) or None

    def __init__(self, datasource=None, parent=None):
        super().__init__(parent=parent)
        self._datasource = datasource
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
        if found_df is None or len(found_df) == 0:
            return

        desired_width = rect.width() * 0.90
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


class SessionCalendarWindow(QMainWindow):
    """Main window: calendar + day session list."""

    def __init__(self, datasource: Optional[CalendarDatasource] = None, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("EEG Session Calendar")
        self.resize(900, 560)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        self.calendar = CustomDataDisplayingCalendar(datasource=datasource)
        splitter.addWidget(self.calendar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.day_label = QLabel("Select a day")
        self.day_label.setWordWrap(True)
        right_layout.addWidget(self.day_label)
        self.session_list = QListWidget()
        self.session_list.itemDoubleClicked.connect(self._on_session_activated)
        right_layout.addWidget(self.session_list)
        self.open_timeline_btn = QPushButton("Open in Timeline")
        self.open_timeline_btn.clicked.connect(self._on_open_timeline_clicked)
        right_layout.addWidget(self.open_timeline_btn)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.calendar.daySelectionChanged.connect(self._on_day_selected)
        if datasource is not None:
            self.calendar.set_datasource(datasource)

    def set_datasource(self, datasource: CalendarDatasource):
        self.calendar.set_datasource(datasource)
        self._on_day_selected(_ensure_utc_day(QDateTime(self.calendar.selectedDate()).toPyDateTime().replace(tzinfo=timezone.utc)))

    def _on_day_selected(self, day_date: Optional[datetime]):
        self.session_list.clear()
        if day_date is None or self.calendar.datasource is None:
            self.day_label.setText("Select a day")
            return

        records = self.calendar.datasource.get_records_for_day(day_date)
        self.day_label.setText(f"{day_date.strftime('%Y-%m-%d')} — {len(records)} session(s)")
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
                    "xdf_path": getattr(a_row, "xdf_path", None),
                    "recording_datetime": rec_dt,
                    "duration_sec": dur_val,
                    "status": status,
                },
            )
            self.session_list.addItem(item)
        ## END for a_row in records.itertuples()....

    def _on_open_timeline_clicked(self):
        item = self.session_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Open in Timeline", "Select a session in the list first.")
            return
        self._on_session_activated(item)

    def _on_session_activated(self, item: QListWidgetItem):
        payload = item.data(Qt.UserRole)
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

            mode = open_session_in_timeline(xdf_path, start=recording_datetime, duration_sec=duration_sec)
            if mode == "ipc":
                self.statusBar().showMessage(f"Zoomed/loaded in running timeline: {Path(xdf_path).name}", 5000)
            else:
                self.statusBar().showMessage(f"Started offline timeline for: {Path(xdf_path).name}", 5000)
        except Exception as e:
            QMessageBox.warning(self, "Open in Timeline", str(e))


def launch_session_calendar(catalog_path: Optional[Union[str, Path]] = None, discovery_dirs: Optional[Sequence[Union[str, Path]]] = None, pickled_data_path: Optional[Path] = None, datasource: Optional[CalendarDatasource] = None, block: bool = True) -> SessionCalendarWindow:
    """Open the session calendar window, optionally discovering into the SQLite catalog first.

    Args:
        catalog_path: SQLite catalog path (default under PICKLED_COLLECTION/session_catalog).
        discovery_dirs: If provided, scan these dirs and upsert into the catalog before showing.
        pickled_data_path: Used when resolving the default catalog path.
        datasource: Optional pre-built CalendarDatasource (skips catalog load when set).
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
        datasource = CalendarDatasource(xdf_stream_infos_df=df)

    window = SessionCalendarWindow(datasource=datasource)
    window.show()
    if created_app and block:
        app.exec_()
    return window


if __name__ == "__main__":
    launch_session_calendar(block=True)
