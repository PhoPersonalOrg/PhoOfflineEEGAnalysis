"""Tests for multi-session & full-day XDF timeline handoff from PhoOfflineEEGAnalysis."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QItemSelectionModel, Qt
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QAbstractItemView, QApplication

from phoofflineeeganalysis.analysis import timeline_handoff
from phoofflineeeganalysis.offline_data_manager.ui.main_window import PhoOfflineDataManagerWindow
from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, build_populated_sample_service


def test_day_detail_multi_select_and_open_all_day(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    svc = build_populated_sample_service(tmp_path)
    win = PhoOfflineDataManagerWindow(service=svc, auto_select_latest_day=False)
    win.show()
    win.calendar_panel.set_selected_day(SAMPLE_ANCHOR_DAY)
    win._on_day_selected(SAMPLE_ANCHOR_DAY)
    app.processEvents()

    panel = win.day_detail_panel
    assert panel.records_list.selectionMode() == QAbstractItemView.ExtendedSelection

    # With 0 rows selected on a day with 2 EEG sessions, both 'Open All (2) in Timeline' and 'Open Day (2)' are enabled
    panel.records_list.clearSelection()
    panel.records_list.setCurrentItem(None)
    app.processEvents()
    assert panel.btn_open_timeline.isEnabled()
    assert "2" in panel.btn_open_timeline.text()
    assert panel.btn_open_day_timeline.isEnabled()

    captured_calls: list[tuple[list[str], str]] = []

    def _fake_open_sessions(records_or_paths, *, mode: str = "append", **_kw) -> str:
        names = [r.get("filename") if isinstance(r, dict) else Path(str(r)).name for r in records_or_paths]
        captured_calls.append((names, mode))
        return "ipc"

    monkeypatch.setattr(svc, "open_sessions_in_timeline", _fake_open_sessions)

    # 1) Clicking primary Open button with nothing selected opens ALL 2 EEG sessions of the day
    QTest.mouseClick(panel.btn_open_timeline, Qt.LeftButton, Qt.NoModifier)
    app.processEvents()
    assert len(captured_calls) == 1
    assert len(captured_calls[0][0]) == 2
    assert captured_calls[0][1] == "append"

    # 2) Multi-select both EEG session rows in records_list -> 'Open 2 in Timeline'
    eeg_rows = []
    for row_idx in range(panel.records_list.count()):
        it = panel.records_list.item(row_idx)
        rec = it.data(Qt.UserRole)
        if isinstance(rec, dict) and rec.get("kind") == "session":
            eeg_rows.append(row_idx)
    ## END for row_idx in range(panel.records_list.count())...

    assert len(eeg_rows) == 2
    panel.records_list.clearSelection()
    for row_idx in eeg_rows:
        panel.records_list.item(row_idx).setSelected(True)
    ## END for row_idx in eeg_rows...

    panel.records_list.setCurrentRow(eeg_rows[0], QItemSelectionModel.NoUpdate)
    app.processEvents()
    assert len(panel.selected_eeg_records()) == 2
    assert panel.btn_open_timeline.text() == "Open 2 in Timeline"

    # Shift+Click triggers 'replace' mode on multi-selection
    QTest.mouseClick(panel.btn_open_timeline, Qt.LeftButton, Qt.ShiftModifier)
    app.processEvents()
    assert len(captured_calls) == 2
    assert len(captured_calls[1][0]) == 2
    assert captured_calls[1][1] == "replace"

    # 3) Context menu on multi-selection offers multi-selected Open & Replace actions
    ctx_menu = panel.build_context_menu_for_record(panel.selected_eeg_records()[0])
    texts = [a.text() for a in ctx_menu.actions() if not a.isSeparator()]
    assert "Open 2 Selected in Timeline" in texts
    assert "Replace Timeline with 2 Selected" in texts

    rep_action = next(a for a in ctx_menu.actions() if a.text() == "Replace Timeline with 2 Selected")
    rep_action.trigger()
    app.processEvents()
    assert len(captured_calls) == 3
    assert captured_calls[2][1] == "replace"

    win.close()
    app.processEvents()


def test_open_sessions_in_timeline_batch_ipc_and_clean_spawn_env(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    xdf_a = tmp_path / "sess_a.xdf"
    xdf_b = tmp_path / "sess_b.xdf"
    xdf_a.write_bytes(b"XDF:")
    xdf_b.write_bytes(b"XDF:")

    # 1) Verify batch IPC payload over real QLocalServer
    server_name = f"pho_handoff_test_{os.getpid()}_{int(time.time() * 1000)}"
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    received_payloads: list[dict] = []

    def _on_conn() -> None:
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            if sock is None:
                continue
            if sock.bytesAvailable() > 0 or sock.waitForReadyRead(200):
                raw = bytes(sock.readAll()).decode("utf-8").strip()
                if raw:
                    received_payloads.append(json.loads(raw.splitlines()[0]))
            sock.write(b'{"ok":true,"accepted":true}\n')
            sock.flush()
            sock.waitForBytesWritten(200)
        ## END while server.hasPendingConnections()...

    server.newConnection.connect(_on_conn)
    assert server.listen(server_name)
    try:
        records = [
            {"xdf_path": str(xdf_a), "recording_datetime": datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc), "duration_sec": None, "status": "discovered"},
            {"xdf_path": str(xdf_b), "recording_datetime": datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc), "duration_sec": 600.0, "status": "computed"},
        ]
        res = timeline_handoff.open_sessions_in_timeline(records, mode="replace", server_name=server_name, connect_attempts=1)
        app.processEvents()
        assert res == "ipc"
        assert len(received_payloads) == 1
        msg = received_payloads[0]
        assert msg["cmd"] == "show_sessions"
        assert msg["mode"] == "replace"
        assert len(msg["sessions"]) == 2
        # Discovered session with duration_sec=None must keep end=None (no fake +3600s)
        assert msg["sessions"][0]["end"] is None
        assert msg["sessions"][0]["prefer_cached_spectrograms"] is False
        assert msg["sessions"][1]["end"] is not None
        assert msg["sessions"][1]["prefer_cached_spectrograms"] is True
    finally:
        server.close()
        QLocalServer.removeServer(server_name)

    # 2) Verify cold-start spawn passes multiple --show-session args in one Popen and strips PyQt5 env vars
    fake_timeline_root = tmp_path / "fake_pyPhoTimeline"
    fake_timeline_root.mkdir()
    (fake_timeline_root / "main_offline_timeline.py").write_text("# stub\n", encoding="utf-8")

    popen_calls: list[tuple[list[str], str, dict]] = []

    def _fake_popen(cmd, cwd=None, env=None, **_kw):
        popen_calls.append((list(cmd), str(cwd), dict(env or {})))
        return MagicMock()

    monkeypatch.setenv("PYPHOTIMELINE_ROOT", str(fake_timeline_root))
    monkeypatch.setenv("QT_API", "pyqt5")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "parent_venv"))
    monkeypatch.setattr(timeline_handoff, "_LAST_SPAWN_MONOTONIC", 0.0)
    monkeypatch.setattr("subprocess.Popen", _fake_popen)

    dead_server = f"pho_dead_server_{os.getpid()}_{int(time.time() * 1000)}"
    res_spawn = timeline_handoff.open_sessions_in_timeline(
        [{"xdf_path": str(xdf_a), "status": "discovered"}, {"xdf_path": str(xdf_b), "status": "exported"}],
        mode="append",
        server_name=dead_server,
        connect_attempts=1,
    )
    assert res_spawn == "spawned"
    assert len(popen_calls) == 1
    cmd_args, cwd_arg, child_env = popen_calls[0]
    assert cmd_args.count("--show-session") == 2
    assert str(xdf_a.resolve()) in cmd_args
    assert str(xdf_b.resolve()) in cmd_args
    assert "--prefer-cached-spectrograms" in cmd_args
    assert "QT_API" not in child_env
    assert "VIRTUAL_ENV" not in child_env
