"""PhoOfflineEEGAnalysis package entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    """Console entrypoint for ``phoofflineeeganalysis`` — launches the Offline Analysis Manager."""
    parser = argparse.ArgumentParser(prog="phoofflineeeganalysis", description="Launch the Pho Offline Analysis Manager.")
    parser.add_argument("--config", type=Path, default=None, help="Path to offline_analysis_paths.yaml")
    parser.add_argument("--discover", action="store_true", help="Run session discovery on startup")
    args = parser.parse_args(argv)

    from phoofflineeeganalysis.analysis.UI.offline_analysis_manager import launch_offline_analysis_manager

    launch_offline_analysis_manager(config_yaml=args.config, discover_on_start=bool(args.discover), block=True)
    return 0
