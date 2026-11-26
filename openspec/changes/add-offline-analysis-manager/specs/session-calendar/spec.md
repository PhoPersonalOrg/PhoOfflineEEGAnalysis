## ADDED Requirements

### Requirement: Embeddable Session Calendar Panel
The system SHALL expose `SessionCalendarPanel(QWidget)` containing the month-grid calendar, day detail list, and timeline/reveal/copy actions so it can be embedded inside `OfflineAnalysisManagerWindow` while `SessionCalendarWindow` and `launch_session_calendar` remain available for standalone/notebook usage.

#### Scenario: Embed calendar panel in manager window
- **WHEN** `OfflineAnalysisManagerWindow` instantiates `SessionCalendarPanel`
- **THEN** the panel SHALL render the month-grid calendar and day detail list and preserve double-click timeline handoff and context-menu reveal/copy actions

### Requirement: Toggleable Video Overlay on Calendar
The system SHALL support an optional video interval layer on `CalendarDatasource` and `CustomDataDisplayingCalendar` that can be toggled on or off without altering EEG session status bars.

#### Scenario: Display videos alongside sessions
- **WHEN** a video calendar DataFrame is provided and the video overlay is enabled
- **THEN** calendar cells SHALL paint distinct category-colored video interval bars alongside EEG session bars, and the day detail list SHALL include video entries with reveal-in-Explorer and copy-path actions

#### Scenario: Toggle video overlay off
- **WHEN** the user disables the video overlay toggle
- **THEN** calendar cells SHALL hide video bars while continuing to paint EEG session status bars
