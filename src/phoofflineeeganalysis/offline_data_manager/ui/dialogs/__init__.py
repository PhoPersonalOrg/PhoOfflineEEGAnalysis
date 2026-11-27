"""Configuration and action dialogs for Pho Offline Data Manager."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.ui.dialogs.computation_options_dialog import ComputationOptionsDialog
from phoofflineeeganalysis.offline_data_manager.ui.dialogs.consolidate_media_dialog import ConsolidateMediaDialog
from phoofflineeeganalysis.offline_data_manager.ui.dialogs.process_sessions_dialog import ProcessSessionsDialog
from phoofflineeeganalysis.offline_data_manager.ui.dialogs.sources_paths_dialog import SourcesPathsDialog

__all__ = [
    "ComputationOptionsDialog",
    "ConsolidateMediaDialog",
    "ProcessSessionsDialog",
    "SourcesPathsDialog",
]
