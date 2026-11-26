"""Lightweight XDF discovery into the session catalog (no EEG processing)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from phoofflineeeganalysis.analysis.app_paths import AppPaths
from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Discover XDF files into the SQLite session catalog.")
    parser.add_argument("--config", type=Path, default=None, help="Path to offline_analysis_paths.yaml")
    parser.add_argument("--dir", dest="dirs", action="append", type=Path, default=None, help="Override discovery directory (repeatable)")
    args = parser.parse_args(argv)

    paths = AppPaths.load(config_yaml=args.config)
    discovery_dirs = list(args.dirs) if args.dirs else paths.existing_lab_recorder_dirs()
    if not discovery_dirs:
        raise SystemExit("No existing LabRecorder discovery directories configured. Edit config/offline_analysis_paths.local.yaml or pass --dir.")

    catalog = get_or_create_catalog(db_path=paths.resolved_session_catalog_path(), pickled_data_path=paths.pickled_data_path)
    n_upserted = catalog.discover_and_upsert(discovery_dirs, recordings_extensions=[".xdf"])
    print(f"Discovered/upserted {n_upserted} sessions -> {catalog.db_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
