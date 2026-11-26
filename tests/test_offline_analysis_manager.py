"""Tests for AppPaths, VideoCatalog.load_calendar_dataframe, and OfflineAnalysisManagerWindow."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from phoofflineeeganalysis.analysis.app_paths import AppPaths
from phoofflineeeganalysis.analysis.session_catalog import SessionCatalog
from phoofflineeeganalysis.analysis.video_catalog import VideoCatalog


def test_app_paths_load_save_and_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_yaml = tmp_path / "offline_analysis_paths.yaml"
    local_yaml = tmp_path / "offline_analysis_paths.local.yaml"
    base_yaml.write_text(
        "lab_recorder_dirs: []\n"
        'pickled_data_path: ""\n'
        'session_catalog_path: ""\n'
        'video_catalog_path: "output/video_catalog.sqlite"\n'
        'video_sources_yaml: "config/video_sources.yaml"\n'
        "process_defaults:\n"
        "  n_most_recent_sessions_to_preprocess: 5\n",
        encoding="utf-8",
    )

    loaded = AppPaths.load(config_yaml=base_yaml, local_yaml=local_yaml, apply_env=False, repo_root=tmp_path)
    assert loaded.lab_recorder_dirs == []
    assert loaded.video_catalog_path == tmp_path / "output" / "video_catalog.sqlite"
    assert loaded.process_defaults["n_most_recent_sessions_to_preprocess"] == 5

    lab_dir = tmp_path / "sub-P001"
    lab_dir.mkdir()
    loaded.lab_recorder_dirs = [lab_dir]
    loaded.pickled_data_path = tmp_path / "pickled"
    saved_path = loaded.save()
    assert saved_path == local_yaml
    assert local_yaml.exists()

    reloaded = AppPaths.load(config_yaml=base_yaml, local_yaml=local_yaml, apply_env=False, repo_root=tmp_path)
    assert reloaded.lab_recorder_dirs == [lab_dir]
    assert reloaded.existing_lab_recorder_dirs() == [lab_dir]
    assert reloaded.resolved_session_catalog_path() == tmp_path / "pickled" / "session_catalog" / "sessions.sqlite"

    env_dir = tmp_path / "env_lab"
    env_dir.mkdir()
    monkeypatch.setenv("PHO_LAB_RECORDER_DIRS", str(env_dir))
    env_loaded = AppPaths.load(config_yaml=base_yaml, local_yaml=local_yaml, apply_env=True, repo_root=tmp_path)
    assert env_loaded.lab_recorder_dirs == [env_dir]


def test_video_catalog_load_calendar_dataframe(tmp_path: Path) -> None:
    db = tmp_path / "video_catalog.sqlite"
    vc = VideoCatalog(db)
    t0 = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
    vc.upsert_video(
        video_path=tmp_path / "cam_a.mkv",
        category="WebcamRecordings",
        source_root=tmp_path,
        start_datetime=t0,
        duration_sec=1800.0,
        file_size=1000,
        mtime=10.0,
    )
    vc.upsert_video(
        video_path=tmp_path / "cam_a.mp4",
        category="WebcamRecordings",
        source_root=tmp_path,
        start_datetime=t0,
        duration_sec=1800.0,
        file_size=900,
        mtime=9.0,
    )
    vc.recompute_duplicates()

    cal_df = vc.load_calendar_dataframe(include_duplicates=False)
    assert len(cal_df) == 1
    assert cal_df.iloc[0]["filename"] == "cam_a.mkv"
    assert cal_df.iloc[0]["category"] == "WebcamRecordings"
    assert cal_df.iloc[0]["recording_day_date"] == datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert 0.5 < cal_df.iloc[0]["first_timestamp_rel"] < 0.7
    assert cal_df.iloc[0]["duration_rel"] > 0.01

    cal_all = vc.load_calendar_dataframe(include_duplicates=True)
    assert len(cal_all) == 2


def test_manager_window_and_video_overlay(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QDate
    from PyQt5.QtWidgets import QApplication

    from phoofflineeeganalysis.analysis.UI.offline_analysis_manager import OfflineAnalysisManagerWindow

    app = QApplication.instance() or QApplication([])

    session_db = tmp_path / "sessions.sqlite"
    sc = SessionCatalog(session_db)
    sc.upsert_session(
        xdf_path=tmp_path / "ses01.xdf",
        status="computed",
        recording_datetime=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        duration_sec=600.0,
    )

    video_db = tmp_path / "videos.sqlite"
    vc = VideoCatalog(video_db)
    vc.upsert_video(
        video_path=tmp_path / "eye01.mkv",
        category="EyeRecordings",
        source_root=tmp_path,
        start_datetime=datetime(2026, 3, 15, 10, 5, 0, tzinfo=timezone.utc),
        duration_sec=300.0,
    )

    paths = AppPaths(
        lab_recorder_dirs=[tmp_path],
        session_catalog_path=session_db,
        video_catalog_path=video_db,
        video_sources_yaml=tmp_path / "video_sources.yaml",
        local_yaml_path=tmp_path / "offline_analysis_paths.local.yaml",
    )

    win = OfflineAnalysisManagerWindow(paths=paths)
    win.calendar_panel.calendar.setSelectedDate(QDate(2026, 3, 15))
    win.calendar_panel.refresh_selected_day()
    assert win.calendar_panel.session_list.count() == 2
    assert "1 session(s), 1 video(s)" in win.calendar_panel.day_label.text()

    win.action_toggle_video_overlay.setChecked(False)
    assert win.calendar_panel.session_list.count() == 1
    assert "1 session(s)" in win.calendar_panel.day_label.text()

    win.close()
    app.processEvents()
