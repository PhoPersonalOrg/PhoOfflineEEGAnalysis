"""Composable Manager Home subwidgets."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.ui.widgets.day_detail import DayDetailPanel, DayRecordRowWidget
from phoofflineeeganalysis.offline_data_manager.ui.widgets.job_log_dock import JobLogPanel
from phoofflineeeganalysis.offline_data_manager.ui.widgets.left_rail import LeftRailPanel
from phoofflineeeganalysis.offline_data_manager.ui.widgets.month_calendar import MonthCalendarPanel, StyledMonthCalendar
from phoofflineeeganalysis.offline_data_manager.ui.widgets.toolbar import ManagerToolBar

__all__ = [
    "DayDetailPanel",
    "DayRecordRowWidget",
    "JobLogPanel",
    "LeftRailPanel",
    "ManagerToolBar",
    "MonthCalendarPanel",
    "StyledMonthCalendar",
]
