# Design: Offline Analysis Manager

## Context
`SessionCalendarWindow` currently acts as a standalone month-grid viewer with timeline handoff, reveal-in-Explorer, and copy-path actions. `SessionCatalog` and `VideoCatalog` are independent SQLite catalogs, and `SpectogramPlottingHelper.process_XDFs_main` lives in `main_analyze_run.py`. Generalizing `run_session_calendar.py` into a central manager requires separating the reusable calendar panel from the application shell, isolating long-running work off the Qt main thread, and centralizing machine-specific paths.

## Goals / Non-Goals
- **Goals:**
  - Single PyQt `OfflineAnalysisManagerWindow` hosting an embeddable `SessionCalendarPanel`.
  - Non-blocking background execution (`QThread`) for session discovery, video ingest/report/consolidate, and EEG processing (`process_XDFs_main`), with live status bar updates and a job log dock.
  - Dual-catalog calendar display: EEG session status bars plus optional toggleable video category bars, and day-list rows for both.
  - Centralized YAML-backed `AppPaths` configuration with local override file support (`*.local.yaml`) and an in-app path editor dialog.
- **Non-Goals (v1):**
  - Embedding `pyPhoTimeline` inside the manager (keep IPC/spawn handoff).
  - Embedding Panel/HoloViews spectrogram browsers in Qt.
  - Merging `SessionCatalog` and `VideoCatalog` into a single SQLite database.

## Key Decisions
1. **Shell vs. Embedded Panel:** Extract `SessionCalendarPanel(QWidget)` from `SessionCalendarWindow`. `SessionCalendarWindow(QMainWindow)` wraps `SessionCalendarPanel` for backwards-compatible notebook/standalone launch. `OfflineAnalysisManagerWindow(QMainWindow)` embeds `SessionCalendarPanel` as its central widget.
2. **Dual-Catalog Calendar Painting:** Keep `SessionCatalog` and `VideoCatalog` separate. `CalendarDatasource` accepts an optional `video_infos_df` (produced by `VideoCatalog.load_calendar_dataframe()`). `CustomDataDisplayingCalendar.paintCell` paints EEG session bars on the left/main width and thinner category-colored video bars along the right edge when video overlay is enabled, avoiding clutter.
3. **Background Workers & Live Refresh:** Use a generic `QObject` worker moved to a `QThread` for discover, video ingest/report/consolidate, and EEG processing. While a processing job runs, a `QTimer` (2 s interval) reloads `SessionCatalog.load_calendar_dataframe()` and refreshes the calendar so status color transitions (`discovered` → `computing` → `computed`/`exported`/`failed`) appear live. Import `main_analyze_run` lazily inside the process worker and default `retain_in_memory=False`.
4. **Centralized Path Config:** `config/offline_analysis_paths.yaml` stores schema defaults (empty or repo-relative paths, no hard-coded drive letters). `AppPaths.load()` layers `config/offline_analysis_paths.yaml` → `config/offline_analysis_paths.local.yaml` (gitignored) → environment variables (`PHO_LAB_RECORDER_DIRS`, `PHO_PICKLED_DATA_PATH`, `PHO_SESSION_CATALOG_PATH`, `PHO_VIDEO_CATALOG_PATH`, `PHO_VIDEO_SOURCES_YAML`). Saving from the UI writes to `offline_analysis_paths.local.yaml` so user-specific paths are never dirty in git.
5. **Entry Points:** Add `run_offline_analysis_manager.py`, turn `run_session_calendar.py` into a deprecation alias that launches the manager, and wire `phoofflineeeganalysis:main` to `launch_offline_analysis_manager`.
