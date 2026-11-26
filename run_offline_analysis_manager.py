"""Launch the PyQt Offline Analysis Manager application."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from phoofflineeeganalysis.analysis.UI.offline_analysis_manager import launch_offline_analysis_manager


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the Pho Offline Analysis Manager.")
    parser.add_argument("--config", type=Path, default=None, help="Path to offline_analysis_paths.yaml")
    parser.add_argument("--discover", action="store_true", help="Run session discovery on startup")
    args = parser.parse_args(argv)
    launch_offline_analysis_manager(config_yaml=args.config, discover_on_start=bool(args.discover), block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
