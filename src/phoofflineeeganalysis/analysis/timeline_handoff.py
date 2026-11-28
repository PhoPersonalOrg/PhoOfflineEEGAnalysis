"""Handoff from the session calendar to pyPhoTimeline offline viewer.

Tries the running offline QLocalServer first; otherwise cold-starts
``main_offline_timeline.py --show-session``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

OFFLINE_TIMELINE_SERVER_NAME = "pypho_timeline_offline"
_COMPUTED_STATUSES = frozenset({"computed", "exported"})
_LAST_SPAWN_MONOTONIC: float = 0.0
_COLD_START_GRACE_SECONDS: float = 15.0

# Sibling repo next to PhoOfflineEEGAnalysis under ACTIVE_DEV
_DEFAULT_PYPHOTIMELINE_ROOT = Path(__file__).resolve().parents[4] / "pyPhoTimeline"


def _to_handoff_time(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _prefer_cached_from_status(status: Optional[str], prefer_cached_spectrograms: Optional[bool]) -> bool:
    """Default to preferring cached spectrograms/bundles regardless of catalog status."""
    if prefer_cached_spectrograms is not None:
        return bool(prefer_cached_spectrograms)
    return True


def resolve_pyphotimeline_root() -> Path:
    env = os.environ.get("PYPHOTIMELINE_ROOT")
    if env:
        return Path(env).resolve()
    return _DEFAULT_PYPHOTIMELINE_ROOT.resolve()


def resolve_offline_python(pyphotimeline_root: Optional[Path] = None) -> Path:
    root = pyphotimeline_root if pyphotimeline_root is not None else resolve_pyphotimeline_root()
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    venv_py_unix = root / ".venv" / "bin" / "python"
    if venv_py_unix.exists():
        return venv_py_unix
    return Path(sys.executable)


def _wait_for_socket_response(sock, timeout_ms: int) -> bool:
    """Wait for response bytes, pumping the Qt event loop so a same-process server can accept."""
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance()
    remaining = max(0, int(timeout_ms))
    slice_ms = 50
    while remaining >= 0:
        if sock.bytesAvailable() > 0:
            return True
        if sock.waitForReadyRead(min(slice_ms, max(remaining, 0))):
            return True
        if app is not None:
            app.processEvents()
        if sock.bytesAvailable() > 0:
            return True
        remaining -= slice_ms
        if remaining < 0:
            break
    ## END while remaining >= 0....
    return sock.bytesAvailable() > 0


def _build_clean_child_env(pyphotimeline_root: Optional[Path] = None, python_exe: Optional[Path] = None) -> Dict[str, str]:
    """Return a copy of ``os.environ`` scrubbed of parent PyQt5 / venv variables so PyQt6 child launches cleanly."""
    env = dict(os.environ)
    for key in ("QT_API", "PYQTGRAPH_QT_LIB", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    ## END for key in ("QT_API", ...)...

    parent_prefix = str(Path(sys.prefix).resolve()).lower()
    raw_path = env.get("PATH", "")
    kept_parts: List[str] = []
    for part in raw_path.split(os.pathsep):
        if not part:
            continue
        low = part.lower()
        try:
            norm_part = str(Path(part).resolve()).lower()
        except OSError:
            norm_part = low
        if "pyqt5" in low or (parent_prefix and norm_part.startswith(parent_prefix)):
            continue
        kept_parts.append(part)
    ## END for part in raw_path.split(os.pathsep)...

    if python_exe is not None:
        exe_dir = str(Path(python_exe).parent)
        if exe_dir and exe_dir not in kept_parts:
            kept_parts.insert(0, exe_dir)
    elif pyphotimeline_root is not None:
        for rel in (Path(".venv") / "Scripts", Path(".venv") / "bin"):
            cand = pyphotimeline_root / rel
            if cand.exists():
                kept_parts.insert(0, str(cand))
                break
        ## END for rel in (Path(".venv") / "Scripts", Path(".venv") / "bin")...

    env["PATH"] = os.pathsep.join(kept_parts)
    return env


def normalize_session_handoff_items(sessions: Sequence[Union[str, Path, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Validate and normalize session records/paths into IPC/CLI handoff dicts."""
    normalized: List[Dict[str, Any]] = []
    seen_resolved = set()
    for item in sessions:
        if isinstance(item, dict):
            raw_path = item.get("xdf_path") or item.get("path")
            if not raw_path:
                raise ValueError("Session record has no xdf_path")
            start = item.get("start", item.get("recording_datetime"))
            end = item.get("end")
            duration_sec = item.get("duration_sec")
            status = item.get("status")
            prefer_cached = item.get("prefer_cached_spectrograms")
        else:
            raw_path = item
            start = None
            end = None
            duration_sec = None
            status = None
            prefer_cached = None

        path = Path(str(raw_path))
        if not path.exists():
            raise FileNotFoundError(f"XDF does not exist: {path}")
        resolved = path.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)

        end_val = end
        if end_val is None and start is not None and duration_sec is not None:
            try:
                if isinstance(start, datetime):
                    end_val = start + timedelta(seconds=float(duration_sec))
                else:
                    end_val = float(start) + float(duration_sec)
            except Exception:
                end_val = None

        eff_prefer_cached = _prefer_cached_from_status(status, prefer_cached)
        normalized.append({
            "xdf_path": str(resolved),
            "start": _to_handoff_time(start),
            "end": _to_handoff_time(end_val),
            "prefer_cached_spectrograms": eff_prefer_cached,
            "status": status,
        })
    ## END for item in sessions...

    return normalized


