"""Durable SQLite catalog of recorded EEG sessions with stream-modality chips and source tracking."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd
from phopymnehelper.historical_data import HistoricalData

from phoofflineeeganalysis.offline_data_manager.media_modalities import stream_names_to_chips

SESSION_STATUSES = ("discovered", "computing", "computed", "exported", "failed")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    xdf_path TEXT PRIMARY KEY,
    xdf_filename TEXT NOT NULL,
    xdf_mtime REAL,
    source_root TEXT,
    recording_datetime TEXT,
    recording_day_date TEXT,
    duration_sec REAL,
    first_timestamp_rel REAL,
    duration_rel REAL,
    status TEXT NOT NULL DEFAULT 'discovered',
    streams_json TEXT,
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


def _norm_fs_str(path: Union[str, Path]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


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


def _parse_streams_json(raw: Any) -> List[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x)]
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x)]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


def inspect_xdf_stream_chips(xdf_path: Union[str, Path]) -> List[str]:
    """Extract canonical stream chips (``EEG``, ``Motion``, ``CQ``, ``Markers``) from an XDF file."""
    path = Path(xdf_path)
    if not path.is_file():
        return []

    names: List[str] = []
    types: List[str] = []
    try:
        import pyxdf

        if hasattr(pyxdf, "resolve_streams"):
            infos = pyxdf.resolve_streams(str(path))
            for info in infos or []:
                if isinstance(info, dict):
                    if info.get("name"):
                        names.append(str(info["name"]))
                    if info.get("type"):
                        types.append(str(info["type"]))
            ## END for info in infos or []...

            if names or types:
                return stream_names_to_chips(names, types)
    except Exception:
        pass

    try:
        from phopymnehelper.xdf_files import LabRecorderXDF

        holder = LabRecorderXDF.init_basic_from_lab_recorder_xdf_file(path, debug_print=False)
        for stream in getattr(holder, "streams", None) or []:
            info = stream.get("info") if isinstance(stream, dict) else None
            if isinstance(info, dict):
                raw_name = info.get("name")
                if isinstance(raw_name, list) and raw_name:
                    names.append(str(raw_name[0]))
                elif raw_name:
                    names.append(str(raw_name))
                raw_type = info.get("type")
                if isinstance(raw_type, list) and raw_type:
                    types.append(str(raw_type[0]))
                elif raw_type:
                    types.append(str(raw_type))
        ## END for stream in getattr(holder, "streams", None) or []...
    except Exception:
        return []

    return stream_names_to_chips(names, types)


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
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
                if "streams_json" not in cols:
                    conn.execute("ALTER TABLE sessions ADD COLUMN streams_json TEXT")
                if "source_root" not in cols:
                    conn.execute("ALTER TABLE sessions ADD COLUMN source_root TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_source_root ON sessions(source_root)")
                conn.commit()


    def upsert_session(self, *, xdf_path: Union[str, Path], status: Optional[str] = None, xdf_mtime: Optional[float] = None,
                       source_root: Optional[Union[str, Path]] = None, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None,
                       first_timestamp_rel: Optional[float] = None, duration_rel: Optional[float] = None,
                       streams: Optional[Sequence[str]] = None, streams_json: Optional[str] = None,
                       fif_path: Optional[Union[str, Path]] = None, mat_path: Optional[Union[str, Path]] = None,
                       cache_key_hex: Optional[str] = None, params_json: Optional[Union[str, Dict[str, Any]]] = None,
                       computed_at: Optional[datetime] = None, error: Optional[str] = None, preserve_advanced_status: bool = True) -> None:
        """Insert or update a session row. Only non-None fields overwrite existing values."""
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

        resolved_streams_json: Optional[str] = streams_json
        if resolved_streams_json is None and streams is not None:
            resolved_streams_json = json.dumps([str(s) for s in streams if str(s)])

        source_root_str: Optional[str] = None
        if source_root is not None:
            sr_path = Path(source_root)
            source_root_str = str(sr_path.resolve()) if sr_path.exists() else str(sr_path)

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
                        xdf_path, xdf_filename, xdf_mtime, source_root, recording_datetime, recording_day_date,
                        duration_sec, first_timestamp_rel, duration_rel, status, streams_json,
                        fif_path, mat_path, cache_key_hex, params_json, computed_at, error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(xdf_path) DO UPDATE SET
                        xdf_filename = excluded.xdf_filename,
                        xdf_mtime = COALESCE(excluded.xdf_mtime, sessions.xdf_mtime),
                        source_root = COALESCE(excluded.source_root, sessions.source_root),
                        recording_datetime = COALESCE(excluded.recording_datetime, sessions.recording_datetime),
                        recording_day_date = COALESCE(excluded.recording_day_date, sessions.recording_day_date),
                        duration_sec = COALESCE(excluded.duration_sec, sessions.duration_sec),
                        first_timestamp_rel = COALESCE(excluded.first_timestamp_rel, sessions.first_timestamp_rel),
                        duration_rel = COALESCE(excluded.duration_rel, sessions.duration_rel),
                        status = excluded.status,
                        streams_json = COALESCE(excluded.streams_json, sessions.streams_json),
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
                        source_root_str,
                        rec_iso,
                        day_iso,
                        duration_sec,
                        first_timestamp_rel,
                        duration_rel,
                        effective_status,
                        resolved_streams_json,
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


    @staticmethod
    def _match_source_root(file_path: Path, candidate_roots: Sequence[Path]) -> Optional[Path]:
        file_norm = _norm_fs_str(file_path)
        best: Optional[Path] = None
        best_len = -1
        for root in candidate_roots:
            root_norm = _norm_fs_str(root)
            if file_norm == root_norm or file_norm.startswith(root_norm + os.sep):
                if len(root_norm) > best_len:
                    best = root
                    best_len = len(root_norm)
        ## END for root in candidate_roots...

        return best


    def discover_and_upsert(self, dirs: Union[Path, str, Sequence[Union[Path, str]]], recordings_extensions: Optional[List[str]] = None, n_most_recent: Optional[int] = None, *, enrich_streams: bool = False) -> int:
        """Scan directories for recording files and upsert as ``discovered`` (with optional stream chip inspection)."""
        if isinstance(dirs, (Path, str)):
            dir_list: List[Path] = [Path(dirs)]
        else:
            dir_list = [Path(p) for p in dirs]
        ext = recordings_extensions if recordings_extensions is not None else [".xdf"]
        found = list(HistoricalData.get_recording_files(recordings_dir=dir_list, recordings_extensions=ext))
        if n_most_recent is not None and n_most_recent > 0:
            found = found[:n_most_recent]

        for a_path in found:
            matched_root = self._match_source_root(Path(a_path), dir_list) or Path(a_path).parent
            self.upsert_session(xdf_path=a_path, status="discovered", source_root=matched_root, preserve_advanced_status=True)
        ## END for a_path in found...

        if enrich_streams and found:
            self.enrich_streams_for_paths(found)
        return len(found)


    def enrich_streams_for_paths(self, paths: Sequence[Union[str, Path]]) -> int:
        """Inspect XDF stream headers for *paths* and persist ``streams_json`` chips."""
        enriched = 0
        for raw_path in paths:
            chips = inspect_xdf_stream_chips(raw_path)
            if chips:
                self.upsert_session(xdf_path=raw_path, streams=chips, preserve_advanced_status=True)
                enriched += 1
        ## END for raw_path in paths...

        return enriched


    def mark_computing(self, xdf_path: Union[str, Path]) -> None:
        self.upsert_session(xdf_path=xdf_path, status="computing", preserve_advanced_status=False, error=None)


    def mark_computed(self, xdf_path: Union[str, Path], *, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None, streams: Optional[Sequence[str]] = None, cache_key_hex: Optional[str] = None, params_json: Optional[Union[str, Dict[str, Any]]] = None) -> None:
        self.upsert_session(
            xdf_path=xdf_path,
            status="computed",
            recording_datetime=recording_datetime,
            duration_sec=duration_sec,
            streams=streams,
            cache_key_hex=cache_key_hex,
            params_json=params_json,
            computed_at=datetime.now(tz=timezone.utc),
            preserve_advanced_status=False,
            error=None,
        )


    def mark_exported(self, xdf_path: Union[str, Path], *, fif_path: Optional[Union[str, Path]] = None, mat_path: Optional[Union[str, Path]] = None, recording_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None, streams: Optional[Sequence[str]] = None) -> None:
        self.upsert_session(
            xdf_path=xdf_path,
            status="exported",
            fif_path=fif_path,
            mat_path=mat_path,
            recording_datetime=recording_datetime,
            duration_sec=duration_sec,
            streams=streams,
            computed_at=datetime.now(tz=timezone.utc),
            preserve_advanced_status=False,
            error=None,
        )


    def mark_failed(self, xdf_path: Union[str, Path], error: str) -> None:
        self.upsert_session(xdf_path=xdf_path, status="failed", error=str(error), preserve_advanced_status=False)


    def sync_from_processed_batch(self, stream_infos_df: pd.DataFrame, active_only_out_eeg_raws: Sequence[Any], *, recordings_dir: Optional[Union[str, Path]] = None, status: str = "computed", summary_csv_path: Optional[Union[str, Path]] = None, extra_params: Optional[Dict[str, Any]] = None) -> int:
        """Upsert catalog rows from a finished ``process_XDFs_main`` batch, capturing stream chips when present."""
        if stream_infos_df is None or len(stream_infos_df) == 0:
            return 0

        recordings_dir_path = Path(recordings_dir) if recordings_dir is not None else None
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

            stream_names: List[str] = []
            for name_col in ("name", "stream_name"):
                if name_col in rows.columns:
                    stream_names.extend(str(v) for v in rows[name_col].dropna().tolist() if str(v))
            ## END for name_col in ("name", "stream_name")...

            stream_types: List[str] = []
            for type_col in ("type", "stream_type"):
                if type_col in rows.columns:
                    stream_types.extend(str(v) for v in rows[type_col].dropna().tolist() if str(v))
            ## END for type_col in ("type", "stream_type")...

            chips = stream_names_to_chips(stream_names, stream_types) if (stream_names or stream_types) else []

            params: Dict[str, Any] = dict(extra_params or {})
            if summary_csv_path is not None:
                params["summary_csv_path"] = str(Path(summary_csv_path))

            effective_status = "exported" if (fif_path or mat_path) and status == "computed" else status
            self.upsert_session(
                xdf_path=xdf_path,
                status=effective_status,
                source_root=recordings_dir_path,
                recording_datetime=meas_date if isinstance(meas_date, datetime) else None,
                duration_sec=duration_sec,
                streams=chips if chips else None,
                fif_path=fif_path,
                mat_path=mat_path,
                params_json=params if params else None,
                computed_at=datetime.now(tz=timezone.utc),
                preserve_advanced_status=False,
                error=None,
            )
            n_updated += 1
        ## END for dataset_idx in dataset_ids...

        return n_updated


    def load_sessions_dataframe(self) -> pd.DataFrame:
        with self._lock:
            with self._connect() as conn:
                df = pd.read_sql_query("SELECT * FROM sessions ORDER BY recording_datetime ASC, xdf_filename ASC", conn)
        if len(df) == 0:
            return df
        df["recording_datetime"] = df["recording_datetime"].map(_iso_to_datetime)
        df["recording_day_date"] = df["recording_day_date"].map(_iso_to_datetime)
        if "streams_json" in df.columns:
            df["streams"] = df["streams_json"].map(_parse_streams_json)
        else:
            df["streams"] = [[] for _ in range(len(df))]
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
                    "source_root",
                    "recording_datetime",
                    "recording_day_date",
                    "duration_sec",
                    "first_timestamp_rel",
                    "duration_rel",
                    "status",
                    "streams",
                    "fif_path",
                    "mat_path",
                    "error",
                ]
            )

        out = df.copy()
        out["name"] = "Epoc X"
        for idx, row in out.iterrows():
            if pd.isna(row.get("first_timestamp_rel")) and row.get("recording_datetime") is not None:
                out.at[idx, "first_timestamp_rel"] = _rel_fraction_of_day(row["recording_datetime"])
            if pd.isna(row.get("duration_rel")):
                if pd.notna(row.get("duration_sec")):
                    out.at[idx, "duration_rel"] = _duration_rel_from_sec(float(row["duration_sec"]))
                else:
                    out.at[idx, "duration_rel"] = 0.05
            if pd.isna(row.get("first_timestamp_rel")):
                out.at[idx, "first_timestamp_rel"] = 0.1
        ## END for idx, row in out.iterrows()...

        return out


    def source_summary_by_root(self, candidate_roots: Optional[Sequence[Path]] = None) -> Dict[str, Dict[str, Any]]:
        """Summarize session counts and latest update time grouped by LabRecorder root."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("SELECT xdf_path, source_root, updated_at FROM sessions").fetchall()

        roots = list(candidate_roots) if candidate_roots is not None else []
        summary: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            raw_sr = row["source_root"]
            matched_key: Optional[str] = None
            if raw_sr:
                matched_key = _norm_fs_str(raw_sr)
            elif row["xdf_path"]:
                m = self._match_source_root(Path(row["xdf_path"]), roots) if roots else None
                matched_key = _norm_fs_str(m if m is not None else Path(row["xdf_path"]).parent)
            if not matched_key:
                continue
            last_up = row["updated_at"]
            prev = summary.get(matched_key)
            if prev is None:
                summary[matched_key] = {"count": 1, "last_scanned_at": last_up}
            else:
                prev["count"] = int(prev["count"]) + 1
                if last_up and (prev["last_scanned_at"] is None or str(last_up) > str(prev["last_scanned_at"])):
                    prev["last_scanned_at"] = last_up
        ## END for row in rows...

        return summary


def get_or_create_catalog(db_path: Optional[Union[str, Path]] = None, pickled_data_path: Optional[Path] = None) -> SessionCatalog:
    path = Path(db_path) if db_path is not None else default_catalog_path(pickled_data_path=pickled_data_path)
    return SessionCatalog(path)


__all__ = [
    "SESSION_STATUSES",
    "SessionCatalog",
    "default_catalog_path",
    "get_or_create_catalog",
    "inspect_xdf_stream_chips",
]
