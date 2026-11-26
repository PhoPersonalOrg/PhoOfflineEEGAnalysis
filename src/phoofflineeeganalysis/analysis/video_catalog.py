"""Durable SQLite catalog of video recordings: discover, dedupe, consolidate.

Usage:
    from phoofflineeeganalysis.analysis.video_catalog import VideoCatalog, default_video_catalog_path

    catalog = VideoCatalog(default_video_catalog_path())
    catalog.discover_and_upsert()
    catalog.recompute_duplicates()
    catalog.consolidate(dry_run=True)
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
from phopylslhelper.file_metadata_caching.video_metadata import VideoMetadataParser

from phoofflineeeganalysis.analysis.video_ingest_helpers import (
    DEFAULT_MIN_AGE_S,
    enrich_from_timing_sidecar,
    format_hint_for_path,
    is_recording_complete,
    unique_dest_path,
)

VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".wmv"]
DEFAULT_DURATION_TOL_SEC = 1.0
DEFAULT_CONSOLIDATE_ROOT = Path(r"H:\PhoBehaviorProject\RECORDINGS")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS videos (
    video_path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    stem TEXT NOT NULL,
    extension TEXT NOT NULL,
    category TEXT NOT NULL,
    source_root TEXT NOT NULL,
    start_datetime TEXT,
    end_datetime TEXT,
    duration_sec REAL,
    fps REAL,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    mtime REAL,
    format_hint TEXT,
    duplicate_group_id TEXT,
    is_canonical INTEGER NOT NULL DEFAULT 1,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    duplicate_reason TEXT,
    consolidated_path TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_stem ON videos(stem);
CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category);
CREATE INDEX IF NOT EXISTS idx_videos_group ON videos(duplicate_group_id);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    group_id TEXT PRIMARY KEY,
    stem TEXT NOT NULL,
    duration_sec REAL,
    canonical_path TEXT,
    member_count INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class SourceSpec:
    """One discovery root under a content category."""

    category: str
    path: Path
    recursive: bool = False
    exclude: List[Path] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None or (isinstance(dt, float) and pd.isna(dt)):
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if not isinstance(dt, datetime):
        return None
    return dt.isoformat()


def _iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def default_video_catalog_path(project_root: Optional[Path] = None) -> Path:
    """Default SQLite path under PhoOfflineEEGAnalysis/output/video_catalog.sqlite."""
    if project_root is None:
        # src/phoofflineeeganalysis/analysis/video_catalog.py → repo root
        project_root = Path(__file__).resolve().parents[3]
    out = Path(project_root) / "output"
    out.mkdir(parents=True, exist_ok=True)
    return out / "video_catalog.sqlite"


def default_sources_yaml_path(project_root: Optional[Path] = None) -> Path:
    if project_root is None:
        project_root = Path(__file__).resolve().parents[3]
    return Path(project_root) / "config" / "video_sources.yaml"


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def _load_video_sources_fallback(path: Path) -> Dict[str, Any]:
    """Minimal loader for our video_sources.yaml shape when PyYAML is unavailable."""
    categories: Dict[str, List[Dict[str, Any]]] = {}
    consolidate: Dict[str, Any] = {}
    section: Optional[str] = None  # "categories" | "consolidate"
    current_category: Optional[str] = None
    current_entry: Optional[Dict[str, Any]] = None
    in_exclude = False

    def _flush_entry() -> None:
        nonlocal current_entry
        if current_category is not None and current_entry is not None:
            categories.setdefault(current_category, []).append(current_entry)
        current_entry = None

    ## END def _flush_entry....

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            ## END if blank....

            if line == "categories:":
                _flush_entry()
                section = "categories"
                current_category = None
                in_exclude = False
                continue
            ## END if categories....

            if line == "consolidate:":
                _flush_entry()
                section = "consolidate"
                current_category = None
                in_exclude = False
                continue
            ## END if consolidate....

            if section == "consolidate":
                if ":" in line:
                    key, _, val = line.strip().partition(":")
                    consolidate[key.strip()] = _parse_scalar(val)
                continue
            ## END if consolidate section....

            if section != "categories":
                continue
            ## END if not categories....

            # "  CategoryName:"
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
                _flush_entry()
                current_category = stripped[:-1].strip()
                in_exclude = False
                continue
            ## END if category header....

            if stripped.startswith("- path:"):
                _flush_entry()
                in_exclude = False
                current_entry = {"path": _parse_scalar(stripped[len("- path:"):]), "recursive": False, "exclude": []}
                continue
            ## END if path entry....

            if current_entry is None:
                continue
            ## END if no entry....

            if stripped.startswith("recursive:"):
                current_entry["recursive"] = bool(_parse_scalar(stripped[len("recursive:"):]))
                in_exclude = False
                continue
            ## END if recursive....

            if stripped == "exclude:":
                in_exclude = True
                current_entry.setdefault("exclude", [])
                continue
            ## END if exclude header....

            if in_exclude and stripped.startswith("- "):
                current_entry.setdefault("exclude", []).append(_parse_scalar(stripped[2:]))
                continue
            ## END if exclude item....

            in_exclude = False
        ## END for raw_line in f....

    _flush_entry()
    return {"categories": categories, "consolidate": consolidate}


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        data = _load_video_sources_fallback(path)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_source_specs(sources_yaml: Optional[Union[str, Path]] = None) -> Tuple[List[SourceSpec], Path]:
    """Load SourceSpec list and consolidate dest_root from YAML."""
    path = Path(sources_yaml) if sources_yaml is not None else default_sources_yaml_path()
    data = _load_yaml(path)
    categories = data.get("categories") or {}
    specs: List[SourceSpec] = []
    for category, entries in categories.items():
        if not entries:
            continue
        for entry in entries:
            if isinstance(entry, str):
                specs.append(SourceSpec(category=str(category), path=Path(entry)))
                continue
            root = Path(entry["path"])
            recursive = bool(entry.get("recursive", False))
            exclude = [Path(p) for p in (entry.get("exclude") or [])]
            specs.append(SourceSpec(category=str(category), path=root, recursive=recursive, exclude=exclude))
        ## END for entry in entries....
    ## END for category, entries in categories.items()....

    consolidate = data.get("consolidate") or {}
    dest_root = Path(consolidate.get("dest_root") or DEFAULT_CONSOLIDATE_ROOT)
    return specs, dest_root


def _keeper_sort_key(row: Dict[str, Any]) -> Tuple[int, int, float, float]:
    """Prefer .mkv, then larger size, then newer mtime."""
    ext = str(row.get("extension") or "").lower()
    is_mkv = 1 if ext == ".mkv" else 0
    size = float(row.get("file_size") or 0)
    mtime = float(row.get("mtime") or 0)
    return (is_mkv, 1, size, mtime)


def _group_id_for(stem: str, duration_sec: float) -> str:
    raw = f"{stem}|{duration_sec:.3f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class VideoCatalog:
    """Thread-safe SQLite catalog of video files across multi-root sources."""

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


    def upsert_video(self, *, video_path: Union[str, Path], category: str, source_root: Union[str, Path], start_datetime: Optional[datetime] = None, end_datetime: Optional[datetime] = None, duration_sec: Optional[float] = None, fps: Optional[float] = None, width: Optional[int] = None, height: Optional[int] = None, file_size: Optional[int] = None, mtime: Optional[float] = None, format_hint: Optional[str] = None, preserve_consolidate: bool = True) -> None:
        path = Path(video_path).resolve()
        filename = path.name
        stem = path.stem
        extension = path.suffix.lower()
        source_root_str = str(Path(source_root).resolve()) if Path(source_root).exists() else str(Path(source_root))
        if file_size is None or mtime is None:
            try:
                st = path.stat()
                if file_size is None:
                    file_size = int(st.st_size)
                if mtime is None:
                    mtime = float(st.st_mtime)
            except OSError:
                pass
        if format_hint is None:
            format_hint = format_hint_for_path(path)
        updated_at = _utc_now_iso()
        start_iso = _datetime_to_iso(start_datetime)
        end_iso = _datetime_to_iso(end_datetime)

        with self._lock:
            with self._connect() as conn:
                existing = conn.execute("SELECT consolidated_path FROM videos WHERE video_path = ?", (str(path),)).fetchone()
                consolidated = existing["consolidated_path"] if (preserve_consolidate and existing is not None) else None
                conn.execute(
                    """
                    INSERT INTO videos (
                        video_path, filename, stem, extension, category, source_root,
                        start_datetime, end_datetime, duration_sec, fps, width, height,
                        file_size, mtime, format_hint, duplicate_group_id, is_canonical, is_duplicate,
                        duplicate_reason, consolidated_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 0, NULL, ?, ?)
                    ON CONFLICT(video_path) DO UPDATE SET
                        filename = excluded.filename,
                        stem = excluded.stem,
                        extension = excluded.extension,
                        category = excluded.category,
                        source_root = excluded.source_root,
                        start_datetime = COALESCE(excluded.start_datetime, videos.start_datetime),
                        end_datetime = COALESCE(excluded.end_datetime, videos.end_datetime),
                        duration_sec = COALESCE(excluded.duration_sec, videos.duration_sec),
                        fps = COALESCE(excluded.fps, videos.fps),
                        width = COALESCE(excluded.width, videos.width),
                        height = COALESCE(excluded.height, videos.height),
                        file_size = COALESCE(excluded.file_size, videos.file_size),
                        mtime = COALESCE(excluded.mtime, videos.mtime),
                        format_hint = COALESCE(excluded.format_hint, videos.format_hint),
                        consolidated_path = CASE WHEN ? THEN videos.consolidated_path ELSE excluded.consolidated_path END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(path),
                        filename,
                        stem,
                        extension,
                        category,
                        source_root_str,
                        start_iso,
                        end_iso,
                        duration_sec,
                        fps,
                        width,
                        height,
                        file_size,
                        mtime,
                        format_hint,
                        consolidated,
                        updated_at,
                        1 if preserve_consolidate else 0,
                    ),
                )
                conn.commit()


    def discover_and_upsert(self, sources: Optional[Union[Sequence[SourceSpec], Path, str]] = None, *, video_extensions: Optional[List[str]] = None, use_cache: bool = True, force_rebuild: bool = False, min_age_s: float = DEFAULT_MIN_AGE_S, skip_incomplete: bool = True) -> Dict[str, int]:
        """Scan configured sources, parse metadata, upsert rows. Returns counts."""
        if sources is None or isinstance(sources, (str, Path)):
            specs, _dest = load_source_specs(sources)
        else:
            specs = list(sources)
        exts = video_extensions or list(VIDEO_EXTENSIONS)
        found = 0
        upserted = 0
        skipped_incomplete = 0
        skipped_missing = 0

        for spec in specs:
            root = Path(spec.path)
            if not root.exists() or not root.is_dir():
                skipped_missing += 1
                continue
            ## END if root missing....

            df = VideoMetadataParser.parse_video_folder(
                root,
                video_extensions=exts,
                use_cache=use_cache,
                force_rebuild=force_rebuild,
                recursive=spec.recursive,
                exclude_dirs=list(spec.exclude),
            )
            if df is None or df.empty:
                continue
            ## END if empty....

            for _, row in df.iterrows():
                raw_path = row.get("video_file_path")
                if raw_path is None or (isinstance(raw_path, float) and pd.isna(raw_path)):
                    continue
                path = Path(str(raw_path))
                found += 1
                if skip_incomplete and not is_recording_complete(path, min_age_s=min_age_s):
                    skipped_incomplete += 1
                    continue
                ## END if incomplete....

                start_dt = row.get("video_start_datetime")
                if isinstance(start_dt, pd.Timestamp):
                    start_dt = start_dt.to_pydatetime()
                duration = row.get("video_duration")
                try:
                    duration_f = float(duration) if duration is not None and pd.notna(duration) else None
                except (TypeError, ValueError):
                    duration_f = None

                meta: Dict[str, Any] = {
                    "start_datetime": start_dt if isinstance(start_dt, datetime) else None,
                    "duration_sec": duration_f,
                }
                meta = enrich_from_timing_sidecar(path, meta)
                start_dt = meta.get("start_datetime") if isinstance(meta.get("start_datetime"), datetime) else start_dt
                duration_f = meta.get("duration_sec", duration_f)
                end_dt = None
                if isinstance(start_dt, datetime) and duration_f is not None:
                    try:
                        end_dt = start_dt + timedelta(seconds=float(duration_f))
                    except (OverflowError, ValueError):
                        end_dt = row.get("video_end_datetime")
                        if isinstance(end_dt, pd.Timestamp):
                            end_dt = end_dt.to_pydatetime()
                else:
                    end_dt = row.get("video_end_datetime")
                    if isinstance(end_dt, pd.Timestamp):
                        end_dt = end_dt.to_pydatetime()

                fps = row.get("video_fps")
                width = row.get("video_width")
                height = row.get("video_height")
                file_size = row.get("video_file_size")
                try:
                    fps_f = float(fps) if fps is not None and pd.notna(fps) else None
                except (TypeError, ValueError):
                    fps_f = None
                try:
                    width_i = int(width) if width is not None and pd.notna(width) else None
                except (TypeError, ValueError):
                    width_i = None
                try:
                    height_i = int(height) if height is not None and pd.notna(height) else None
                except (TypeError, ValueError):
                    height_i = None
                try:
                    size_i = int(file_size) if file_size is not None and pd.notna(file_size) else None
                except (TypeError, ValueError):
                    size_i = None

                self.upsert_video(
                    video_path=path,
                    category=spec.category,
                    source_root=root,
                    start_datetime=start_dt if isinstance(start_dt, datetime) else None,
                    end_datetime=end_dt if isinstance(end_dt, datetime) else None,
                    duration_sec=float(duration_f) if duration_f is not None else None,
                    fps=fps_f,
                    width=width_i,
                    height=height_i,
                    file_size=size_i,
                    format_hint=format_hint_for_path(path),
                )
                upserted += 1
            ## END for _, row in df.iterrows()....
        ## END for spec in specs....

        return {
            "sources": len(specs),
            "found": found,
            "upserted": upserted,
            "skipped_incomplete": skipped_incomplete,
            "skipped_missing_roots": skipped_missing,
        }


    def load_videos_df(self) -> pd.DataFrame:
        with self._lock:
            with self._connect() as conn:
                df = pd.read_sql_query("SELECT * FROM videos ORDER BY start_datetime DESC, video_path", conn)
        if not df.empty:
            for col in ("start_datetime", "end_datetime"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
            ## END for col....
            df["is_canonical"] = df["is_canonical"].astype(bool)
            df["is_duplicate"] = df["is_duplicate"].astype(bool)
        return df


    def load_calendar_dataframe(self, include_duplicates: bool = False) -> pd.DataFrame:
        """Return a DataFrame shaped for ``CalendarDatasource`` video overlay rendering."""
        columns = [
            "video_path",
            "filename",
            "category",
            "recording_datetime",
            "recording_day_date",
            "duration_sec",
            "first_timestamp_rel",
            "duration_rel",
            "is_duplicate",
            "is_canonical",
            "consolidated_path",
        ]
        df = self.load_videos_df()
        if df.empty:
            return pd.DataFrame(columns=columns)
        if not include_duplicates and "is_duplicate" in df.columns:
            df = df.loc[~df["is_duplicate"]].copy()
        if df.empty:
            return pd.DataFrame(columns=columns)

        rec_dts: List[Optional[datetime]] = []
        day_dts: List[Optional[datetime]] = []
        first_rels: List[float] = []
        dur_rels: List[float] = []
        for row in df.itertuples():
            raw_start = getattr(row, "start_datetime", None)
            dt_val: Optional[datetime] = None
            if raw_start is not None and pd.notna(raw_start):
                if isinstance(raw_start, pd.Timestamp):
                    dt_val = raw_start.to_pydatetime()
                elif isinstance(raw_start, datetime):
                    dt_val = raw_start
            if dt_val is not None:
                if dt_val.tzinfo is None:
                    dt_val = dt_val.replace(tzinfo=timezone.utc)
                else:
                    dt_val = dt_val.astimezone(timezone.utc)
                day_start = dt_val.replace(hour=0, minute=0, second=0, microsecond=0)
                first_rel = float((dt_val - day_start).total_seconds()) / (24.0 * 3600.0)
            else:
                day_start = None
                first_rel = 0.1
            dur = getattr(row, "duration_sec", None)
            if dur is not None and pd.notna(dur):
                try:
                    dur_rel = max(0.01, min(1.0, float(dur) / (24.0 * 3600.0)))
                except (TypeError, ValueError):
                    dur_rel = 0.03
            else:
                dur_rel = 0.03
            rec_dts.append(dt_val)
            day_dts.append(day_start)
            first_rels.append(max(0.0, min(0.99, first_rel)))
            dur_rels.append(dur_rel)
        ## END for row in df.itertuples()....

        out = pd.DataFrame(
            {
                "video_path": df["video_path"].values,
                "filename": df["filename"].values,
                "category": df["category"].values,
                "recording_datetime": rec_dts,
                "recording_day_date": day_dts,
                "duration_sec": df["duration_sec"].values,
                "first_timestamp_rel": first_rels,
                "duration_rel": dur_rels,
                "is_duplicate": df["is_duplicate"].values,
                "is_canonical": df["is_canonical"].values,
                "consolidated_path": df["consolidated_path"].values if "consolidated_path" in df.columns else None,
            }
        )
        return out


    def load_duplicates_df(self) -> pd.DataFrame:
        df = self.load_videos_df()
        if df.empty or "duplicate_group_id" not in df.columns:
            return df
        grouped = df[df["duplicate_group_id"].notna() & (df["duplicate_group_id"] != "")]
        if grouped.empty:
            return grouped
        counts = grouped.groupby("duplicate_group_id")["video_path"].transform("count")
        return grouped.loc[counts >= 2].sort_values(["duplicate_group_id", "is_canonical", "video_path"], ascending=[True, False, True])


    def recompute_duplicates(self, duration_tol_sec: float = DEFAULT_DURATION_TOL_SEC) -> Dict[str, int]:
        """Assign duplicate groups; prefer .mkv then size then mtime as canonical."""
        df = self.load_videos_df()
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM duplicate_groups")
                conn.execute(
                    """
                    UPDATE videos SET duplicate_group_id = NULL, is_canonical = 1, is_duplicate = 0, duplicate_reason = NULL
                    """
                )
                conn.commit()

        if df.empty:
            return {"groups": 0, "duplicate_rows": 0}

        # Bucket by stem, then cluster by duration within tolerance
        groups_written = 0
        dup_rows = 0
        updates: List[Tuple[Any, ...]] = []
        group_rows: List[Tuple[Any, ...]] = []

        for stem, stem_df in df.groupby("stem", sort=False):
            members = stem_df.to_dict(orient="records")
            # Greedy clusters by duration
            clusters: List[List[Dict[str, Any]]] = []
            for row in members:
                dur = row.get("duration_sec")
                if dur is None or (isinstance(dur, float) and pd.isna(dur)):
                    # Unique singleton without duration linkage
                    clusters.append([row])
                    continue
                placed = False
                for cluster in clusters:
                    ref = cluster[0].get("duration_sec")
                    if ref is None or (isinstance(ref, float) and pd.isna(ref)):
                        continue
                    if abs(float(dur) - float(ref)) <= float(duration_tol_sec):
                        cluster.append(row)
                        placed = True
                        break
                    ## END if within tol....
                ## END for cluster in clusters....

                if not placed:
                    clusters.append([row])
                ## END if not placed....
            ## END for row in members....

            for cluster in clusters:
                if len(cluster) == 1:
                    row = cluster[0]
                    updates.append((None, 1, 0, None, _utc_now_iso(), row["video_path"]))
                    continue
                ## END if singleton....

                # Representative duration = mean of available
                durs = [float(r["duration_sec"]) for r in cluster if r.get("duration_sec") is not None and pd.notna(r.get("duration_sec"))]
                rep_dur = sum(durs) / len(durs) if durs else None
                gid = _group_id_for(str(stem), float(rep_dur or 0.0))
                keeper = max(cluster, key=_keeper_sort_key)
                keeper_path = keeper["video_path"]
                has_mkv = any(str(r.get("extension") or "").lower() == ".mkv" for r in cluster)
                has_non_mkv = any(str(r.get("extension") or "").lower() != ".mkv" for r in cluster)
                mixed_container = has_mkv and has_non_mkv

                groups_written += 1
                group_rows.append((gid, str(stem), rep_dur, keeper_path, len(cluster)))

                for row in cluster:
                    is_keep = row["video_path"] == keeper_path
                    if is_keep:
                        reason = None
                        updates.append((gid, 1, 0, None, _utc_now_iso(), row["video_path"]))
                    else:
                        dup_rows += 1
                        if mixed_container and str(row.get("extension") or "").lower() != ".mkv" and str(keeper.get("extension") or "").lower() == ".mkv":
                            reason = "converted_container"
                        else:
                            reason = "cross_location_copy"
                        updates.append((gid, 0, 1, reason, _utc_now_iso(), row["video_path"]))
                    ## END if is_keep....
                ## END for row in cluster....
            ## END for cluster in clusters....
        ## END for stem, stem_df....

        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    UPDATE videos SET
                        duplicate_group_id = ?,
                        is_canonical = ?,
                        is_duplicate = ?,
                        duplicate_reason = ?,
                        updated_at = ?
                    WHERE video_path = ?
                    """,
                    updates,
                )
                if group_rows:
                    conn.executemany(
                        """
                        INSERT INTO duplicate_groups (group_id, stem, duration_sec, canonical_path, member_count)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(group_id) DO UPDATE SET
                            stem = excluded.stem,
                            duration_sec = excluded.duration_sec,
                            canonical_path = excluded.canonical_path,
                            member_count = excluded.member_count
                        """,
                        group_rows,
                    )
                conn.commit()

        return {"groups": groups_written, "duplicate_rows": dup_rows}


    def export_review_csv(self, path: Union[str, Path]) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df = self.load_videos_df()
        df.to_csv(out, index=False, encoding="utf-8")
        return out


    def print_duplicate_report(self) -> None:
        dups = self.load_duplicates_df()
        if dups.empty:
            print("No duplicate groups.")
            return
        ## END if dups.empty....

        for gid, group in dups.groupby("duplicate_group_id", sort=True):
            stem = group.iloc[0]["stem"]
            dur = group.iloc[0]["duration_sec"]
            print(f"* group={gid}  stem={stem}  dur={dur}  (x{len(group)})")
            ordered = group.sort_values(["is_canonical", "filename"], ascending=[False, True])
            for _, row in ordered.iterrows():
                label = "KEEP  " if bool(row["is_canonical"]) else "DUP   "
                reason = row.get("duplicate_reason") or ""
                extra = f"  reason={reason}" if reason else ""
                print(f"    {label}{row['filename']}  {row['video_path']}{extra}")
            ## END for _, row in ordered.iterrows()....

            print()
        ## END for gid, group....


    def consolidate(self, dest_root: Optional[Union[str, Path]] = None, *, dry_run: bool = False, only_canonical: bool = True) -> Dict[str, int]:
        """Copy canonical videos into ``{dest_root}/{category}/``."""
        if dest_root is None:
            _specs, dest_root = load_source_specs()
        dest_root = Path(dest_root)
        df = self.load_videos_df()
        if df.empty:
            return {"planned": 0, "copied": 0, "skipped": 0, "failed": 0}
        if only_canonical:
            df = df[df["is_canonical"].astype(bool)]
        ## END if only_canonical....

        planned = 0
        copied = 0
        skipped = 0
        failed = 0

        for _, row in df.iterrows():
            src = Path(row["video_path"])
            category = str(row["category"])
            category_dir = dest_root / category
            if not src.is_file():
                skipped += 1
                continue
            ## END if missing source....

            # If already consolidated to an existing path, skip
            existing_dest = row.get("consolidated_path")
            if existing_dest and Path(str(existing_dest)).is_file():
                skipped += 1
                continue
            ## END if already consolidated....

            dest = unique_dest_path(category_dir, src.name)
            # Prefer short hash suffix when basename would collide with a different source
            if dest.name != src.name or (category_dir / src.name).exists():
                # unique_dest_path already chose stem_N; if same basename exists as identical file, skip
                candidate_same = category_dir / src.name
                if candidate_same.is_file():
                    try:
                        if candidate_same.resolve() != src.resolve() and candidate_same.stat().st_size == src.stat().st_size:
                            # Different path, same size — still use unique name
                            pass
                    except OSError:
                        pass
            planned += 1
            print(f"{'[dry-run] ' if dry_run else ''}{src} -> {dest}")
            if dry_run:
                continue
            ## END if dry_run....

            try:
                category_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                with self._lock:
                    with self._connect() as conn:
                        conn.execute(
                            "UPDATE videos SET consolidated_path = ?, updated_at = ? WHERE video_path = ?",
                            (str(dest), _utc_now_iso(), str(src.resolve())),
                        )
                        conn.commit()
                copied += 1
            except OSError as exc:
                print(f"  ! Copy failed: {exc}")
                failed += 1
            ## END try copy....
        ## END for _, row in df.iterrows()....

        return {"planned": planned, "copied": copied, "skipped": skipped, "failed": failed}


__all__ = [
    "SourceSpec",
    "VideoCatalog",
    "VIDEO_EXTENSIONS",
    "DEFAULT_CONSOLIDATE_ROOT",
    "default_video_catalog_path",
    "default_sources_yaml_path",
    "load_source_specs",
]
