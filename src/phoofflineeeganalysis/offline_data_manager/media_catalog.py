"""Durable SQLite catalog of multi-modal media files (video, screenshots, audio): discover, dedupe, consolidate."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

import pandas as pd
from phopylslhelper.file_metadata_caching.video_metadata import VideoMetadataParser

from phoofflineeeganalysis.offline_data_manager.media_modalities import (
    MEDIA_CATEGORIES,
    VIDEO_EXTENSIONS,
    extensions_for_category,
    media_kind_for_category,
)
from phoofflineeeganalysis.offline_data_manager.video_ingest_helpers import (
    DEFAULT_MIN_AGE_S,
    DUP_DIR_NAME,
    enrich_from_timing_sidecar,
    exclusive_open_succeeds,
    format_hint_for_path,
    is_recording_complete,
    parse_media_timestamp,
    probe_image_dimensions,
    probe_wav_duration_sec,
    unique_dest_path,
)

DEFAULT_DURATION_TOL_SEC = 1.0
DEFAULT_CONSOLIDATE_ROOT = Path(r"H:\PhoBehaviorProject\RECORDINGS")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS videos (
    video_path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    stem TEXT NOT NULL,
    extension TEXT NOT NULL,
    category TEXT NOT NULL,
    media_kind TEXT NOT NULL DEFAULT 'video',
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
        project_root = Path(__file__).resolve().parents[3]
    out = Path(project_root) / "output"
    out.mkdir(parents=True, exist_ok=True)
    return out / "video_catalog.sqlite"


default_media_catalog_path = default_video_catalog_path


def default_sources_yaml_path(project_root: Optional[Path] = None) -> Path:
    """Return preferred media_sources.yaml (falling back to legacy video_sources.yaml)."""
    if project_root is None:
        project_root = Path(__file__).resolve().parents[3]
    root = Path(project_root)
    candidates = [
        root / "config" / "offline_data_manager" / "media_sources.yaml",
        root / "config" / "media_sources.yaml",
        root / "config" / "video_sources.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    ## END for candidate in candidates...

    return candidates[0]


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
    """Minimal loader for media_sources.yaml / video_sources.yaml shape when PyYAML is unavailable."""
    categories: Dict[str, List[Dict[str, Any]]] = {}
    consolidate: Dict[str, Any] = {}
    section: Optional[str] = None
    current_category: Optional[str] = None
    current_entry: Optional[Dict[str, Any]] = None
    in_exclude = False

    def _flush_entry() -> None:
        nonlocal current_entry
        if current_category is not None and current_entry is not None:
            categories.setdefault(current_category, []).append(current_entry)
        current_entry = None

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            if line == "categories:":
                _flush_entry()
                section = "categories"
                current_category = None
                in_exclude = False
                continue

            if line == "consolidate:":
                _flush_entry()
                section = "consolidate"
                current_category = None
                in_exclude = False
                continue

            if section == "consolidate":
                if ":" in line:
                    key, _, val = line.strip().partition(":")
                    consolidate[key.strip()] = _parse_scalar(val)
                continue

            if section != "categories":
                continue

            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
                _flush_entry()
                current_category = stripped[:-1].strip()
                in_exclude = False
                continue

            if stripped.startswith("- path:"):
                _flush_entry()
                in_exclude = False
                current_entry = {"path": _parse_scalar(stripped[len("- path:"):]), "recursive": False, "exclude": []}
                continue

            if current_entry is None:
                continue

            if stripped.startswith("recursive:"):
                current_entry["recursive"] = bool(_parse_scalar(stripped[len("recursive:"):]))
                in_exclude = False
                continue

            if stripped == "exclude:":
                in_exclude = True
                current_entry.setdefault("exclude", [])
                continue

            if in_exclude and stripped.startswith("- "):
                current_entry.setdefault("exclude", []).append(_parse_scalar(stripped[2:]))
                continue

            in_exclude = False
        ## END for raw_line in f...

    _flush_entry()
    return {"categories": categories, "consolidate": consolidate}


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        data = _load_video_sources_fallback(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _quote_yaml_str(val: str) -> str:
    escaped = str(val).replace("\\", "/").replace('"', '\\"')
    return f'"{escaped}"'


def save_source_specs(specs: Sequence[SourceSpec], *, dest_root: Optional[Union[str, Path]] = None, sources_yaml: Optional[Union[str, Path]] = None) -> Path:
    """Serialize *specs* and *dest_root* into a ``media_sources.yaml``-shaped YAML file."""
    target = Path(sources_yaml) if sources_yaml is not None else default_sources_yaml_path()
    if dest_root is None and target.exists():
        try:
            _existing, existing_dest = load_source_specs(target)
            dest_root = existing_dest
        except Exception:
            dest_root = DEFAULT_CONSOLIDATE_ROOT
    resolved_dest = Path(dest_root) if dest_root is not None else DEFAULT_CONSOLIDATE_ROOT

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for cat in MEDIA_CATEGORIES:
        grouped[cat] = []
    ## END for cat in MEDIA_CATEGORIES...

    for spec in specs:
        entry: Dict[str, Any] = {
            "path": Path(spec.path).as_posix(),
            "recursive": bool(spec.recursive),
        }
        if spec.exclude:
            entry["exclude"] = [Path(ex).as_posix() for ex in spec.exclude]
        grouped.setdefault(str(spec.category), []).append(entry)
    ## END for spec in specs...

    categories_payload = {cat: entries for cat, entries in grouped.items() if entries}
    payload: Dict[str, Any] = {
        "categories": categories_payload,
        "consolidate": {"dest_root": resolved_dest.as_posix()},
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        with open(target, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False)
        return target
    except ImportError:
        pass

    lines: List[str] = ["categories:"]
    for cat, entries in categories_payload.items():
        lines.append(f"  {cat}:")
        for entry in entries:
            lines.append(f"    - path: {_quote_yaml_str(entry['path'])}")
            lines.append(f"      recursive: {'true' if entry.get('recursive') else 'false'}")
            excludes = entry.get("exclude") or []
            if excludes:
                lines.append("      exclude:")
                for ex in excludes:
                    lines.append(f"        - {_quote_yaml_str(ex)}")
                ## END for ex in excludes...
        ## END for entry in entries...
    ## END for cat, entries in categories_payload.items()...

    lines.append("")
    lines.append("consolidate:")
    lines.append(f"  dest_root: {_quote_yaml_str(resolved_dest.as_posix())}")
    lines.append("")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return target


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
        ## END for entry in entries...
    ## END for category, entries in categories.items()...

    consolidate = data.get("consolidate") or {}
    dest_root = Path(consolidate.get("dest_root") or DEFAULT_CONSOLIDATE_ROOT)
    return specs, dest_root


def _norm_fs_str(path: Union[str, Path]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _is_excluded_path(candidate: Path, exclude_norms: Sequence[str]) -> bool:
    if DUP_DIR_NAME in candidate.parts:
        return True
    cand_norm = _norm_fs_str(candidate)
    for ex_norm in exclude_norms:
        if cand_norm == ex_norm or cand_norm.startswith(ex_norm + os.sep):
            return True
    ## END for ex_norm in exclude_norms...

    return False


def _iter_non_video_files(root: Path, *, recursive: bool, exclude_dirs: Sequence[Path], allowed_exts: Sequence[str]) -> Iterator[Path]:
    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in allowed_exts}
    exclude_norms = [_norm_fs_str(ex) for ex in exclude_dirs]
    if not recursive:
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if not entry.is_file():
                continue
            if _is_excluded_path(entry, exclude_norms):
                continue
            if entry.suffix.lower() in ext_set:
                yield entry
        ## END for entry in entries...

        return

    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        dirnames[:] = [
            d for d in sorted(dirnames, key=str.lower)
            if d != DUP_DIR_NAME and not _is_excluded_path(dir_path / d, exclude_norms)
        ]
        for fname in sorted(filenames, key=str.lower):
            fpath = dir_path / fname
            if _is_excluded_path(fpath, exclude_norms):
                continue
            if fpath.suffix.lower() in ext_set:
                yield fpath
        ## END for fname in sorted(filenames, key=str.lower)...
    ## END for dirpath, dirnames, filenames in os.walk(root)...


def _keeper_sort_key(row: Dict[str, Any]) -> Tuple[int, int, float, float]:
    """Prefer .mkv, then larger size, then newer mtime."""
    ext = str(row.get("extension") or "").lower()
    is_mkv = 1 if ext == ".mkv" else 0
    size = float(row.get("file_size") or 0)
    mtime = float(row.get("mtime") or 0)
    return (is_mkv, 1, size, mtime)


def _group_id_for(stem: str, discriminator: str) -> str:
    raw = f"{stem}|{discriminator}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class MediaCatalog:
    """Thread-safe SQLite catalog of multi-modal media files (video, screenshots, audio) across multi-root sources."""

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
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
                if "media_kind" not in cols:
                    conn.execute("ALTER TABLE videos ADD COLUMN media_kind TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_media_kind ON videos(media_kind)")
                missing_rows = conn.execute(
                    "SELECT video_path, category, extension FROM videos WHERE media_kind IS NULL OR media_kind = ''"
                ).fetchall()
                if missing_rows:
                    backfill = [
                        (media_kind_for_category(r["category"], r["extension"]), r["video_path"])
                        for r in missing_rows
                    ]
                    conn.executemany("UPDATE videos SET media_kind = ? WHERE video_path = ?", backfill)
                conn.commit()


    def upsert_video(self, *, video_path: Union[str, Path], category: str, source_root: Union[str, Path],
                     media_kind: Optional[str] = None, start_datetime: Optional[datetime] = None, end_datetime: Optional[datetime] = None,
                     duration_sec: Optional[float] = None, fps: Optional[float] = None, width: Optional[int] = None, height: Optional[int] = None,
                     file_size: Optional[int] = None, mtime: Optional[float] = None, format_hint: Optional[str] = None, preserve_consolidate: bool = True) -> None:
        path = Path(video_path).resolve()
        filename = path.name
        stem = path.stem
        extension = path.suffix.lower()
        resolved_kind = media_kind or media_kind_for_category(category, extension)
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
        start_iso = _datetime_to_iso(start_datetime)
        end_iso = _datetime_to_iso(end_datetime)
        updated_at = _utc_now_iso()

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    INSERT INTO videos (
                        video_path, filename, stem, extension, category, media_kind, source_root,
                        start_datetime, end_datetime, duration_sec, fps, width, height,
                        file_size, mtime, format_hint, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_path) DO UPDATE SET
                        filename = excluded.filename,
                        stem = excluded.stem,
                        extension = excluded.extension,
                        category = excluded.category,
                        media_kind = excluded.media_kind,
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
                        {"consolidated_path = videos.consolidated_path," if preserve_consolidate else ""}
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(path),
                        filename,
                        stem,
                        extension,
                        category,
                        resolved_kind,
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
                        updated_at,
                    ),
                )
                conn.commit()


    def upsert_media(self, *, media_path: Optional[Union[str, Path]] = None, video_path: Optional[Union[str, Path]] = None, **kwargs: Any) -> None:
        """Alias wrapper around ``upsert_video`` accepting ``media_path`` or ``video_path``."""
        target = media_path if media_path is not None else video_path
        if target is None:
            raise ValueError("Either media_path or video_path is required")
        self.upsert_video(video_path=target, **kwargs)


    def discover_and_upsert(self, sources: Optional[Union[Sequence[SourceSpec], Path, str]] = None, *, video_extensions: Optional[List[str]] = None, use_cache: bool = True, force_rebuild: bool = False, min_age_s: float = DEFAULT_MIN_AGE_S, skip_incomplete: bool = True) -> Dict[str, int]:
        """Scan configured sources across video, screenshot, and audio categories and upsert rows."""
        if sources is None or isinstance(sources, (str, Path)):
            specs, _dest = load_source_specs(sources)
        else:
            specs = list(sources)

        found = 0
        upserted = 0
        skipped_incomplete = 0
        skipped_missing = 0
        by_kind: Dict[str, int] = {"video": 0, "screenshot": 0, "audio": 0}

        for spec in specs:
            root = Path(spec.path)
            if not root.exists() or not root.is_dir():
                skipped_missing += 1
                continue

            kind = media_kind_for_category(spec.category)
            if kind == "video":
                exts = video_extensions or list(VIDEO_EXTENSIONS)
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

                for _, row in df.iterrows():
                    raw_path = row.get("video_file_path")
                    if raw_path is None or (isinstance(raw_path, float) and pd.isna(raw_path)):
                        continue
                    path = Path(str(raw_path))
                    found += 1
                    if skip_incomplete and not is_recording_complete(path, min_age_s=min_age_s):
                        skipped_incomplete += 1
                        continue

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
                    fps_f = float(fps) if fps is not None and pd.notna(fps) else None
                    width_i = int(width) if width is not None and pd.notna(width) else None
                    height_i = int(height) if height is not None and pd.notna(height) else None
                    size_i = int(file_size) if file_size is not None and pd.notna(file_size) else None

                    self.upsert_video(
                        video_path=path,
                        category=spec.category,
                        media_kind="video",
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
                    by_kind["video"] += 1
                ## END for _, row in df.iterrows()...

                continue

            allowed_exts = video_extensions if video_extensions is not None else extensions_for_category(spec.category)
            now_s = time.time()
            for path in _iter_non_video_files(root, recursive=spec.recursive, exclude_dirs=spec.exclude, allowed_exts=allowed_exts):
                found += 1
                try:
                    st = path.stat()
                except OSError:
                    skipped_incomplete += 1
                    continue
                if st.st_size <= 0:
                    skipped_incomplete += 1
                    continue
                if kind == "audio" and skip_incomplete:
                    if float(min_age_s) > 0.0 and (now_s - float(st.st_mtime)) < float(min_age_s):
                        skipped_incomplete += 1
                        continue
                    if not exclusive_open_succeeds(path):
                        skipped_incomplete += 1
                        continue

                start_dt = parse_media_timestamp(path, mtime=float(st.st_mtime))
                duration_f: Optional[float] = None
                end_dt: Optional[datetime] = None
                width_i: Optional[int] = None
                height_i: Optional[int] = None

                if kind == "audio":
                    duration_f = probe_wav_duration_sec(path)
                    if start_dt is not None and duration_f is not None:
                        try:
                            end_dt = start_dt + timedelta(seconds=float(duration_f))
                        except (OverflowError, ValueError):
                            end_dt = None
                elif kind == "screenshot":
                    width_i, height_i = probe_image_dimensions(path)

                self.upsert_video(
                    video_path=path,
                    category=spec.category,
                    media_kind=kind,
                    source_root=root,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    duration_sec=duration_f,
                    width=width_i,
                    height=height_i,
                    file_size=int(st.st_size),
                    mtime=float(st.st_mtime),
                    format_hint=kind,
                )
                upserted += 1
                by_kind[kind] = by_kind.get(kind, 0) + 1
            ## END for path in _iter_non_video_files(...)...
        ## END for spec in specs...

        return {
            "sources": len(specs),
            "found": found,
            "upserted": upserted,
            "skipped_incomplete": skipped_incomplete,
            "skipped_missing_roots": skipped_missing,
            "upserted_video": by_kind.get("video", 0),
            "upserted_screenshot": by_kind.get("screenshot", 0),
            "upserted_audio": by_kind.get("audio", 0),
        }


    def load_videos_df(self) -> pd.DataFrame:
        with self._lock:
            with self._connect() as conn:
                df = pd.read_sql_query("SELECT * FROM videos ORDER BY start_datetime DESC, video_path", conn)
        if not df.empty:
            for col in ("start_datetime", "end_datetime"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
            ## END for col in ("start_datetime", "end_datetime")...

            if "media_kind" not in df.columns:
                df["media_kind"] = [media_kind_for_category(c, e) for c, e in zip(df["category"], df["extension"])]
            else:
                df["media_kind"] = [
                    mk if isinstance(mk, str) and mk else media_kind_for_category(c, e)
                    for mk, c, e in zip(df["media_kind"], df["category"], df["extension"])
                ]
            df["is_canonical"] = df["is_canonical"].astype(bool)
            df["is_duplicate"] = df["is_duplicate"].astype(bool)
        return df


    load_media_df = load_videos_df


    def load_calendar_dataframe(self, include_duplicates: bool = False, categories: Optional[Sequence[str]] = None, media_kinds: Optional[Sequence[str]] = None) -> pd.DataFrame:
        """Return a DataFrame shaped for ``CalendarDatasource`` media overlay rendering."""
        columns = [
            "video_path",
            "filename",
            "category",
            "media_kind",
            "source_root",
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
        if categories is not None:
            cat_set = {str(c) for c in categories}
            df = df.loc[df["category"].astype(str).isin(cat_set)].copy()
        if media_kinds is not None:
            kind_set = {str(k).lower() for k in media_kinds}
            df = df.loc[df["media_kind"].astype(str).str.lower().isin(kind_set)].copy()
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
            mkind = str(getattr(row, "media_kind", "video") or "video")
            default_stub = 0.015 if mkind == "screenshot" else 0.03
            dur = getattr(row, "duration_sec", None)
            if dur is not None and pd.notna(dur):
                try:
                    dur_rel = max(0.01, min(1.0, float(dur) / (24.0 * 3600.0)))
                except (TypeError, ValueError):
                    dur_rel = default_stub
            else:
                dur_rel = default_stub
            rec_dts.append(dt_val)
            day_dts.append(day_start)
            first_rels.append(max(0.0, min(0.99, first_rel)))
            dur_rels.append(dur_rel)
        ## END for row in df.itertuples()...

        out = pd.DataFrame(
            {
                "video_path": df["video_path"].values,
                "filename": df["filename"].values,
                "category": df["category"].values,
                "media_kind": df["media_kind"].values,
                "source_root": df["source_root"].values if "source_root" in df.columns else None,
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


    def source_summary_by_root(self) -> Dict[str, Dict[str, Any]]:
        """Return ``{norm_source_root: {'count': int, 'last_scanned_at': str|None}}`` from SQLite."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT source_root, COUNT(*) AS cnt, MAX(updated_at) AS last_up FROM videos GROUP BY source_root"
                ).fetchall()
        summary: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            raw_root = row["source_root"]
            if not raw_root:
                continue
            key = _norm_fs_str(raw_root)
            prev = summary.get(key)
            cnt = int(row["cnt"] or 0)
            last_up = row["last_up"]
            if prev is None:
                summary[key] = {"count": cnt, "last_scanned_at": last_up}
            else:
                prev["count"] = int(prev["count"]) + cnt
                if last_up and (prev["last_scanned_at"] is None or str(last_up) > str(prev["last_scanned_at"])):
                    prev["last_scanned_at"] = last_up
        ## END for row in rows...

        return summary


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
        """Assign duplicate groups: stem+duration for video/timed media, category+stem+size for untimed screenshots/audio."""
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

        groups_written = 0
        dup_rows = 0
        updates: List[Tuple[Any, ...]] = []
        group_rows: List[Tuple[Any, ...]] = []

        for stem, stem_df in df.groupby("stem", sort=False):
            members = stem_df.to_dict(orient="records")
            timed_clusters: List[List[Dict[str, Any]]] = []
            untimed_buckets: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
            singleton_rows: List[Dict[str, Any]] = []

            for row in members:
                dur = row.get("duration_sec")
                mkind = str(row.get("media_kind") or "video")
                if dur is not None and not (isinstance(dur, float) and pd.isna(dur)):
                    placed = False
                    for cluster in timed_clusters:
                        ref = cluster[0].get("duration_sec")
                        if ref is None or (isinstance(ref, float) and pd.isna(ref)):
                            continue
                        if abs(float(dur) - float(ref)) <= float(duration_tol_sec):
                            cluster.append(row)
                            placed = True
                            break
                    ## END for cluster in timed_clusters...

                    if not placed:
                        timed_clusters.append([row])
                    continue

                fsize = row.get("file_size")
                if mkind in ("screenshot", "audio") and fsize is not None and not (isinstance(fsize, float) and pd.isna(fsize)) and int(fsize) > 0:
                    bkey = (str(row.get("category") or ""), int(fsize))
                    untimed_buckets.setdefault(bkey, []).append(row)
                else:
                    singleton_rows.append(row)
            ## END for row in members...

            all_clusters: List[Tuple[List[Dict[str, Any]], str]] = []
            for cluster in timed_clusters:
                durs = [float(r["duration_sec"]) for r in cluster if r.get("duration_sec") is not None and pd.notna(r.get("duration_sec"))]
                rep_dur = sum(durs) / len(durs) if durs else 0.0
                all_clusters.append((cluster, f"{rep_dur:.3f}"))
            ## END for cluster in timed_clusters...

            for (cat_name, fsize_val), cluster in untimed_buckets.items():
                all_clusters.append((cluster, f"{cat_name}|size={fsize_val}"))
            ## END for (cat_name, fsize_val), cluster in untimed_buckets.items()...

            for s_row in singleton_rows:
                all_clusters.append(([s_row], "singleton"))
            ## END for s_row in singleton_rows...

            for cluster, disc in all_clusters:
                if len(cluster) == 1:
                    row = cluster[0]
                    updates.append((None, 1, 0, None, _utc_now_iso(), row["video_path"]))
                    continue

                durs = [float(r["duration_sec"]) for r in cluster if r.get("duration_sec") is not None and pd.notna(r.get("duration_sec"))]
                rep_dur = sum(durs) / len(durs) if durs else None
                gid = _group_id_for(str(stem), disc)
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
                        updates.append((gid, 1, 0, None, _utc_now_iso(), row["video_path"]))
                    else:
                        dup_rows += 1
                        if mixed_container and str(row.get("extension") or "").lower() != ".mkv" and str(keeper.get("extension") or "").lower() == ".mkv":
                            reason = "converted_container"
                        else:
                            reason = "cross_location_copy"
                        updates.append((gid, 0, 1, reason, _utc_now_iso(), row["video_path"]))
                ## END for row in cluster...
            ## END for cluster, disc in all_clusters...
        ## END for stem, stem_df in df.groupby("stem", sort=False)...

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
            ## END for _, row in ordered.iterrows()...

            print()
        ## END for gid, group in dups.groupby("duplicate_group_id", sort=True)...


    def consolidate(self, dest_root: Optional[Union[str, Path]] = None, *, dry_run: bool = False, only_canonical: bool = True) -> Dict[str, int]:
        """Copy canonical media files into ``{dest_root}/{category}/``."""
        if dest_root is None:
            _specs, dest_root = load_source_specs()
        dest_root = Path(dest_root)
        df = self.load_videos_df()
        if df.empty:
            return {"planned": 0, "copied": 0, "skipped": 0, "failed": 0}
        if only_canonical:
            df = df[df["is_canonical"].astype(bool)]

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

            existing_dest = row.get("consolidated_path")
            if existing_dest and Path(str(existing_dest)).is_file():
                skipped += 1
                continue

            dest = unique_dest_path(category_dir, src.name)
            planned += 1
            print(f"{'[dry-run] ' if dry_run else ''}{src} -> {dest}")
            if dry_run:
                continue

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
        ## END for _, row in df.iterrows()...

        return {"planned": planned, "copied": copied, "skipped": skipped, "failed": failed}


VideoCatalog = MediaCatalog

__all__ = [
    "SourceSpec",
    "MediaCatalog",
    "VideoCatalog",
    "VIDEO_EXTENSIONS",
    "DEFAULT_CONSOLIDATE_ROOT",
    "DEFAULT_DURATION_TOL_SEC",
    "default_media_catalog_path",
    "default_video_catalog_path",
    "default_sources_yaml_path",
    "load_source_specs",
    "save_source_specs",
]
