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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple, Union

OFFLINE_TIMELINE_SERVER_NAME = "pypho_timeline_offline"
_COMPUTED_STATUSES = frozenset({"computed", "exported"})

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
    if prefer_cached_spectrograms is not None:
        return bool(prefer_cached_spectrograms)
    if status is None:
        return False
    return str(status).strip().lower() in _COMPUTED_STATUSES


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


def try_send_show_session(xdf_path: Union[str, Path], start: Any = None, end: Any = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None, timeout_ms: int = 1500) -> Tuple[bool, Optional[bool]]:
    """Attempt show_session against a running offline timeline.

    Returns ``(connected, ok)``:
    - ``(False, None)`` if no server accepted the connection
    - ``(True, True/False)`` if connected; ``ok`` from the ack (True when response missing/unparseable)
    """
    from qtpy.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(OFFLINE_TIMELINE_SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False, None
    prefer_cached = _prefer_cached_from_status(status, prefer_cached_spectrograms)
    payload = {
        "cmd": "show_session",
        "xdf_path": str(Path(xdf_path)),
        "start": _to_handoff_time(start),
        "end": _to_handoff_time(end),
        "prefer_cached_spectrograms": prefer_cached,
        "status": status,
    }
    sock.write((json.dumps(payload) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    if _wait_for_socket_response(sock, timeout_ms):
        try:
            raw = bytes(sock.readAll()).decode("utf-8", errors="replace").strip()
            if not raw:
                sock.disconnectFromServer()
                return True, True  # delivered; empty body means peer accepted without payload
            line = raw.splitlines()[0]
            resp = json.loads(line)
            sock.disconnectFromServer()
            # Explicit rejection only; missing "ok" (or accepted:true ack) counts as delivered.
            if "ok" in resp:
                return True, bool(resp.get("ok"))
            return True, True
        except Exception:
            sock.disconnectFromServer()
            return True, True
    sock.disconnectFromServer()
    return True, True


def spawn_offline_timeline(xdf_path: Union[str, Path], start: Any = None, end: Any = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None) -> subprocess.Popen:
    """Cold-start pyPhoTimeline offline with --show-session."""
    root = resolve_pyphotimeline_root()
    script = root / "main_offline_timeline.py"
    if not script.exists():
        raise FileNotFoundError(f"main_offline_timeline.py not found at {script}")
    python_exe = resolve_offline_python(root)
    prefer_cached = _prefer_cached_from_status(status, prefer_cached_spectrograms)
    cmd = [str(python_exe), str(script), "--show-session", str(Path(xdf_path))]
    if start is not None:
        cmd.extend(["--start", str(_to_handoff_time(start))])
    if end is not None:
        cmd.extend(["--end", str(_to_handoff_time(end))])
    if prefer_cached:
        cmd.append("--prefer-cached-spectrograms")
    if status is not None:
        cmd.extend(["--session-status", str(status)])
    return subprocess.Popen(cmd, cwd=str(root))


def open_session_in_timeline(xdf_path: Union[str, Path], start: Any = None, end: Any = None, duration_sec: Optional[float] = None, prefer_cached_spectrograms: Optional[bool] = None, status: Optional[str] = None, connect_attempts: int = 3, retry_delay_s: float = 0.15) -> str:
    """Show *xdf_path* in offline timeline (IPC zoom/append, or cold-start).

    Returns a short status string: ``ipc``, ``spawned``, or ``ipc_error``.
    Spawns only when no server accepts the connection.
    """
    path = Path(xdf_path)
    if not path.exists():
        raise FileNotFoundError(f"XDF does not exist: {path}")

    end_val = end
    if end_val is None and start is not None and duration_sec is not None:
        try:
            if isinstance(start, datetime):
                from datetime import timedelta
                end_val = start + timedelta(seconds=float(duration_sec))
            else:
                end_val = float(start) + float(duration_sec)
        except Exception:
            end_val = None

    for attempt in range(max(1, int(connect_attempts))):
        connected, ok = try_send_show_session(path, start=start, end=end_val, prefer_cached_spectrograms=prefer_cached_spectrograms, status=status)
        if connected:
            return "ipc" if ok else "ipc_error"
        if attempt + 1 < connect_attempts:
            time.sleep(retry_delay_s)
    ## END for attempt in range(max(1, int(connect_attempts)))....

    spawn_offline_timeline(path, start=start, end=end_val, prefer_cached_spectrograms=prefer_cached_spectrograms, status=status)
    return "spawned"
