"""Month calendar workspace with EEG status bars, multimodal media strips, navigation header, and legend."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from PyQt5.QtCore import QDate, QDateTime, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import (
    CalendarDatasource,
    CustomDataDisplayingCalendar,
    _ensure_utc_day,
)
from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens


class StyledMonthCalendar(CustomDataDisplayingCalendar):
    """Subclass of ``CustomDataDisplayingCalendar`` adding crisp focus border and DesignTokens colors."""

    def __init__(self, datasource: Optional[CalendarDatasource] = None, parent: Optional[QWidget] = None, show_video_overlay: bool = True):
        super().__init__(datasource=datasource, parent=parent, show_video_overlay=show_video_overlay)
        self.setNavigationBarVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setGridVisible(True)


    def paintCell(self, painter, rect, date) -> None:
        super().paintCell(painter, rect, date)
        if date == self.selectedDate():
            painter.save()
            pen = QPen(DesignTokens.calendar_selection_border_qcolor(), 2.0)
            painter.setPen(pen)
            inset = QRectF(rect.left() + 1.0, rect.top() + 1.0, max(2.0, rect.width() - 2.0), max(2.0, rect.height() - 2.0))
            painter.drawRect(inset)
            painter.restore()


class MonthCalendarPanel(QFrame):
    """Center workspace hosting month navigation, status/modality legend strip, and ``StyledMonthCalendar``."""

    daySelected = pyqtSignal(object)  # datetime UTC midnight or None

    def __init__(self, datasource: Optional[CalendarDatasource] = None, parent: Optional[QWidget] = None, show_media_overlay: bool = True):
        super().__init__(parent=parent)
        self.setObjectName("MonthCalendarPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(DesignTokens.Spacing.MD, DesignTokens.Spacing.MD, DesignTokens.Spacing.MD, DesignTokens.Spacing.MD)
        root.setSpacing(DesignTokens.Spacing.SM)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(DesignTokens.Spacing.SM)

        self.month_title_label = QLabel("", self)
        self.month_title_label.setObjectName("PanelTitle")
        nav_row.addWidget(self.month_title_label)
        nav_row.addStretch(1)

        self.btn_prev_month = QPushButton("< Prev", self)
        self.btn_prev_month.clicked.connect(self._on_prev_month)
        nav_row.addWidget(self.btn_prev_month)

        self.btn_today = QPushButton("Today", self)
        self.btn_today.clicked.connect(self._on_today)
        nav_row.addWidget(self.btn_today)

        self.btn_next_month = QPushButton("Next >", self)
        self.btn_next_month.clicked.connect(self._on_next_month)
        nav_row.addWidget(self.btn_next_month)
        root.addLayout(nav_row)

        root.addWidget(self._build_legend_bar())

        self.calendar = StyledMonthCalendar(datasource=datasource, parent=self, show_video_overlay=show_media_overlay)
        self.calendar.daySelectionChanged.connect(self.daySelected.emit)
        self.calendar.currentPageChanged.connect(self._on_page_changed)
        root.addWidget(self.calendar, stretch=1)

        self._refresh_month_header()


    def _build_legend_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("SectionCard")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(DesignTokens.Spacing.MD, 3, DesignTokens.Spacing.MD, 3)
        row.setSpacing(DesignTokens.Spacing.MD)

        status_items: Tuple[Tuple[str, str], ...] = (
            ("Discovered", DesignTokens.session_status_hex("discovered")),
            ("Computing", DesignTokens.session_status_hex("computing")),
            ("Computed", DesignTokens.session_status_hex("computed")),
            ("Exported", DesignTokens.session_status_hex("exported")),
            ("Failed", DesignTokens.session_status_hex("failed")),
        )
        media_items: Tuple[Tuple[str, str], ...] = (
            ("Eye", DesignTokens.modality_hex("EyeRecordings")),
            ("Webcam", DesignTokens.modality_hex("WebcamRecordings")),
            ("Screen", DesignTokens.modality_hex("ScreenRecordings")),
            ("Screenshot", DesignTokens.modality_hex("Screenshots")),
            ("Audio", DesignTokens.modality_hex("AudioRecordings")),
        )

        eeg_lbl = QLabel("EEG:", bar)
        eeg_lbl.setObjectName("MutedLabel")
        row.addWidget(eeg_lbl)

        for label_txt, hex_col in status_items:
            row.addLayout(self._legend_chip(bar, label_txt, hex_col))
        ## END for label_txt, hex_col in status_items...

        sep = QLabel("|  Media:", bar)
        sep.setObjectName("MutedLabel")
        row.addWidget(sep)

        for label_txt, hex_col in media_items:
            row.addLayout(self._legend_chip(bar, label_txt, hex_col))
        ## END for label_txt, hex_col in media_items...

        row.addStretch(1)
        return bar


    @staticmethod
    def _legend_chip(parent: QWidget, text: str, hex_col: str) -> QHBoxLayout:
        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        swatch = QFrame(parent)
        swatch.setFixedSize(9, 9)
        swatch.setStyleSheet(DesignTokens.swatch_qss(hex_col, radius=2))
        lbl = QLabel(text, parent)
        lbl.setObjectName("MutedLabel")
        hbox.addWidget(swatch)
        hbox.addWidget(lbl)
        return hbox


    def _on_prev_month(self) -> None:
        self.calendar.showPreviousMonth()
        self._refresh_month_header()


    def _on_next_month(self) -> None:
        self.calendar.showNextMonth()
        self._refresh_month_header()


    def _on_today(self) -> None:
        today = QDate.currentDate()
        self.calendar.setSelectedDate(today)
        self.calendar.setCurrentPage(today.year(), today.month())
        self._refresh_month_header()


    def _on_page_changed(self, _year: int, _month: int) -> None:
        self._refresh_month_header()


    def _refresh_month_header(self) -> None:
        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        dt = datetime(year, month, 1)
        self.month_title_label.setText(dt.strftime("%B %Y"))


    def selected_day_utc(self) -> datetime:
        return _ensure_utc_day(QDateTime(self.calendar.selectedDate()).toPyDateTime().replace(tzinfo=timezone.utc))


    def set_selected_day(self, day: datetime) -> None:
        qdate = QDate(day.year, day.month, day.day)
        self.calendar.setSelectedDate(qdate)
        self.calendar.setCurrentPage(day.year, day.month)
        self._refresh_month_header()


    def set_datasource(self, datasource: CalendarDatasource) -> None:
        self.calendar.set_datasource(datasource)


    def set_show_media_overlay(self, enabled: bool) -> None:
        self.calendar.set_show_video_overlay(enabled)


def create_preview_month_calendar() -> MonthCalendarPanel:
    from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, build_populated_sample_service

    tmp_root = Path(tempfile.mkdtemp(prefix="pho_odm_cal_preview_"))
    svc = build_populated_sample_service(tmp_root)
    payload = svc.calendar_payload()
    panel = MonthCalendarPanel(datasource=payload["datasource"])
    panel.setStyleSheet(DesignTokens.app_stylesheet())
    panel.set_selected_day(SAMPLE_ANCHOR_DAY)
    panel.resize(780, 560)
    return panel


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    preview = create_preview_month_calendar()
    preview.show()
    sys.exit(app.exec_())
