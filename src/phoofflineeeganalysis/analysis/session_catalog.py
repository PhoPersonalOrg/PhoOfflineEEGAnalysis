"""Durable SQLite catalog of recorded EEG sessions for calendar browse and processing status.

Usage:
    from phoofflineeeganalysis.analysis.session_catalog import SessionCatalog

    catalog = SessionCatalog(db_path)
    catalog.discover_and_upsert(lab_recorder_dirs)
    df = catalog.load_calendar_dataframe()
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import pandas as pd
from phopymnehelper.historical_data import HistoricalData

SESSION_STATUSES = ("discovered", "computing", "computed", "exported", "failed")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    xdf_path TEXT PRIMARY KEY,
    xdf_filename TEXT NOT NULL,
    xdf_mtime REAL,
    recording_datetime TEXT,
    recording_day_date TEXT,
    duration_sec REAL,
    first_timestamp_rel REAL,
    duration_rel REAL,
    status TEXT NOT NULL DEFAULT 'discovered',
    fif_path TEXT,
    mat_path TEXT,
    cache_key_hex TEXT,
    params_json TEXT,
    computed_at TEXT,
    error TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(recording_day_date);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
"""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None or (isinstance(dt, float) and pd.isna(dt)):
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    aware = _ensure_aware(dt)
    return aware.isoformat() if aware is not None else None


