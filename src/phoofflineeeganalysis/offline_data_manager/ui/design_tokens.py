"""Centralized charcoal-slate design tokens and QSS helpers for Pho Offline Data Manager."""

from __future__ import annotations

from PyQt5.QtGui import QColor, QFont

from phoofflineeeganalysis.offline_data_manager.media_modalities import MODALITY_CATEGORY_HEX, SESSION_STATUS_HEX


class DesignTokens:
    """Semantic palette, typography, spacing, and stylesheet builders (zero scattered hex in widgets)."""

    class Colors:
        BG_APP = "#181D23"
        BG_PANEL = "#212830"
        BG_PANEL_ALT = "#262E37"
        BG_ELEVATED = "#2D3742"
        BG_HOVER = "#34404E"
        BG_INPUT = "#151A20"
        BG_SELECTED_ROW = "#2A3B52"
        BORDER_SUBTLE = "#313C47"
        BORDER_STRONG = "#445466"
        BORDER_FOCUS = "#5B8DEF"

        TEXT_PRIMARY = "#E6EDF3"
        TEXT_SECONDARY = "#9BA8B5"
        TEXT_MUTED = "#6E7B88"

        ACCENT_PRIMARY = "#4C82E6"
        ACCENT_PRIMARY_HOVER = "#6092EE"
        ACCENT_PRIMARY_DISABLED = "#2B3A52"

        ACCENT_COMPUTE = "#238636"
        ACCENT_COMPUTE_HOVER = "#2EA043"
        ACCENT_COMPUTE_BORDER = "#3FB950"
        ACCENT_COMPUTE_DISABLED = "#22342A"

        STATUS_ONLINE = "#2EA043"
        STATUS_WARNING = "#D29922"
        STATUS_DANGER = "#E5534B"

        CHIP_BG = "#2A3440"
        CHIP_BORDER = "#3E4C5C"
        CHIP_TEXT = "#C8D4E0"

    class Spacing:
        XS = 4
        SM = 6
        MD = 8
        LG = 12
        XL = 16

    class Radii:
        SM = 4
        MD = 6
        LG = 8

    class Fonts:
        UI_FAMILIES = ("Segoe UI", "Inter", "Arial")
        MONO_FAMILIES = ("Cascadia Mono", "Consolas", "Courier New")
        SIZE_TITLE = 13
        SIZE_SUBTITLE = 11
        SIZE_BODY = 10
        SIZE_CAPTION = 9


    @classmethod
    def ui_font(cls, point_size: int = 10, bold: bool = False) -> QFont:
        font = QFont(cls.Fonts.UI_FAMILIES[0], point_size)
        font.setBold(bold)
        return font


    @classmethod
    def mono_font(cls, point_size: int = 9, bold: bool = False) -> QFont:
        font = QFont(cls.Fonts.MONO_FAMILIES[0], point_size)
        font.setStyleHint(QFont.Monospace)
        font.setBold(bold)
        return font


    @classmethod
    def session_status_hex(cls, status: str) -> str:
        return SESSION_STATUS_HEX.get(str(status or "").lower(), SESSION_STATUS_HEX["discovered"])


    @classmethod
    def session_status_qcolor(cls, status: str, alpha: int = 165) -> QColor:
        color = QColor(cls.session_status_hex(status))
        color.setAlpha(alpha)
        return color


    @classmethod
    def modality_hex(cls, category_or_modality: str) -> str:
        return MODALITY_CATEGORY_HEX.get(str(category_or_modality or ""), MODALITY_CATEGORY_HEX["default"])


    @classmethod
    def modality_qcolor(cls, category_or_modality: str, alpha: int = 185) -> QColor:
        color = QColor(cls.modality_hex(category_or_modality))
        color.setAlpha(alpha)
        return color


    @classmethod
    def swatch_qss(cls, hex_color: str, radius: int = 3) -> str:
        return f"background-color: {hex_color}; border: 1px solid {cls.Colors.BORDER_STRONG}; border-radius: {radius}px;"


    @classmethod
    def modality_swatch_qss(cls, category_or_modality: str) -> str:
        return cls.swatch_qss(cls.modality_hex(category_or_modality), radius=3)


    @classmethod
    def status_badge_qss(cls, status: str) -> str:
        fg = cls.session_status_hex(status)
        return (
            f"color: {fg}; background-color: {cls.Colors.BG_INPUT}; "
            f"border: 1px solid {fg}; border-radius: {cls.Radii.SM}px; padding: 1px 6px; font-weight: 600;"
        )


    @classmethod
    def category_badge_qss(cls, category: str) -> str:
        fg = cls.modality_hex(category)
        return (
            f"color: {cls.Colors.TEXT_PRIMARY}; background-color: {cls.Colors.CHIP_BG}; "
            f"border-left: 3px solid {fg}; border-top: 1px solid {cls.Colors.CHIP_BORDER}; "
            f"border-right: 1px solid {cls.Colors.CHIP_BORDER}; border-bottom: 1px solid {cls.Colors.CHIP_BORDER}; "
            f"border-radius: {cls.Radii.SM}px; padding: 1px 6px;"
        )


    @classmethod
    def stream_chip_qss(cls) -> str:
        return (
            f"color: {cls.Colors.CHIP_TEXT}; background-color: {cls.Colors.CHIP_BG}; "
            f"border: 1px solid {cls.Colors.CHIP_BORDER}; border-radius: {cls.Radii.SM}px; padding: 1px 5px;"
        )


    @classmethod
    def health_badge_qss(cls, online: bool) -> str:
        fg = cls.Colors.STATUS_ONLINE if online else cls.Colors.STATUS_WARNING
        return (
            f"color: {fg}; background-color: {cls.Colors.BG_INPUT}; "
            f"border: 1px solid {fg}; border-radius: {cls.Radii.SM}px; padding: 1px 6px; font-weight: 600;"
        )


    @classmethod
    def calendar_selection_border_qcolor(cls) -> QColor:
        return QColor(cls.Colors.BORDER_FOCUS)


    @classmethod
    def calendar_cell_bg_qcolor(cls, is_selected: bool = False) -> QColor:
        return QColor(cls.Colors.BG_SELECTED_ROW if is_selected else cls.Colors.BG_PANEL)


    @classmethod
    def app_stylesheet(cls) -> str:
        c = cls.Colors
        r = cls.Radii
        return f"""
        QMainWindow, QDialog {{
            background-color: {c.BG_APP};
            color: {c.TEXT_PRIMARY};
        }}
        QWidget {{
            color: {c.TEXT_PRIMARY};
            font-family: "Segoe UI", "Inter", sans-serif;
            font-size: 10pt;
        }}
        QFrame#LeftRailPanel, QFrame#MonthCalendarPanel, QFrame#DayDetailPanel, QFrame#JobLogPanel {{
            background-color: {c.BG_PANEL};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.MD}px;
        }}
        QFrame#SectionCard {{
            background-color: {c.BG_PANEL_ALT};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.MD}px;
        }}
        QToolBar#ManagerToolBar {{
            background-color: {c.BG_PANEL};
            border-bottom: 1px solid {c.BORDER_SUBTLE};
            spacing: 6px;
            padding: 4px 8px;
        }}
        QToolBar#ManagerToolBar QToolButton {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.SM}px;
            padding: 4px 10px;
        }}
        QToolBar#ManagerToolBar QToolButton:hover {{
            background-color: {c.BG_HOVER};
            border-color: {c.BORDER_STRONG};
        }}
        QToolBar#ManagerToolBar QToolButton:checked {{
            background-color: {c.BG_SELECTED_ROW};
            border-color: {c.BORDER_FOCUS};
        }}
        QToolBar#ManagerToolBar QToolButton:disabled {{
            color: {c.TEXT_MUTED};
            background-color: {c.BG_PANEL};
        }}
        QMenuBar {{
            background-color: {c.BG_APP};
            color: {c.TEXT_PRIMARY};
            border-bottom: 1px solid {c.BORDER_SUBTLE};
        }}
        QMenuBar::item:selected, QMenu::item:selected {{
            background-color: {c.BG_HOVER};
        }}
        QMenu {{
            background-color: {c.BG_PANEL_ALT};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_STRONG};
        }}
        QLabel#PanelTitle {{
            color: {c.TEXT_PRIMARY};
            font-size: 11pt;
            font-weight: 600;
        }}
        QLabel#MutedLabel {{
            color: {c.TEXT_SECONDARY};
            font-size: 9pt;
        }}
        QLabel#StatsSummary {{
            color: {c.TEXT_SECONDARY};
            background-color: {c.BG_INPUT};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.SM}px;
            padding: 6px 8px;
            font-size: 9pt;
        }}
        QPushButton {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_STRONG};
            border-radius: {r.MD}px;
            padding: 5px 10px;
        }}
        QPushButton:hover {{
            background-color: {c.BG_HOVER};
        }}
        QPushButton:disabled {{
            color: {c.TEXT_MUTED};
            background-color: {c.BG_PANEL};
            border-color: {c.BORDER_SUBTLE};
        }}
        QPushButton#PrimaryButton {{
            background-color: {c.ACCENT_PRIMARY};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_FOCUS};
            font-weight: 600;
            padding: 6px 12px;
        }}
        QPushButton#PrimaryButton:hover {{
            background-color: {c.ACCENT_PRIMARY_HOVER};
        }}
        QPushButton#PrimaryButton:disabled {{
            background-color: {c.ACCENT_PRIMARY_DISABLED};
            color: {c.TEXT_MUTED};
            border-color: {c.BORDER_SUBTLE};
        }}
        QPushButton#ComputeButton {{
            background-color: {c.ACCENT_COMPUTE};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.ACCENT_COMPUTE_BORDER};
            font-weight: 600;
            padding: 6px 12px;
        }}
        QPushButton#ComputeButton:hover {{
            background-color: {c.ACCENT_COMPUTE_HOVER};
        }}
        QPushButton#ComputeButton:disabled {{
            background-color: {c.ACCENT_COMPUTE_DISABLED};
            color: {c.TEXT_MUTED};
            border-color: {c.BORDER_SUBTLE};
        }}
        QPushButton#TabFilterButton {{
            background-color: {c.BG_INPUT};
            color: {c.TEXT_SECONDARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.SM}px;
            padding: 3px 8px;
            font-size: 9pt;
        }}
        QPushButton#TabFilterButton:checked {{
            background-color: {c.BG_SELECTED_ROW};
            color: {c.TEXT_PRIMARY};
            border-color: {c.BORDER_FOCUS};
            font-weight: 600;
        }}
        QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
            background-color: {c.BG_INPUT};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.SM}px;
            padding: 4px 6px;
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
            border-color: {c.BORDER_FOCUS};
        }}
        QListWidget, QTableWidget {{
            background-color: {c.BG_INPUT};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.MD}px;
            gridline-color: {c.BORDER_SUBTLE};
        }}
        QListWidget::item {{
            border-bottom: 1px solid {c.BORDER_SUBTLE};
            padding: 2px;
        }}
        QListWidget::item:selected, QTableWidget::item:selected {{
            background-color: {c.BG_SELECTED_ROW};
            color: {c.TEXT_PRIMARY};
        }}
        QHeaderView::section {{
            background-color: {c.BG_PANEL_ALT};
            color: {c.TEXT_SECONDARY};
            border: none;
            border-right: 1px solid {c.BORDER_SUBTLE};
            border-bottom: 1px solid {c.BORDER_STRONG};
            padding: 4px 6px;
            font-weight: 600;
        }}
        QProgressBar {{
            background-color: {c.BG_INPUT};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: {r.SM}px;
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{
            background-color: {c.ACCENT_PRIMARY};
            border-radius: 3px;
        }}
        QCalendarWidget QWidget {{
            alternate-background-color: {c.BG_PANEL_ALT};
        }}
        QCalendarWidget QAbstractItemView:enabled {{
            background-color: {c.BG_INPUT};
            color: {c.TEXT_PRIMARY};
            selection-background-color: {c.BG_SELECTED_ROW};
            selection-color: {c.TEXT_PRIMARY};
        }}
        QCalendarWidget QAbstractItemView:disabled {{
            color: {c.TEXT_MUTED};
        }}
        QSplitter::handle {{
            background-color: {c.BORDER_SUBTLE};
        }}
        QStatusBar {{
            background-color: {c.BG_PANEL};
            color: {c.TEXT_SECONDARY};
            border-top: 1px solid {c.BORDER_SUBTLE};
        }}
        """


__all__ = ["DesignTokens"]
