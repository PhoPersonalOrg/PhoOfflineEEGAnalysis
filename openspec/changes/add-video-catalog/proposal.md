# Change: Add video catalog ingest and consolidate

## Why
Video recordings are scattered across multiple drives and folders (eye camera, webcam, screen). There is no durable index that tracks metadata, marks conversion/cross-location duplicates, or consolidates unique files into one typed tree.

## What Changes
- Add machine-readable `config/video_sources.yaml` for multi-root discovery with categories, recursion, and excludes.
- Extend `VideoMetadataParser` / `BaseFileMetadataParser` with recursive discovery, exclude dirs, and implicit `_DUP` skipping.
- Add SQLite `VideoCatalog` with upsert, stem+duration dedupe (prefer `.mkv`), review CSV export, and consolidate-to-typed-folders.
- Add completeness gating and CAM timing-sidecar enrichment (adapted from whisper-timestamped patterns, no runtime dependency).
- Add `pho-video-catalog` CLI: `ingest`, `report`, `consolidate [--dry-run]`.

## Impact
- Affected specs: `video-catalog` (new capability)
- Affected code:
  - New: `config/video_sources.yaml`
  - New: `src/phoofflineeeganalysis/analysis/video_catalog.py`
  - New: `src/phoofflineeeganalysis/analysis/video_ingest_helpers.py`
  - New: `src/phoofflineeeganalysis/video_catalog_cli.py`
  - Update: `PhoPyLSLhelper` `file_metadata.py` / `video_metadata.py`
  - Update: `pyproject.toml` scripts
- No breaking changes expected; feature is additive.
