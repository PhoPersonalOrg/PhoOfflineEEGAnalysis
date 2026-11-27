"""Launch the Pho Offline Data Manager PyQt5 desktop application."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from phoofflineeeganalysis.offline_data_manager.ui.main_window import launch_offline_data_manager


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the Pho Offline Data Manager.")
    parser.add_argument("--config", type=Path, default=None, help="Path to paths.yaml (or legacy offline_analysis_paths.yaml)")
    parser.add_argument("--discover", action="store_true", help="Run session discovery on startup")
    parser.add_argument("--legacy-ui", action="store_true", help="Open legacy OfflineAnalysisManagerWindow shell")
    args = parser.parse_args(argv)
    if args.legacy_ui:
        from phoofflineeeganalysis.analysis.UI.offline_analysis_manager import launch_offline_analysis_manager

        launch_offline_analysis_manager(config_yaml=args.config, discover_on_start=bool(args.discover), block=True)
        return 0

    launch_offline_data_manager(config_yaml=args.config, discover_on_start=bool(args.discover), block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
