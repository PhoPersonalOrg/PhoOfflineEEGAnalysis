"""Completeness gating, CAM sidecar enrichment, and non-video media metadata helpers."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DUP_DIR_NAME = "_DUP"
TIMING_SIDECAR_SUFFIX = ".timing.json"
DEFAULT_MIN_AGE_S = 10.0

_CAM_RE = re.compile(r"^CAM_(\d{4}-\d{2}-\d{2}T\d{6})$", re.IGNORECASE)
_DEBUT_RE = re.compile(r"^Debut_\d{4}-\d{2}-\d{2}T\d{6}", re.IGNORECASE)
_ISO_STEM_PATTERNS = (
    (re.compile(r"(\d{4}-\d{2}-\d{2})[T_ -](\d{2})[-.](\d{2})[-.](\d{2})"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"(\d{4}-\d{2}-\d{2})[T_](\d{2})(\d{2})(\d{2})"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"), "compact"),
)


def sidecar_path_for(recording_file: Path) -> Path:
    """``file.ext`` -> ``file.ext.timing.json``."""
    return recording_file.with_suffix(recording_file.suffix + TIMING_SIDECAR_SUFFIX)


def read_timing_sidecar(recording_file: Path) -> Optional[Dict[str, Any]]:
    """Load timing sidecar JSON next to *recording_file*, or None."""
    sidecar = sidecar_path_for(recording_file)
    if not sidecar.is_file():
        return None
    try:
        with open(sidecar, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def is_cam_filename(path: Path) -> bool:
    return _CAM_RE.match(path.stem) is not None


def is_debut_filename(path: Path) -> bool:
    return _DEBUT_RE.match(path.stem) is not None


def format_hint_for_path(path: Path) -> str:
    if is_cam_filename(path):
        return "cam"
    if is_debut_filename(path):
        return "debut"
    return "other"


def sidecar_is_finalized(recording_file: Path) -> bool:
    """True when the CAM timing sidecar includes a stop timestamp."""
    sidecar = read_timing_sidecar(recording_file)
    if sidecar is None:
        return False
    return sidecar.get("wall_clock_stop_unix") is not None


def exclusive_open_succeeds(path: Path) -> bool:
    """True when this process can open *path* with no sharing (writer has closed)."""
    if not path.is_file():
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            generic_read = 0x80000000
            open_existing = 3
            file_attribute_normal = 0x80
            file_share_none = 0
            invalid_handle = ctypes.c_void_p(-1).value
            create_file = ctypes.windll.kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                str(path),
                generic_read,
                file_share_none,
                None,
                open_existing,
                file_attribute_normal,
                None,
            )
            if handle == invalid_handle or handle is None:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (OSError, AttributeError, ValueError, OverflowError):
            return False

    try:
        fd = os.open(path, os.O_RDONLY)
        os.close(fd)
        return True
    except OSError:
        return False


def is_recording_complete(path: Path, *, min_age_s: float = DEFAULT_MIN_AGE_S, now: Optional[float] = None, require_exclusive_open: bool = True) -> bool:
    """Return True when *path* looks finished writing (batch-ingest friendly)."""
    path = Path(path)
    if DUP_DIR_NAME in path.parts:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    if not path.is_file() or st.st_size <= 0:
        return False
    clock = time.time() if now is None else now
    if (clock - float(st.st_mtime)) < float(min_age_s):
        return False

    if is_cam_filename(path):
        sidecar = sidecar_path_for(path)
        if sidecar.is_file():
            return sidecar_is_finalized(path)
        if require_exclusive_open and not exclusive_open_succeeds(path):
            return False

    return True


def enrich_from_timing_sidecar(path: Path, row: Dict[str, Any]) -> Dict[str, Any]:
    """Override start/duration fields from CAM timing sidecar when present."""
    out = dict(row)
    sidecar_file = sidecar_path_for(path)
    sidecar = read_timing_sidecar(path)
    if sidecar is None:
        if sidecar_file.is_file():
            out["timing_sidecar"] = str(sidecar_file)
        return out

    out["timing_sidecar"] = str(sidecar_file)
    wall_unix = sidecar.get("wall_clock_start_unix")
    if wall_unix is not None:
        try:
            utc_dt = datetime.fromtimestamp(float(wall_unix), tz=timezone.utc)
            out["start_datetime"] = utc_dt.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            pass

    dur = sidecar.get("duration_perf_seconds")
    if dur is not None:
        try:
            out["duration_sec"] = float(dur)
            out["duration_perf_seconds"] = float(dur)
        except (TypeError, ValueError):
            pass

    if sidecar.get("lsl_local_clock_start") is not None:
        out["lsl_local_clock_start"] = sidecar["lsl_local_clock_start"]

    if sidecar.get("segment_index") is not None:
        out["segment_index"] = sidecar["segment_index"]

    return out


def parse_media_timestamp(path: Path, mtime: Optional[float] = None) -> Optional[datetime]:
    """Parse recording start datetime from filename when obvious, falling back to UTC mtime."""
    stem = path.stem
    for regex, fmt in _ISO_STEM_PATTERNS:
        m = regex.search(stem)
        if m is None:
            continue
        try:
            if fmt == "compact":
                y, mo, d, hh, mm, ss = (int(g) for g in m.groups())
                return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc)
            date_part, hh, mm, ss = m.groups()
            parsed = datetime.strptime(f"{date_part} {hh}:{mm}:{ss}", fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    ## END for regex, fmt in _ISO_STEM_PATTERNS...

    ts = mtime
    if ts is None:
        try:
            ts = float(path.stat().st_mtime)
        except OSError:
            return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def probe_wav_duration_sec(path: Path) -> Optional[float]:
    """Best-effort stdlib duration probe for ``.wav`` audio files (no external deps)."""
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.getnframes()
            if rate > 0 and frames >= 0:
                return float(frames) / float(rate)
    except (OSError, wave.Error, EOFError):
        return None
    return None


def probe_image_dimensions(path: Path) -> Tuple[Optional[int], Optional[int]]:
    """Best-effort width/height probe for screenshots when Pillow is installed."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
            return int(w), int(h)
    except Exception:
        return None, None


def unique_dest_path(dest_dir: Path, filename: str) -> Path:
    """Return dest_dir/filename, or dest_dir/stem_N.suffix if that name exists."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


__all__ = [
    "DUP_DIR_NAME",
    "DEFAULT_MIN_AGE_S",
    "sidecar_path_for",
    "read_timing_sidecar",
    "is_cam_filename",
    "is_debut_filename",
    "format_hint_for_path",
    "sidecar_is_finalized",
    "exclusive_open_succeeds",
    "is_recording_complete",
    "enrich_from_timing_sidecar",
    "parse_media_timestamp",
    "probe_wav_duration_sec",
    "probe_image_dimensions",
    "unique_dest_path",
]
