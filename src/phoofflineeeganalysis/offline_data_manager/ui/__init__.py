"""PyQt5 desktop UI package for Pho Offline Data Manager."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens
from phoofflineeeganalysis.offline_data_manager.ui.dialogs import (
    ComputationOptionsDialog,
    ConsolidateMediaDialog,
    ProcessSessionsDialog,
    SourcesPathsDialog,
)
from phoofflineeeganalysis.offline_data_manager.ui.main_window import (
    PhoOfflineDataManagerWindow,
    create_preview_main_window,
    launch_offline_data_manager,
)

__all__ = [
    "ComputationOptionsDialog",
    "ConsolidateMediaDialog",
    "DesignTokens",
    "PhoOfflineDataManagerWindow",
    "ProcessSessionsDialog",
    "SourcesPathsDialog",
    "create_preview_main_window",
    "launch_offline_data_manager",
]
