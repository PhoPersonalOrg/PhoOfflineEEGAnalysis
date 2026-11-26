# PhoOfflineEEGAnalysis

Offline EEG and multi-modality analysis toolkit for Emotiv Epoc/Epoc X recordings, session discovery, video metadata cataloging, and spectrogram exploration.

## Offline Analysis Manager (PyQt GUI)

Launch the unified manager application (session calendar, discovery, video catalog ingest/report/consolidate, video calendar overlay, and EEG processing):

```bash
python run_offline_analysis_manager.py
# or, if installed in the active environment:
phoofflineeeganalysis
```

Configure machine-specific paths via **Config → Edit Paths...** (writes to gitignored `config/offline_analysis_paths.local.yaml`) or environment variables (`PHO_LAB_RECORDER_DIRS`, `PHO_PICKLED_DATA_PATH`, `PHO_SESSION_CATALOG_PATH`, `PHO_VIDEO_CATALOG_PATH`, `PHO_VIDEO_SOURCES_YAML`).

See `src/phoofflineeeganalysis/analysis/SESSION_CALENDAR.md` for full workflow details and standalone notebook usage.

## CLI Utilities

- `python run_discover_sessions.py` — lightweight XDF discovery into the SQLite session catalog
- `pho-video-catalog ingest|report|consolidate` — video metadata catalog CLI
