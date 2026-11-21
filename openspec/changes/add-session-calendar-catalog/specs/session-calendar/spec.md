## ADDED Requirements

### Requirement: Session Catalog Persistence
The system SHALL maintain a durable SQLite session catalog keyed by canonical XDF path, populated during discovery and updated during processing.

#### Scenario: Discover without full EEG load
- **WHEN** the user runs discovery against one or more recording directories
- **THEN** each found `.xdf` SHALL be upserted with at least `xdf_path`, `xdf_filename`, `xdf_mtime`, `recording_datetime` (when parseable), and `status=discovered`

#### Scenario: Processing updates status
- **WHEN** `process_XDFs_main` successfully computes a session
- **THEN** the catalog row for that XDF path SHALL be updated to `status=computed` (or `exported` when FIF/MAT artifacts are written) with timestamps and optional artifact paths

#### Scenario: Processing failure recorded
- **WHEN** processing of an XDF fails with a handled error
- **THEN** the catalog row SHALL be updated to `status=failed` with an error message

### Requirement: Calendar-Shaped Data Export
The system SHALL expose an API that loads catalog rows as a pandas DataFrame suitable for the PyQt calendar datasource, including day-relative columns when duration/start are known.

#### Scenario: Load for calendar
- **WHEN** `load_calendar_dataframe` is called
- **THEN** the returned DataFrame SHALL include `recording_datetime`, `recording_day_date`, and relative columns usable by `CalendarDatasource` (with safe defaults when duration is unknown)

### Requirement: PyQt Session Calendar Browser
The system SHALL provide a PyQt month-grid calendar that visualizes catalog sessions by day and shows a detail list for the selected day.

#### Scenario: Status coloring
- **WHEN** sessions exist on a calendar day
- **THEN** the day cell SHALL paint session intervals colored by processing status (`discovered`, `computed`, `exported`, `failed`, etc.)

#### Scenario: Day detail list
- **WHEN** the user selects a day with one or more sessions
- **THEN** a side panel SHALL list those sessions with filename, time, duration (if known), and status

#### Scenario: Standalone launch
- **WHEN** the user calls `launch_session_calendar` with a catalog path and/or discovery directories
- **THEN** a main window SHALL open showing the calendar backed by the catalog (running discovery first when directories are provided)
