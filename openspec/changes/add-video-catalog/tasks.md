## 1. Config and discovery
- [x] 1.1 Add `config/video_sources.yaml` from VIDEO_FILES.md video sections
- [x] 1.2 Extend `parse_filesystem_folder` / `parse_video_folder` with recursive, exclude_dirs, skip `_DUP`

## 2. Ingest helpers and catalog
- [x] 2.1 Add completeness + CAM sidecar helpers
- [x] 2.2 Implement `VideoCatalog` SQLite schema, upsert, dedupe, consolidate, review CSV
- [x] 2.3 Add `pho-video-catalog` CLI (`ingest`, `report`, `consolidate`)

## 3. Specs
- [x] 3.1 Write `video-catalog` capability delta with scenarios
