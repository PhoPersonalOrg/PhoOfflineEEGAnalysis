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
