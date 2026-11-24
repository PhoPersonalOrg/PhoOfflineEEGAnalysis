# Session calendar + discovery catalog

Browse recorded LabRecorder XDF sessions on a PyQt month-grid calendar. A SQLite catalog is filled at discovery time and updated during `process_XDFs_main`.

## Workflow

### 1. Discover (no full EEG load)

```python
from pathlib import Path
from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog

lab_dir = Path(r"E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001")
catalog = get_or_create_catalog()  # default: .../PICKLED_COLLECTION/session_catalog/sessions.sqlite
n = catalog.discover_and_upsert(lab_dir)
print(f"Upserted {n} sessions into {catalog.db_path}")
```

### 2. Open the calendar

```python
from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import launch_session_calendar

launch_session_calendar(discovery_dirs=[lab_dir])  # discover then show
# or, if catalog already populated:
# launch_session_calendar()
```

Legacy import (notebooks): `from phoofflineeeganalysis.UI.CustomCalendarWidget import ...`

### 3. Process via `main_analyze_run.py`

Running `python main_analyze_run.py` (or its `__main__` block):

1. Opens/creates the SQLite catalog under `PICKLED_COLLECTION/session_catalog/sessions.sqlite`
2. Discovers all `.xdf` files under `lab_recorder_output_path` (`status=discovered`)
3. Passes the catalog into `process_XDFs_main` — each file is marked `computing` → `computed` / `exported` / `failed`
4. After exports, `sync_from_processed_batch` writes durations/`meas_date` and export paths into the catalog

### 4. Refresh the calendar

Re-open `launch_session_calendar()` (or rebuild `CalendarDatasource` from `catalog.load_calendar_dataframe()`) to see updated status colors:

| Status | Color |
|--------|--------|
| discovered | cornflower |
| computing | amber |
| computed | green |
| exported | blue |
| failed | red |

Click a day to list sessions (time, status, filename, duration).

### 5. Open a session in pyPhoTimeline (offline)

Double-click a session in the list, or select it and click **Open in Timeline**:

1. If offline timeline is already running (listens on QLocalServer `pypho_timeline_offline`), the calendar sends `show_session` — the viewer **zooms** if that XDF is already loaded, otherwise **appends** it then zooms.
2. If no timeline is running, it cold-starts `pyPhoTimeline/main_offline_timeline.py --show-session <xdf_path>`.
3. When catalog `status` is `computed` or `exported`, handoff sets `--prefer-cached-spectrograms` / IPC `prefer_cached_spectrograms` so the viewer reuses feature-store HDF5 or `PICKLED_COLLECTION/computation_cache` `spectogram` pickles instead of recomputing STFT (falls back to compute on cache miss).

A healthy offline viewer prints `Offline command server listening as 'pypho_timeline_offline'` after startup. Subsequent calendar clicks (and a second `--show-session` launch) forward over IPC instead of opening another window. If the server is up but `show_session` fails, the calendar reports an error and does **not** spawn another process.

Leave the Historical Timeline / offline viewer open for instant zoom on subsequent clicks.

Optional: set `PYPHOTIMELINE_ROOT` if pyPhoTimeline is not the sibling `../pyPhoTimeline` under `ACTIVE_DEV`.

## API sketch

- `SessionCatalog.discover_and_upsert(dirs)`
- `SessionCatalog.load_calendar_dataframe()`
- `SessionCatalog.mark_computing` / `mark_computed` / `mark_exported` / `mark_failed`
- `launch_session_calendar(catalog_path=..., discovery_dirs=...)`
- `open_session_in_timeline(xdf_path, start=..., duration_sec=...)` (IPC or spawn)