def _iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        return _ensure_aware(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _day_date_iso(dt: Optional[datetime]) -> Optional[str]:
    aware = _ensure_aware(dt)
    if aware is None:
        return None
    day = aware.replace(hour=0, minute=0, second=0, microsecond=0)
    return day.isoformat()


def _rel_fraction_of_day(dt: Optional[datetime]) -> Optional[float]:
    aware = _ensure_aware(dt)
    if aware is None:
        return None
    day_start = aware.replace(hour=0, minute=0, second=0, microsecond=0)
    return float((aware - day_start).total_seconds()) / (24.0 * 3600.0)


def _duration_rel_from_sec(duration_sec: Optional[float]) -> Optional[float]:
    if duration_sec is None or not pd.notna(duration_sec):
        return None
    try:
        return float(duration_sec) / (24.0 * 3600.0)
    except (TypeError, ValueError):
        return None


def default_catalog_path(pickled_data_path: Optional[Path] = None) -> Path:
    """Default SQLite path under PICKLED_COLLECTION/session_catalog/sessions.sqlite."""
    if pickled_data_path is None:
        pickled_data_path = Path(r"E:/Dropbox (Personal)/Databases/AnalysisData/MNE_preprocessed/PICKLED_COLLECTION")
    root = Path(pickled_data_path).joinpath("session_catalog")
    root.mkdir(parents=True, exist_ok=True)
    return root.joinpath("sessions.sqlite")


class SessionCatalog:
    """Thread-safe SQLite catalog of XDF recording sessions."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA_SQL)
                conn.commit()

    def upsert_session(self, *, xdf_path: Union[str, Path], status: Optional[str] = None, xdf_mtime: Optional[float] = None, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None, first_timestamp_rel: Optional[float] = None, duration_rel: Optional[float] = None, fif_path: Optional[Union[str, Path]] = None, mat_path: Optional[Union[str, Path]] = None, cache_key_hex: Optional[str] = None, params_json: Optional[Union[str, Dict[str, Any]]] = None, computed_at: Optional[datetime] = None, error: Optional[str] = None, preserve_advanced_status: bool = True) -> None:
        """Insert or update a session row. Only non-None fields overwrite existing values.

        If *preserve_advanced_status* is True and the existing status is more advanced than
        ``discovered``/``computing``, a new ``status=discovered`` from re-discovery will not downgrade it.
        """
        path = Path(xdf_path).resolve()
        filename = path.name
        if xdf_mtime is None and path.exists():
            try:
                xdf_mtime = float(path.stat().st_mtime)
            except OSError:
                xdf_mtime = None

        rec_dt = _ensure_aware(recording_datetime)
        if rec_dt is None and path.exists():
            rec_dt = self._try_parse_recording_datetime(path)

        day_iso = _day_date_iso(rec_dt)
        if first_timestamp_rel is None and rec_dt is not None:
            first_timestamp_rel = _rel_fraction_of_day(rec_dt)
        if duration_rel is None and duration_sec is not None:
            duration_rel = _duration_rel_from_sec(duration_sec)

        if isinstance(params_json, dict):
            params_json = json.dumps(params_json)

        fif_str = str(Path(fif_path)) if fif_path is not None else None
        mat_str = str(Path(mat_path)) if mat_path is not None else None
        computed_iso = _datetime_to_iso(computed_at) if computed_at is not None else None
        rec_iso = _datetime_to_iso(rec_dt)
        updated_at = _utc_now_iso()

        with self._lock:
            with self._connect() as conn:
                existing = conn.execute("SELECT status FROM sessions WHERE xdf_path = ?", (str(path),)).fetchone()
                effective_status = status
                if existing is not None and preserve_advanced_status and status in (None, "discovered"):
                    prior = existing["status"]
                    if prior in ("computed", "exported", "failed", "computing"):
                        effective_status = prior
                if effective_status is None:
                    effective_status = existing["status"] if existing is not None else "discovered"
                if effective_status not in SESSION_STATUSES:
                    raise ValueError(f"Invalid status {effective_status!r}; expected one of {SESSION_STATUSES}")

                conn.execute(
                    """
                    INSERT INTO sessions (
                        xdf_path, xdf_filename, xdf_mtime, recording_datetime, recording_day_date,
                        duration_sec, first_timestamp_rel, duration_rel, status,
                        fif_path, mat_path, cache_key_hex, params_json, computed_at, error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(xdf_path) DO UPDATE SET
                        xdf_filename = excluded.xdf_filename,
                        xdf_mtime = COALESCE(excluded.xdf_mtime, sessions.xdf_mtime),
                        recording_datetime = COALESCE(excluded.recording_datetime, sessions.recording_datetime),
                        recording_day_date = COALESCE(excluded.recording_day_date, sessions.recording_day_date),
                        duration_sec = COALESCE(excluded.duration_sec, sessions.duration_sec),
                        first_timestamp_rel = COALESCE(excluded.first_timestamp_rel, sessions.first_timestamp_rel),
                        duration_rel = COALESCE(excluded.duration_rel, sessions.duration_rel),
                        status = excluded.status,
                        fif_path = COALESCE(excluded.fif_path, sessions.fif_path),
                        mat_path = COALESCE(excluded.mat_path, sessions.mat_path),
                        cache_key_hex = COALESCE(excluded.cache_key_hex, sessions.cache_key_hex),
                        params_json = COALESCE(excluded.params_json, sessions.params_json),
                        computed_at = COALESCE(excluded.computed_at, sessions.computed_at),
                        error = CASE WHEN excluded.status = 'failed' THEN COALESCE(excluded.error, sessions.error)
                                     WHEN excluded.status IN ('computed', 'exported') THEN NULL
                                     ELSE COALESCE(excluded.error, sessions.error) END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(path),
                        filename,
                        xdf_mtime,
                        rec_iso,
                        day_iso,
                        duration_sec,
                        first_timestamp_rel,
                        duration_rel,
                        effective_status,
                        fif_str,
                        mat_str,
                        cache_key_hex,
                        params_json,
                        computed_iso if computed_iso is not None else (updated_at if effective_status in ("computed", "exported") else None),
                        error,
                        updated_at,
                    ),
                )
                conn.commit()

    @staticmethod
    def _try_parse_recording_datetime(path: Path) -> Optional[datetime]:
        try:
            dt = HistoricalData.extract_datetime_from_filename(path.name)
            return _ensure_aware(dt)
        except ValueError:
            return None

    def discover_and_upsert(self, dirs: Union[Path, str, Sequence[Union[Path, str]]], recordings_extensions: Optional[List[str]] = None, n_most_recent: Optional[int] = None) -> int:
        """Scan directories for recording files and upsert as ``discovered`` (without full EEG load).

        Returns the number of files found/upserted.
        """
        if isinstance(dirs, (Path, str)):
            dir_list: List[Path] = [Path(dirs)]
        else:
            dir_list = [Path(p) for p in dirs]
        ext = recordings_extensions if recordings_extensions is not None else [".xdf"]
        found = list(HistoricalData.get_recording_files(recordings_dir=dir_list, recordings_extensions=ext))
        if n_most_recent is not None and n_most_recent > 0:
            found = found[:n_most_recent]

        for a_path in found:
            self.upsert_session(xdf_path=a_path, status="discovered", preserve_advanced_status=True)
        ## END for a_path in found....

        return len(found)

    def mark_computing(self, xdf_path: Union[str, Path]) -> None:
        self.upsert_session(xdf_path=xdf_path, status="computing", preserve_advanced_status=False, error=None)

    def mark_computed(self, xdf_path: Union[str, Path], *, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None, cache_key_hex: Optional[str] = None, params_json: Optional[Union[str, Dict[str, Any]]] = None) -> None:
        self.upsert_session(
            xdf_path=xdf_path,
            status="computed",
            recording_datetime=recording_datetime,
            duration_sec=duration_sec,
            cache_key_hex=cache_key_hex,
            params_json=params_json,
            computed_at=datetime.now(tz=timezone.utc),
            preserve_advanced_status=False,
            error=None,
        )

    def mark_exported(self, xdf_path: Union[str, Path], *, fif_path: Optional[Union[str, Path]] = None, mat_path: Optional[Union[str, Path]] = None, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None) -> None:
        self.upsert_session(
            xdf_path=xdf_path,
            status="exported",
            fif_path=fif_path,
            mat_path=mat_path,
            recording_datetime=recording_datetime,
            duration_sec=duration_sec,
            computed_at=datetime.now(tz=timezone.utc),
            preserve_advanced_status=False,
            error=None,
        )

    def mark_failed(self, xdf_path: Union[str, Path], error: str) -> None:
        self.upsert_session(xdf_path=xdf_path, status="failed", error=str(error), preserve_advanced_status=False)

    def sync_from_processed_batch(self, stream_infos_df: pd.DataFrame, active_only_out_eeg_raws: Sequence[Any], *, recordings_dir: Optional[Union[str, Path]] = None, status: str = "computed", summary_csv_path: Optional[Union[str, Path]] = None, extra_params: Optional[Dict[str, Any]] = None) -> int:
        """Upsert catalog rows from a finished ``process_XDFs_main`` batch.

        Resolves each row's ``xdf_filename`` under *recordings_dir* (or uses an ``xdf_path`` column if present).
        Returns the number of sessions upserted.
        """
        if stream_infos_df is None or len(stream_infos_df) == 0:
            return 0

        recordings_dir_path = Path(recordings_dir) if recordings_dir is not None else None
        # xdf_dataset_idx is often both the index name and a column after concat/set_index;
        # reset_index would raise ValueError: cannot insert xdf_dataset_idx, already exists.
        sidf = stream_infos_df
        if sidf.index.name == "xdf_dataset_idx" and "xdf_dataset_idx" in sidf.columns:
            sidf = sidf.drop(columns=["xdf_dataset_idx"])
        tmp_df = sidf.reset_index(drop=False)
        if "xdf_dataset_idx" in tmp_df.columns:
            dataset_ids = tmp_df["xdf_dataset_idx"].drop_duplicates().tolist()
        else:
            n_fallback = len(active_only_out_eeg_raws) if active_only_out_eeg_raws is not None else len(tmp_df)
            dataset_ids = list(range(n_fallback))

        n_updated = 0
        for dataset_idx in dataset_ids:
            rows = tmp_df[tmp_df["xdf_dataset_idx"] == dataset_idx] if "xdf_dataset_idx" in tmp_df.columns else tmp_df.iloc[[dataset_idx]]
            if len(rows) == 0:
                continue
            row = rows.iloc[0]
            xdf_path: Optional[Path] = None
            if "xdf_path" in rows.columns and pd.notna(row.get("xdf_path")):
                xdf_path = Path(str(row["xdf_path"]))
            elif "xdf_filename" in rows.columns and pd.notna(row.get("xdf_filename")):
                fname = str(row["xdf_filename"])
                if recordings_dir_path is not None:
                    xdf_path = recordings_dir_path.joinpath(fname)
                else:
                    xdf_path = Path(fname)
            if xdf_path is None:
                continue

            raw = None
            try:
                raw_idx = int(dataset_idx)
                if active_only_out_eeg_raws is not None and 0 <= raw_idx < len(active_only_out_eeg_raws):
                    raw = active_only_out_eeg_raws[raw_idx]
            except (TypeError, ValueError):
                raw = None

            meas_date = None
            duration_sec = None
            if raw is not None:
                meas_date = raw.info.get("meas_date", None)
                try:
                    duration_sec = float(raw.n_times) / float(raw.info["sfreq"]) if raw.info.get("sfreq") else None
                except Exception:
                    duration_sec = None
            else:
                # Streaming Analyze Run: Raw lists are empty; use columns written onto stream_infos
                if "recording_datetime" in rows.columns and pd.notna(row.get("recording_datetime")):
                    meas_date = row.get("recording_datetime")
                    if isinstance(meas_date, str):
                        try:
                            meas_date = datetime.fromisoformat(meas_date.replace("Z", "+00:00"))
                        except Exception:
                            meas_date = None
                if "eeg_duration_sec" in rows.columns and pd.notna(row.get("eeg_duration_sec")):
                    try:
                        duration_sec = float(row.get("eeg_duration_sec"))
                    except Exception:
                        duration_sec = None

            fif_path = None
            mat_path = None
            if "proccessed_fif_filename" in rows.columns and pd.notna(row.get("proccessed_fif_filename")):
                fif_path = str(row["proccessed_fif_filename"])
            if "proccessed_mat_filename" in rows.columns and pd.notna(row.get("proccessed_mat_filename")):
                mat_path = str(row["proccessed_mat_filename"])

            params: Dict[str, Any] = dict(extra_params or {})
            if summary_csv_path is not None:
                params["summary_csv_path"] = str(Path(summary_csv_path))

            effective_status = "exported" if (fif_path or mat_path) and status == "computed" else status
            self.upsert_session(
                xdf_path=xdf_path,
                status=effective_status,
                recording_datetime=meas_date if isinstance(meas_date, datetime) else None,
                duration_sec=duration_sec,
                fif_path=fif_path,
                mat_path=mat_path,
                params_json=params if params else None,
                computed_at=datetime.now(tz=timezone.utc),
                preserve_advanced_status=False,
                error=None,
            )
            n_updated += 1
        ## END for dataset_idx in dataset_ids....

        return n_updated

    def load_sessions_dataframe(self) -> pd.DataFrame:
        with self._lock:
            with self._connect() as conn:
                df = pd.read_sql_query("SELECT * FROM sessions ORDER BY recording_datetime ASC, xdf_filename ASC", conn)
        if len(df) == 0:
            return df
        df["recording_datetime"] = df["recording_datetime"].map(_iso_to_datetime)
        df["recording_day_date"] = df["recording_day_date"].map(_iso_to_datetime)
        return df

    def load_calendar_dataframe(self) -> pd.DataFrame:
        """Return a DataFrame shaped for ``CalendarDatasource`` / the month-grid calendar."""
        df = self.load_sessions_dataframe()
        if len(df) == 0:
            return pd.DataFrame(
                columns=[
                    "xdf_path",
                    "xdf_filename",
                    "name",
                    "recording_datetime",
                    "recording_day_date",
                    "duration_sec",
                    "first_timestamp_rel",
                    "duration_rel",
                    "status",
                    "fif_path",
                    "error",
                ]
            )

        out = df.copy()
        out["name"] = "Epoc X"
        # Fill relative columns when missing
        for idx, row in out.iterrows():
            if pd.isna(row.get("first_timestamp_rel")) and row.get("recording_datetime") is not None:
                out.at[idx, "first_timestamp_rel"] = _rel_fraction_of_day(row["recording_datetime"])
            if pd.isna(row.get("duration_rel")):
                if pd.notna(row.get("duration_sec")):
                    out.at[idx, "duration_rel"] = _duration_rel_from_sec(float(row["duration_sec"]))
                else:
                    # Visible stub so days with unknown duration still paint a short bar
                    out.at[idx, "duration_rel"] = 0.05
            if pd.isna(row.get("first_timestamp_rel")):
                out.at[idx, "first_timestamp_rel"] = 0.1
        ## END for idx, row in out.iterrows()....

        return out


def get_or_create_catalog(db_path: Optional[Union[str, Path]] = None, pickled_data_path: Optional[Path] = None) -> SessionCatalog:
    path = Path(db_path) if db_path is not None else default_catalog_path(pickled_data_path=pickled_data_path)
    return SessionCatalog(path)
