# Change: Add offline analysis manager application

## Why
Offline EEG and multi-modality workflows are currently split across thin root scripts (`run_session_calendar.py`, `run_discover_sessions.py`, `main_analyze_run.py`) with duplicated hard-coded paths and a separate video catalog CLI (`pho-video-catalog`). Analysts need a single PyQt manager application that hosts the session calendar alongside session discovery, video metadata discovery/reporting/consolidation, video calendar overlays, EEG processing launch with live status updates, and centralized path configuration.

## What Changes
- Add a centralized path configuration loader (`AppPaths`) backed by `config/offline_analysis_paths.yaml` (with optional gitignored `*.local.yaml` overrides and environment overrides) so committed launchers have no hard-coded drive paths.
- Refactor `CustomCalendarWidget.py` to extract an embeddable `SessionCalendarPanel(QWidget)` that supports both EEG sessions and an optional toggleable video-interval overlay (with day-list rows for both sessions and videos), while preserving `launch_session_calendar(...)` for standalone/notebook use.
- Add `VideoCatalog.load_calendar_dataframe()` to expose video intervals in the calendar-compatible DataFrame schema.
- Add a new `OfflineAnalysisManagerWindow(QMainWindow)` shell and `launch_offline_analysis_manager(...)` entrypoint hosting the embedded calendar panel, menus/toolbar, background `QThread` workers for discovery, video catalog operations, and `process_XDFs_main` processing with live catalog refresh, a path configuration dialog, and a job log dock.
- Wire `run_offline_analysis_manager.py`, `run_session_calendar.py` (deprecation alias), and the `phoofflineeeganalysis` console script to launch the manager; update `SESSION_CALENDAR.md` and `README.md`.

## Impact
- Affected specs:
  - `offline-analysis-manager` (new capability)
  - `session-calendar` (embeddable panel + optional video overlay)
  - `video-catalog` (calendar-shaped DataFrame export + manager-triggered actions)
- Affected code:
  - New: `config/offline_analysis_paths.yaml`
  - New: `src/phoofflineeeganalysis/analysis/app_paths.py`
  - New: `src/phoofflineeeganalysis/analysis/UI/offline_analysis_manager.py`
  - New: `run_offline_analysis_manager.py`
  - Update: `src/phoofflineeeganalysis/analysis/video_catalog.py`
  - Update: `src/phoofflineeeganalysis/analysis/UI/CustomCalendarWidget.py`
  - Update: `src/phoofflineeeganalysis/UI/CustomCalendarWidget.py` and `src/phoofflineeeganalysis/UI/__init__.py`
  - Update: `src/phoofflineeeganalysis/__init__.py`
  - Update: `run_session_calendar.py`, `run_discover_sessions.py`
  - Update: `src/phoofflineeeganalysis/analysis/SESSION_CALENDAR.md`, `README.md`, `.gitignore`
- Existing APIs (`launch_session_calendar`, `pho-video-catalog`, `run_discover_sessions.py`) remain backwards-compatible.
