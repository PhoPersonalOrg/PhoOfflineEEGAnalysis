## Why
Analysts need to browse all recorded EEG sessions on a calendar before (and after) processing. Today there is no durable session index—discovery is filesystem globs, and the PyQt calendar prototype only works after a full `xdf_stream_infos_df` is built in memory.

## What Changes
- Add a SQLite session catalog populated by lightweight XDF discovery and updated during `process_XDFs_main`.
- Complete the PyQt month-grid calendar (`CustomDataDisplayingCalendar`) to show status-colored sessions, a day detail list, and a standalone launcher.
- Expose APIs to discover/upsert sessions and load a calendar-shaped DataFrame without full EEG processing.
- Browse/status only in v1 (no process-launch from the UI).

## Impact
- Affected specs: `session-calendar` (new capability)
- Affected code:
  - New: `src/phoofflineeeganalysis/analysis/session_catalog.py`
  - Update: `src/phoofflineeeganalysis/analysis/UI/CustomCalendarWidget.py`
  - Update: `main_analyze_run.py` (catalog upsert hooks)
- No breaking changes expected; feature is additive.
