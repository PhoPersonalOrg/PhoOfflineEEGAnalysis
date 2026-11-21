"""CLI for video catalog ingest, duplicate report, and consolidate.

Usage:
    pho-video-catalog ingest
    pho-video-catalog report
    pho-video-catalog consolidate --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from phoofflineeeganalysis.analysis.video_catalog import (
    VideoCatalog,
    default_sources_yaml_path,
    default_video_catalog_path,
    load_source_specs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pho-video-catalog", description="Discover, dedupe, and consolidate video recordings.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite catalog path (default: output/video_catalog.sqlite)")
    parser.add_argument("--sources", type=Path, default=None, help="video_sources.yaml path")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Discover sources, upsert metadata, recompute duplicates")
    ingest.add_argument("--force-rebuild", action="store_true", help="Ignore per-folder video metadata caches")
    ingest.add_argument("--include-incomplete", action="store_true", help="Do not skip still-recording / incomplete files")
    ingest.add_argument("--no-dedupe", action="store_true", help="Skip recompute_duplicates after upsert")

    report = sub.add_parser("report", help="Print KEEP/DUP groups and optionally export CSV")
    report.add_argument("--csv", type=Path, default=None, help="Optional review CSV export path")

    cons = sub.add_parser("consolidate", help="Copy canonical videos into typed RECORDINGS subfolders")
    cons.add_argument("--dest", type=Path, default=None, help="Destination root (default from YAML)")
    cons.add_argument("--dry-run", action="store_true", help="Print planned copies without writing")

    return parser


def _catalog_from_args(args: argparse.Namespace) -> VideoCatalog:
    db = args.db if args.db is not None else default_video_catalog_path()
    return VideoCatalog(db)


def cmd_ingest(args: argparse.Namespace) -> int:
    catalog = _catalog_from_args(args)
    sources = args.sources if args.sources is not None else default_sources_yaml_path()
    print(f"Catalog: {catalog.db_path}")
    print(f"Sources: {sources}")
    counts = catalog.discover_and_upsert(
        sources,
        force_rebuild=bool(args.force_rebuild),
        skip_incomplete=not bool(args.include_incomplete),
    )
    print(
        f"Ingest: sources={counts['sources']} found={counts['found']} upserted={counts['upserted']} "
        f"skipped_incomplete={counts['skipped_incomplete']} missing_roots={counts['skipped_missing_roots']}"
    )
    if not args.no_dedupe:
        dedupe = catalog.recompute_duplicates()
        print(f"Dedupe: groups={dedupe['groups']} duplicate_rows={dedupe['duplicate_rows']}")
    ## END if not args.no_dedupe....

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    catalog = _catalog_from_args(args)
    print(f"Catalog: {catalog.db_path}")
    df = catalog.load_videos_df()
    n = len(df)
    n_canon = int(df["is_canonical"].sum()) if n and "is_canonical" in df.columns else 0
    n_dup = int(df["is_duplicate"].sum()) if n and "is_duplicate" in df.columns else 0
    print(f"Videos: {n}  canonical={n_canon}  duplicates={n_dup}")
    catalog.print_duplicate_report()
    if args.csv is not None:
        out = catalog.export_review_csv(args.csv)
        print(f"Wrote review CSV: {out}")
    ## END if args.csv....

    return 0


def cmd_consolidate(args: argparse.Namespace) -> int:
    catalog = _catalog_from_args(args)
    sources = args.sources if args.sources is not None else default_sources_yaml_path()
    dest: Optional[Path] = args.dest
    if dest is None:
        _specs, dest = load_source_specs(sources)
    print(f"Catalog: {catalog.db_path}")
    print(f"Dest: {dest}  dry_run={bool(args.dry_run)}")
    result = catalog.consolidate(dest_root=dest, dry_run=bool(args.dry_run))
    print(
        f"Consolidate: planned={result['planned']} copied={result['copied']} "
        f"skipped={result['skipped']} failed={result['failed']}"
    )
    return 0 if result["failed"] == 0 else 1


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        return cmd_ingest(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "consolidate":
        return cmd_consolidate(args)
    parser.error(f"Unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
