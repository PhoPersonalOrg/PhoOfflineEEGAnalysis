"""Unit tests for OfflineDataManagerService stats, day_records filtering, sources, and consolidation."""

from __future__ import annotations

from pathlib import Path

from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, build_populated_sample_service


def test_service_stats_day_records_and_modality_filters(tmp_path: Path) -> None:
    svc = build_populated_sample_service(tmp_path)
    st = svc.stats()
    assert st["total_sessions"] == 4
    assert st["sessions_by_status"]["exported"] == 1
    assert st["sessions_by_status"]["computed"] == 1
    assert st["sessions_by_status"]["discovered"] == 1
    assert st["sessions_by_status"]["failed"] == 1
    assert st["total_media"] == 4
    assert st["media_by_kind"]["video"] == 2
    assert st["media_by_kind"]["screenshot"] == 1
    assert st["media_by_kind"]["audio"] == 1

    all_day = svc.day_records(SAMPLE_ANCHOR_DAY)
    assert len(all_day) == 5
    kinds = [r["kind"] for r in all_day]
    assert kinds == ["session", "video", "screenshot", "audio", "session"]
    assert all_day[0]["streams"] == ["EEG", "Motion", "CQ", "Markers"]

    eeg_only = svc.day_records(SAMPLE_ANCHOR_DAY, modality_filters={"EEG"})
    assert len(eeg_only) == 2
    assert all(r["kind"] == "session" for r in eeg_only)

    shots_and_audio = svc.day_records(SAMPLE_ANCHOR_DAY, modality_filters={"Screenshots", "AudioRecordings"})
    assert [r["kind"] for r in shots_and_audio] == ["screenshot", "audio"]

    empty_day = svc.day_records(SAMPLE_ANCHOR_DAY, modality_filters=set())
    assert empty_day == []

    sources = svc.list_sources()
    assert len(sources) == 7
    online_flags = {Path(s["path"]).name: s["exists"] for s in sources}
    assert online_flags["LabRecorder_Primary"] is True
    assert online_flags["UnmountedDrive_LabRecorder"] is False
    assert online_flags["OfflineDrive_EyeTrackerVR"] is False


def test_service_consolidate_dry_run_and_copy(tmp_path: Path) -> None:
    svc = build_populated_sample_service(tmp_path)
    shot_path = tmp_path / "ShareX_Screenshots" / "ShareX_2026-11-12_14-08-44.png"
    shot_path.write_bytes(b"png-bytes")

    dest = tmp_path / "OUT_CONSOLIDATED"
    dry = svc.consolidate_media(dest_root=dest, dry_run=True)
    assert dry["planned"] == 1
    assert dry["copied"] == 0

    real = svc.consolidate_media(dest_root=dest, dry_run=False)
    assert real["copied"] == 1
    assert (dest / "Screenshots" / "ShareX_2026-11-12_14-08-44.png").is_file()


def test_service_compute_options_and_compute_session(tmp_path: Path, monkeypatch) -> None:
    svc = build_populated_sample_service(tmp_path)
    opts0 = svc.get_compute_options()
    assert opts0["use_computation_cache"] is True
    assert opts0["should_write_final_merged_eeg_fif"] is True

    updated = svc.update_compute_options(
        {
            "use_computation_cache": False,
            "use_mtime_in_cache_key": True,
            "should_load_preprocessed": True,
            "absolute_max_workers": 4,
        },
        persist=True,
    )
    assert updated["use_computation_cache"] is False
    assert updated["use_mtime_in_cache_key"] is True
    assert updated["should_load_preprocessed"] is True
    assert updated["absolute_max_workers"] == 4
    assert svc.paths.local_yaml_path.is_file()

    captured = {}

    def _fake_process_sessions(*, progress=None, cancel_event=None, **kwargs):
        captured.update(kwargs)
        return "computed_ok"

    monkeypatch.setattr(svc, "process_sessions", _fake_process_sessions)
    xdf_file = tmp_path / "LabRecorder_Primary" / "recording_2026-11-12_183000.xdf"
    xdf_file.parent.mkdir(parents=True, exist_ok=True)
    xdf_file.write_bytes(b"XDF:")

    rec = {"kind": "session", "xdf_path": str(xdf_file), "filename": xdf_file.name}
    out = svc.compute_session(rec)
    assert out == "computed_ok"
    assert captured["n_most_recent_sessions_to_preprocess"] is None
    assert captured["included_xdf_paths"] == [xdf_file]
    assert captured["included_xdf_file_names"] == [xdf_file.name]
