"""Unit tests for source_registry multi-folder CRUD, health checks, and YAML persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths
from phoofflineeeganalysis.offline_data_manager.media_catalog import MediaCatalog, load_source_specs
from phoofflineeeganalysis.offline_data_manager.session_catalog import SessionCatalog
from phoofflineeeganalysis.offline_data_manager.source_registry import (
    add_lab_dir,
    add_media_source,
    list_sources,
    remove_lab_dir,
    remove_media_source,
    update_media_source,
)


def test_source_registry_crud_and_health(tmp_path: Path) -> None:
    lab_ok = tmp_path / "lab_ok"
    lab_ok.mkdir()
    lab_missing = tmp_path / "lab_missing"

    cam_ok = tmp_path / "webcam_ok"
    cam_ok.mkdir()
    audio_missing = tmp_path / "audio_offline"

    media_yaml = tmp_path / "media_sources.yaml"
    add_media_source(media_yaml, "WebcamRecordings", cam_ok, recursive=False)
    add_media_source(media_yaml, "AudioRecordings", audio_missing, recursive=True, exclude=[audio_missing / "_DUP"])

    paths = AppPaths(
        lab_recorder_dirs=[lab_ok],
        session_catalog_path=tmp_path / "sessions.sqlite",
        media_catalog_path=tmp_path / "media.sqlite",
        media_sources_yaml=media_yaml,
        local_yaml_path=tmp_path / "paths.local.yaml",
    )
    add_lab_dir(paths, lab_missing)
    assert len(paths.lab_recorder_dirs) == 2
    assert paths.local_yaml_path.exists()

    sc = SessionCatalog(paths.resolved_session_catalog_path())
    sc.upsert_session(
        xdf_path=lab_ok / "ses_01.xdf",
        source_root=lab_ok,
        recording_datetime=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
    )
    mc = MediaCatalog(paths.media_catalog_path)
    mc.upsert_media(
        media_path=cam_ok / "cam_01.mkv",
        category="WebcamRecordings",
        source_root=cam_ok,
        start_datetime=datetime(2026, 11, 12, 10, 5, 0, tzinfo=timezone.utc),
    )

    entries = list_sources(paths, session_catalog=sc, media_catalog=mc)
    by_kind = {(e.kind, e.path.name): e for e in entries}
    assert by_kind[("lab_recorder", "lab_ok")].exists is True
    assert by_kind[("lab_recorder", "lab_ok")].file_count_estimate == 1
    assert by_kind[("lab_recorder", "lab_missing")].exists is False
    assert by_kind[("WebcamRecordings", "webcam_ok")].exists is True
    assert by_kind[("WebcamRecordings", "webcam_ok")].file_count_estimate == 1
    assert by_kind[("AudioRecordings", "audio_offline")].exists is False
    assert by_kind[("AudioRecordings", "audio_offline")].recursive is True

    update_media_source(media_yaml, "WebcamRecordings", cam_ok, recursive=True)
    specs, _ = load_source_specs(media_yaml)
    cam_spec = next(s for s in specs if s.category == "WebcamRecordings")
    assert cam_spec.recursive is True

    remove_media_source(media_yaml, "AudioRecordings", audio_missing)
    specs_after, _ = load_source_specs(media_yaml)
    assert len(specs_after) == 1

    remove_lab_dir(paths, lab_missing)
    assert len(paths.lab_recorder_dirs) == 1
