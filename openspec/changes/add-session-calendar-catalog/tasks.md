## 1. Catalog
- [x] 1.1 Add `session_catalog.py` with SQLite schema and upsert API
- [x] 1.2 Implement `discover_and_upsert` via HistoricalData file discovery + filename datetimes
- [x] 1.3 Implement `load_calendar_dataframe` with rel duration columns for the calendar widget

## 2. Processing hooks
- [x] 2.1 Upsert `computing`/`computed`/`exported`/`failed` from `process_XDFs_main`
- [x] 2.2 Record artifact paths and timestamps on successful export/compute

## 3. PyQt calendar UI
- [x] 3.1 Status-colored day painting; remove binge/debug leftovers
- [x] 3.2 Day selection → session detail list
- [x] 3.3 `SessionCalendarWindow` + `launch_session_calendar` entrypoint
- [x] 3.4 Fix import docs / notebook-compatible re-export

## 4. Docs / smoke
- [x] 4.1 Document discover → calendar → process → refresh workflow
