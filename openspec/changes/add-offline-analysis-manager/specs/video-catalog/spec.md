## ADDED Requirements

### Requirement: Calendar-Shaped Video DataFrame Export
The system SHALL expose `VideoCatalog.load_calendar_dataframe()` returning a pandas DataFrame compatible with `CalendarDatasource` video overlay rendering.

#### Scenario: Load videos for calendar overlay
- **WHEN** `VideoCatalog.load_calendar_dataframe()` is called
- **THEN** the returned DataFrame SHALL include `video_path`, `filename`, `category`, `recording_datetime`, `recording_day_date`, `duration_sec`, `first_timestamp_rel`, `duration_rel`, and `is_duplicate`, defaulting to canonical non-duplicate rows unless requested otherwise
