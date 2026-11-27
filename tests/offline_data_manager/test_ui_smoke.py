"""Offscreen smoke tests for PhoOfflineDataManagerWindow and modal dialogs."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from phoofflineeeganalysis.offline_data_manager.ui.dialogs import (
    ComputationOptionsDialog,
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


def test_compute_button_and_context_menu_lmb_vs_alt_lmb(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    svc = build_populated_sample_service(tmp_path)

    # Create the backing .xdf file on disk so _start_compute_single_session finds it
    day_recs = svc.day_records(SAMPLE_ANCHOR_DAY)
    first_sess = next(r for r in day_recs if r["kind"] == "session")
    xdf_path = Path(str(first_sess["xdf_path"]))
    xdf_path.parent.mkdir(parents=True, exist_ok=True)
    xdf_path.write_bytes(b"XDF:")

    win = PhoOfflineDataManagerWindow(service=svc, auto_select_latest_day=False)
    win.show()
    win.calendar_panel.set_selected_day(SAMPLE_ANCHOR_DAY)
    win._on_day_selected(SAMPLE_ANCHOR_DAY)
    app.processEvents()

    panel = win.day_detail_panel
    assert not panel.btn_compute.isEnabled()

    # Selecting a media row (row 1 is webcam video) keeps Compute disabled
    panel.records_list.setCurrentRow(1)
    app.processEvents()
    assert not panel.btn_compute.isEnabled()
    media_menu = panel.build_context_menu_for_record(panel.selected_record())
    media_action_texts = [a.text() for a in media_menu.actions() if not a.isSeparator()]
    assert not any(t.startswith("Compute") for t in media_action_texts)

    # Selecting an .xdf session row (row 0) enables Compute
    panel.records_list.setCurrentRow(0)
    app.processEvents()
    assert panel.btn_compute.isEnabled()

    opened_options_targets = []

    def _fake_open_options(target_record=None):
        opened_options_targets.append(target_record)
        svc.update_compute_options({"use_computation_cache": False, "absolute_max_workers": 6}, persist=False)

    computed_calls = []

    def _fake_start_compute(record):
        computed_calls.append((record["filename"], dict(svc.get_compute_options())))

    monkeypatch.setattr(win, "on_open_compute_options", _fake_open_options)
    monkeypatch.setattr(win, "_start_compute_single_session", _fake_start_compute)

    # 1) Alt+LMB on bottom Compute button -> opens Computation Options panel without running immediately
    QTest.mouseClick(panel.btn_compute, Qt.LeftButton, Qt.AltModifier)
    app.processEvents()
    assert len(opened_options_targets) == 1
    assert opened_options_targets[0]["filename"] == first_sess["filename"]
    assert len(computed_calls) == 0

    # 2) Regular LMB on bottom Compute button -> starts compute immediately using updated current options
    QTest.mouseClick(panel.btn_compute, Qt.LeftButton, Qt.NoModifier)
    app.processEvents()
    assert len(computed_calls) == 1
    assert computed_calls[0][0] == first_sess["filename"]
    assert computed_calls[0][1]["use_computation_cache"] is False
    assert computed_calls[0][1]["absolute_max_workers"] == 6

    # 3) Context menu for .xdf session: pressing Alt keeps menu open; Alt+LMB opens options; LMB runs compute
    sess_rec = panel.selected_record()
    menu_alt = panel.build_context_menu_for_record(sess_rec)
    act_compute_alt = next(a for a in menu_alt.actions() if a.text().startswith("Compute"))
    menu_alt.popup(QPoint(20, 20))
    app.processEvents()
    QTest.keyClick(menu_alt, Qt.Key_Alt)
    assert menu_alt.isVisible()
    QTest.mouseClick(menu_alt, Qt.LeftButton, Qt.AltModifier, menu_alt.actionGeometry(act_compute_alt).center())
    app.processEvents()
    assert len(opened_options_targets) == 2

    menu_lmb = panel.build_context_menu_for_record(sess_rec)
    act_compute_lmb = next(a for a in menu_lmb.actions() if a.text().startswith("Compute"))
    menu_lmb.popup(QPoint(20, 20))
    app.processEvents()
    QTest.mouseClick(menu_lmb, Qt.LeftButton, Qt.NoModifier, menu_lmb.actionGeometry(act_compute_lmb).center())
    app.processEvents()
    assert len(computed_calls) == 2

    # 4) Verify ComputationOptionsDialog Save Options vs Compute button behavior
    opt_dlg = ComputationOptionsDialog(options=svc.get_compute_options(), target_record=sess_rec)
    opt_dlg.use_mtime_check.setChecked(True)
    opt_dlg.load_preproc_check.setChecked(True)
    opt_dlg._on_save_only_clicked()
    assert opt_dlg.result() == QDialog.Accepted
    assert opt_dlg.run_requested is False
    assert opt_dlg.get_options()["use_mtime_in_cache_key"] is True
    assert opt_dlg.get_options()["should_load_preprocessed"] is True

    opt_dlg2 = ComputationOptionsDialog(options=svc.get_compute_options(), target_record=sess_rec)
    opt_dlg2._on_compute_now_clicked()
    assert opt_dlg2.result() == QDialog.Accepted
    assert opt_dlg2.run_requested is True

    win.close()
    app.processEvents()
