# Pho Offline Data Manager (`phoofflineeeganalysis.offline_data_manager`)

Self-contained multimodal offline data manager backend + native PyQt5 desktop UI for Emotiv Epoc X research workflows.

## Package Layout

- `app_paths.py` — layered configuration (`config/offline_data_manager/paths.yaml` -> `paths.local.yaml` -> `PHO_*` env vars), backwards-compatible with legacy `config/offline_analysis_paths.yaml` and `video_*` keys.
- `media_modalities.py` — modality categories (`EyeRecordings`, `WebcamRecordings`, `ScreenRecordings`, `Screenshots`, `AudioRecordings`), file extension sets, stream-to-chip mapping (`EEG`, `Motion`, `CQ`, `Markers`), and color hex constants.
- `media_catalog.py` — SQLite `MediaCatalog` (`VideoCatalog` alias) supporting video (`VideoMetadataParser`), screenshots, and audio discovery, duplicate clustering, category/kind calendar filtering, and consolidation.
- `session_catalog.py` — SQLite `SessionCatalog` storing `.xdf` sessions, processing statuses (`discovered`, `computing`, `computed`, `exported`, `failed`), `source_root`, and `streams_json` stream chips.
- `source_registry.py` — multi-folder LabRecorder and media root health checks (`Online` vs `Offline/Missing`), catalog counts, and YAML CRUD.
- `service.py` — `OfflineDataManagerService` UI contract (`stats`, `list_sources`, `save_paths`, `discover_sessions`, `enrich_session_streams`, `ingest_media`, `duplicate_report`, `consolidate_media`, `process_sessions`, `calendar_payload`, `day_records`, `open_in_timeline`).
- `ui/` — charcoal-slate native PyQt5 desktop UI (`PhoOfflineDataManagerWindow`, `LeftRailPanel`, `MonthCalendarPanel`, `DayDetailPanel`, `JobLogPanel`, `ManagerToolBar`, `SourcesPathsDialog`, `ProcessSessionsDialog`, `ConsolidateMediaDialog`).

## Launching

```bash
uv run --frozen python run_offline_analysis_manager.py
# or installed script:
phoofflineeeganalysis
# preview with sample multimodal fixtures (or capture screenshot):
uv run --frozen python -m phoofflineeeganalysis.offline_data_manager.ui.main_window --grab output/manager_preview.png
```
