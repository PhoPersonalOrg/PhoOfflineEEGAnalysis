## 1. OpenSpec Proposal
- [x] 1.1 Draft `proposal.md`, `design.md`, `tasks.md`, and delta specs under `openspec/changes/add-offline-analysis-manager/`
- [ ] 1.2 Validate with `openspec validate add-offline-analysis-manager --strict` (`openspec` CLI not installed in current PATH)

## 2. Centralized Path Config (`AppPaths`)
- [x] 2.1 Add `config/offline_analysis_paths.yaml` with schema defaults and ignore `config/*.local.yaml` in `.gitignore`
- [x] 2.2 Implement `src/phoofflineeeganalysis/analysis/app_paths.py` (`AppPaths`, YAML load/save, local override + env var layering)
- [x] 2.3 Update `run_discover_sessions.py` to load paths from `AppPaths` instead of hard-coded paths

## 3. Embeddable Calendar Panel & Video Overlay
- [x] 3.1 Add `VideoCatalog.load_calendar_dataframe()` in `src/phoofflineeeganalysis/analysis/video_catalog.py`
- [x] 3.2 Extract `SessionCalendarPanel(QWidget)` in `src/phoofflineeeganalysis/analysis/UI/CustomCalendarWidget.py` while preserving `SessionCalendarWindow` and `launch_session_calendar`
- [x] 3.3 Extend `CalendarDatasource` and `CustomDataDisplayingCalendar` with optional toggleable video overlay painting and day-list video rows (with reveal/copy actions)
- [x] 3.4 Re-export `SessionCalendarPanel` from legacy `src/phoofflineeeganalysis/UI/` shims

## 4. Offline Analysis Manager Shell
- [x] 4.1 Implement `src/phoofflineeeganalysis/analysis/UI/offline_analysis_manager.py` (`OfflineAnalysisManagerWindow`, `launch_offline_analysis_manager`, path editor dialog, process dialog, consolidate dialog)
- [x] 4.2 Wire `QThread` background workers for session discover, video ingest/report/consolidate, and `process_XDFs_main` with live catalog polling, status bar messages, and job log dock

## 5. Entry Points, Docs & Verification
- [x] 5.1 Add `run_offline_analysis_manager.py`, update `run_session_calendar.py` as alias, and wire `phoofflineeeganalysis:main`
- [x] 5.2 Update `src/phoofflineeeganalysis/analysis/SESSION_CALENDAR.md` and `README.md`
- [x] 5.3 Add unit tests for `AppPaths`, `VideoCatalog.load_calendar_dataframe`, and `SessionCalendarPanel` / manager construction and run pytest
