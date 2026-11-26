## ADDED Requirements

### Requirement: Centralized Offline Analysis Path Configuration
The system SHALL provide a YAML-backed path configuration (`AppPaths`) with local override and environment variable layering so launchers and the manager application do not hard-code machine-specific drive paths.

#### Scenario: Load default and local override config
- **WHEN** `AppPaths.load()` is called with default arguments
- **THEN** it SHALL load `config/offline_analysis_paths.yaml`, overlay `config/offline_analysis_paths.local.yaml` if present, and apply environment variable overrides

#### Scenario: Save user-edited paths without dirtying committed defaults
- **WHEN** the user saves path changes from the manager configuration dialog
- **THEN** the system SHALL write the updated paths to `config/offline_analysis_paths.local.yaml` and reload active catalogs

### Requirement: Offline Analysis Manager Shell
The system SHALL provide a PyQt `OfflineAnalysisManagerWindow(QMainWindow)` and `launch_offline_analysis_manager` entrypoint that embeds `SessionCalendarPanel` as its central view and exposes menus, status bar feedback, and a job log dock.

#### Scenario: Launch manager application
- **WHEN** the user runs `run_offline_analysis_manager.py`, `run_session_calendar.py`, or the `phoofflineeeganalysis` console script
- **THEN** `OfflineAnalysisManagerWindow` SHALL open with the embedded session calendar populated from the configured `SessionCatalog` and `VideoCatalog`

### Requirement: Background Job Execution and Live Status Refresh
The system SHALL execute session discovery, video catalog actions (ingest, report, consolidate), and EEG processing (`process_XDFs_main`) on background `QThread` workers without blocking the Qt UI thread.

#### Scenario: Discover sessions from UI
- **WHEN** the user triggers **Sessions -> Discover / Refresh catalog**
- **THEN** a background worker SHALL run `SessionCatalog.discover_and_upsert` on the configured recording directories, log counts to the job log, and refresh the calendar view

#### Scenario: Process recent EEG sessions with live status
- **WHEN** the user triggers **Sessions -> Process recent...**
- **THEN** a background worker SHALL invoke `SpectogramPlottingHelper.process_XDFs_main` with `retain_in_memory=False` and the active `SessionCatalog`, while the UI periodically reloads the calendar DataFrame so status transitions appear live

#### Scenario: Run video catalog actions from UI
- **WHEN** the user triggers **Video -> Ingest**, **Video -> Report**, or **Video -> Consolidate...**
- **THEN** a background worker SHALL invoke the corresponding `VideoCatalog` API, log results to the job log dock, and refresh the video overlay on the calendar