def _send_local_socket_payload(payload: Dict[str, Any], *, server_name: str = OFFLINE_TIMELINE_SERVER_NAME, timeout_ms: int = 1500) -> Tuple[bool, Optional[bool]]:
    from qtpy.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(timeout_ms):
        return False, None
    sock.write((json.dumps(payload) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    if _wait_for_socket_response(sock, timeout_ms):
        try:
            raw = bytes(sock.readAll()).decode("utf-8", errors="replace").strip()
            if not raw:
                sock.disconnectFromServer()
                return True, True
            line = raw.splitlines()[0]
            resp = json.loads(line)
            sock.disconnectFromServer()
            if "ok" in resp:
                return True, bool(resp.get("ok"))
            return True, True
        except Exception:
            sock.disconnectFromServer()
            return True, True
    sock.disconnectFromServer()
    return True, True


def try_send_show_sessions(sessions: Sequence[Union[str, Path, Dict[str, Any]]], *, mode: str = "append", server_name: str = OFFLINE_TIMELINE_SERVER_NAME, timeout_ms: int = 1500) -> Tuple[bool, Optional[bool]]:
    """Send a batch ``show_sessions`` command to a running offline timeline."""
    serialized: List[Dict[str, Any]] = []
    for item in sessions:
        if isinstance(item, dict):
            raw_p = item.get("xdf_path") or item.get("path")
            if not raw_p:
                continue
            st = item.get("status")
            pc = _prefer_cached_from_status(st, item.get("prefer_cached_spectrograms"))
            serialized.append({
                "xdf_path": str(Path(str(raw_p))),
                "start": _to_handoff_time(item.get("start", item.get("recording_datetime"))),
                "end": _to_handoff_time(item.get("end")),
                "prefer_cached_spectrograms": pc,
                "status": st,
            })
        else:
            serialized.append({"xdf_path": str(Path(item)), "prefer_cached_spectrograms": True})
    ## END for item in sessions...

    if not serialized:
        return False, False
    payload = {"cmd": "show_sessions", "sessions": serialized, "mode": str(mode or "append").strip().lower()}
    return _send_local_socket_payload(payload, server_name=server_name, timeout_ms=timeout_ms)


def try_send_show_session(xdf_path: Union[str, Path], start: Any = None, end: Any = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None, timeout_ms: int = 1500, *, mode: str = "append", server_name: str = OFFLINE_TIMELINE_SERVER_NAME) -> Tuple[bool, Optional[bool]]:
    """Attempt show_session against a running offline timeline.

    Returns ``(connected, ok)``:
    - ``(False, None)`` if no server accepted the connection
    - ``(True, True/False)`` if connected; ``ok`` from the ack (True when response missing/unparseable)
    """
    prefer_cached = _prefer_cached_from_status(status, prefer_cached_spectrograms)
    payload = {
        "cmd": "show_session",
        "xdf_path": str(Path(xdf_path)),
        "start": _to_handoff_time(start),
        "end": _to_handoff_time(end),
        "prefer_cached_spectrograms": prefer_cached,
        "status": status,
        "mode": str(mode or "append").strip().lower(),
    }
    return _send_local_socket_payload(payload, server_name=server_name, timeout_ms=timeout_ms)


def spawn_offline_timeline_sessions(sessions: Sequence[Union[str, Path, Dict[str, Any]]], *, mode: str = "append") -> subprocess.Popen:
    """Cold-start ``pyPhoTimeline/main_offline_timeline.py`` with one or more ``--show-session`` paths."""
    global _LAST_SPAWN_MONOTONIC
    root = resolve_pyphotimeline_root()
    script = root / "main_offline_timeline.py"
    if not script.exists():
        raise FileNotFoundError(f"main_offline_timeline.py not found at {script}")
    python_exe = resolve_offline_python(root)

    items: List[Dict[str, Any]] = []
    for s in sessions:
        if isinstance(s, dict):
            raw_p = s.get("xdf_path") or s.get("path")
            if not raw_p:
                continue
            st = s.get("status")
            items.append({
                "xdf_path": str(Path(str(raw_p))),
                "start": _to_handoff_time(s.get("start", s.get("recording_datetime"))),
                "end": _to_handoff_time(s.get("end")),
                "prefer_cached_spectrograms": _prefer_cached_from_status(st, s.get("prefer_cached_spectrograms")),
                "status": st,
            })
        else:
            items.append({"xdf_path": str(Path(s)), "start": None, "end": None, "prefer_cached_spectrograms": True, "status": None})
    ## END for s in sessions...

    if not items:
        raise ValueError("No sessions supplied to spawn_offline_timeline_sessions")

    cmd: List[str] = [str(python_exe), str(script)]
    for item in items:
        cmd.extend(["--show-session", item["xdf_path"]])
    ## END for item in items...

    mode_norm = str(mode or "append").strip().lower()
    if mode_norm in ("append", "replace"):
        cmd.extend(["--mode", mode_norm])
    if len(items) == 1:
        if items[0].get("start") is not None:
            cmd.extend(["--start", str(items[0]["start"])])
        if items[0].get("end") is not None:
            cmd.extend(["--end", str(items[0]["end"])])
        if items[0].get("status") is not None:
            cmd.extend(["--session-status", str(items[0]["status"])])
    if any(bool(item.get("prefer_cached_spectrograms")) for item in items):
        cmd.append("--prefer-cached-spectrograms")

    clean_env = _build_clean_child_env(pyphotimeline_root=root, python_exe=python_exe)
    _LAST_SPAWN_MONOTONIC = time.monotonic()
    return subprocess.Popen(cmd, cwd=str(root), env=clean_env)


def spawn_offline_timeline(xdf_path: Union[str, Path], start: Any = None, end: Any = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None) -> subprocess.Popen:
    """Cold-start pyPhoTimeline offline with --show-session."""
    return spawn_offline_timeline_sessions([{"xdf_path": xdf_path, "start": start, "end": end, "prefer_cached_spectrograms": prefer_cached_spectrograms, "status": status}], mode="append")


def open_sessions_in_timeline(sessions: Sequence[Union[str, Path, Dict[str, Any]]], *, mode: str = "append", connect_attempts: int = 3, retry_delay_s: float = 0.15, server_name: str = OFFLINE_TIMELINE_SERVER_NAME) -> str:
    """Show one or more ``.xdf`` sessions in offline timeline (batch IPC merge/replace, or single cold-start).

    Returns a short status string: ``ipc``, ``spawned``, or ``ipc_error``.
    """
    items = normalize_session_handoff_items(sessions)
    if not items:
        raise ValueError("No valid XDF sessions provided")

    eff_attempts = max(1, int(connect_attempts))
    eff_delay = float(retry_delay_s)
    if _LAST_SPAWN_MONOTONIC > 0.0 and (time.monotonic() - _LAST_SPAWN_MONOTONIC) < _COLD_START_GRACE_SECONDS:
        # Another cold-start was just triggered; wait a bit longer for its early QLocalServer to bind.
        eff_attempts = max(eff_attempts, 12)
        eff_delay = max(eff_delay, 0.25)

    for attempt in range(eff_attempts):
        connected, ok = try_send_show_sessions(items, mode=mode, server_name=server_name)
        if connected:
            return "ipc" if ok else "ipc_error"
        if attempt + 1 < eff_attempts:
            time.sleep(eff_delay)
    ## END for attempt in range(eff_attempts)...

    spawn_offline_timeline_sessions(items, mode=mode)
    return "spawned"


def open_session_in_timeline(xdf_path: Union[str, Path], start: Any = None, end: Any = None, duration_sec: Optional[float] = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None, connect_attempts: int = 3, retry_delay_s: float = 0.15, *, mode: str = "append", server_name: str = OFFLINE_TIMELINE_SERVER_NAME) -> str:
    """Show *xdf_path* in offline timeline (IPC zoom/append, or cold-start).

    Returns a short status string: ``ipc``, ``spawned``, or ``ipc_error``.
    Spawns only when no server accepts the connection.
    """
    return open_sessions_in_timeline(
        [{
            "xdf_path": xdf_path,
            "start": start,
            "end": end,
            "duration_sec": duration_sec,
            "prefer_cached_spectrograms": prefer_cached_spectrograms,
            "status": status,
        }],
        mode=mode,
        connect_attempts=connect_attempts,
        retry_delay_s=retry_delay_s,
        server_name=server_name,
    )
