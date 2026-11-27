"""Offscreen smoke tests for PhoOfflineDataManagerWindow and modal dialogs."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from phoofflineeeganalysis.offline_data_manager.ui.dialogs import (
    ConsolidateMediaDialog,
    ProcessSessionsDialog,
    SourcesPathsDialog,
)
from phoofflineeeganalysis.offline_data_manager.ui.main_window import PhoOfflineDataManagerWindow
from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, build_populated_sample_service


def test_main_window_filters_tabs_and_cancelable_job(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    svc = build_populated_sample_service(tmp_path)
    win = PhoOfflineDataManagerWindow(service=svc, auto_select_latest_day=False)
    win.resize(1440, 900)
    win.calendar_panel.set_selected_day(SAMPLE_ANCHOR_DAY)
    win._on_day_selected(SAMPLE_ANCHOR_DAY)
    app.processEvents()

    assert win.day_detail_panel.records_list.count() == 5
    assert "2 session(s), 3 media" in win.day_detail_panel.day_header_label.text()

    win.day_detail_panel._on_tab_selected("screenshot")
    assert win.day_detail_panel.records_list.count() == 1
    win.day_detail_panel._on_tab_selected("all")
    assert win.day_detail_panel.records_list.count() == 5

    win.toolbar.action_toggle_media_overlay.setChecked(False)
    app.processEvents()
    assert win.day_detail_panel.records_list.count() == 2

    win.toolbar.action_toggle_media_overlay.setChecked(True)
    win.left_rail.set_active_modality_filters({"AudioRecordings"})
    app.processEvents()
    assert win.day_detail_panel.records_list.count() == 1

    grab_out = tmp_path / "smoke_grab.png"
    assert win.grab().save(str(grab_out))
    assert grab_out.is_file() and grab_out.stat().st_size > 0

    started = threading.Event()

    def _work(progress) -> str:
        progress("step", current=1, total=3, active_sessions=["ses.xdf"])
        started.set()
        while win._cancel_event is not None and not win._cancel_event.is_set():
            time.sleep(0.02)
        return "done"

    win._run_job("Smoke Job", _work, lambda _r: None, cancelable=True)
    deadline = time.time() + 3.0
    while not started.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert started.is_set()
    win.on_cancel_job()

    deadline = time.time() + 4.0
    while win._active_thread is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert win._last_job_cancelled is True

    win.close()
    app.processEvents()


def test_dialogs_roundtrip_params(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    svc = build_populated_sample_service(tmp_path)

    src_dlg = SourcesPathsDialog(service=svc)
    res = src_dlg.build_result()
    assert len(res["paths"].lab_recorder_dirs) == 2
    assert len(res["media_specs"]) == 5
    src_dlg.close()

    proc_dlg = ProcessSessionsDialog(
        defaults={"n_most_recent_sessions_to_preprocess": 4, "should_write_final_merged_eeg_fif": True, "use_computation_cache": False, "absolute_max_workers": 3},
        available_filenames=["ses_a.xdf", "ses_b.xdf"],
    )
    proc_dlg.filter_edit.setText("*_b.xdf")
    params = proc_dlg.get_params()
    assert params["n_most_recent_sessions_to_preprocess"] == 4
    assert params["included_xdf_file_names"] == ["ses_b.xdf"]
    assert params["absolute_max_workers"] == 3
    proc_dlg.close()

    cons_dlg = ConsolidateMediaDialog(default_dest=tmp_path / "DEST", canonical_count=12)
    cparams = cons_dlg.get_params()
    assert cparams["dest_root"] == tmp_path / "DEST"
    assert cparams["dry_run"] is True
    cons_dlg.close()
    app.processEvents()
