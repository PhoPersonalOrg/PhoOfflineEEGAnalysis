# Offline Analysis Manager & Session Calendar

The primary PyQt GUI for offline EEG and video data management is **`OfflineAnalysisManagerWindow`**, which embeds the month-grid **`SessionCalendarPanel`** alongside session discovery, EEG processing, video catalog actions, and centralized path configuration.

## Launching the Manager

```bash
python run_offline_analysis_manager.py
# or via installed console script:
phoofflineeeganalysis
# optional flags:
phoofflineeeganalysis --discover
phoofflineeeganalysis --config path/to/offline_analysis_paths.yaml
```

`run_session_calendar.py` remains as a deprecation alias that launches the manager.

## Path Configuration (`AppPaths`)

Committed defaults live in `config/offline_analysis_paths.yaml` (no hard-coded drive letters). Machine-specific overrides are stored in gitignored `config/offline_analysis_paths.local.yaml` (or edited in-app via **Config → Edit Paths...**), and can also be overridden via environment variables:

- `PHO_LAB_RECORDER_DIRS` (`os.pathsep`-separated list)
- `PHO_PICKLED_DATA_PATH`
- `PHO_SESSION_CATALOG_PATH`
- `PHO_VIDEO_CATALOG_PATH`
- `PHO_VIDEO_SOURCES_YAML`

## Manager Features

- **Sessions → Discover / Refresh Catalog (`Ctrl+R`)**: runs `SessionCatalog.discover_and_upsert` on a background thread and refreshes the calendar.
- **Sessions → Process Recent...**: runs `SpectogramPlottingHelper.process_XDFs_main(..., session_catalog=catalog, retain_in_memory=False)` on a background thread while polling the catalog every 2 seconds so status colors update live.
- **Video → Ingest Video Metadata / Duplicate Report / Consolidate Videos...**: wraps `VideoCatalog` (`discover_and_upsert`, `recompute_duplicates`, duplicate summary, and `consolidate` with dry-run support) and refreshes the calendar video overlay.
- **View → Show Video Overlay**: toggles category-colored video interval bars (`EyeRecordings`, `WebcamRecordings`, `ScreenRecordings`) on the calendar and video entries in the day detail list.
- **Job Log Dock**: shows timestamped progress and errors from background jobs.

### Session Status Colors

| Status | Color |
|--------|-------|
| discovered | cornflower |
| computing | amber |
| computed | green |
| exported | blue |
| failed | red |

Click a day to list EEG sessions and (when enabled) videos. Right-click a session for **Open in Timeline**, **Reveal in Explorer**, or **Copy Path**; right-click or double-click a video for **Reveal in Explorer** or **Copy Path**.

## Opening a Session in `pyPhoTimeline` (offline)

Double-click an EEG session in the list, or select it and click **Open in Timeline**:

1. If offline timeline is already running (listens on QLocalServer `pypho_timeline_offline`), the calendar sends `show_session` — the viewer **zooms** if that XDF is already loaded, otherwise **appends** it then zooms.
2. If no timeline is running, it cold-starts `pyPhoTimeline/main_offline_timeline.py --show-session <xdf_path>`.
3. When catalog `status` is `computed` or `exported`, handoff sets `--prefer-cached-spectrograms` / IPC `prefer_cached_spectrograms` so the viewer reuses cached spectrograms when available.

Optional: set `PYPHOTIMELINE_ROOT` if `pyPhoTimeline` is not the sibling `../pyPhoTimeline` under `ACTIVE_DEV`.

## Standalone Calendar Launch (Notebooks)

For notebook or script workflows that only need the calendar window without the manager shell:

```python
from pathlib import Path
from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import launch_session_calendar

launch_session_calendar(discovery_dirs=[Path("path/to/sub-P001")])
# or, if catalog is already populated:
# launch_session_calendar()
```

Legacy import (notebooks): `from phoofflineeeganalysis.UI.CustomCalendarWidget import ...`

## API Sketch

- `AppPaths.load(...)` / `AppPaths.save(...)`
- `SessionCatalog.discover_and_upsert(dirs)` / `load_calendar_dataframe()`
- `VideoCatalog.discover_and_upsert(...)` / `load_calendar_dataframe()` / `consolidate(...)`
- `SessionCalendarPanel` (embeddable widget) / `SessionCalendarWindow` / `launch_session_calendar(...)`
- `OfflineAnalysisManagerWindow` / `launch_offline_analysis_manager(...)`
- `open_session_in_timeline(xdf_path, start=..., duration_sec=...)` (IPC or spawn)
