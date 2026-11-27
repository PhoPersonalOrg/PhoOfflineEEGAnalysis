"""Representative sample fixtures for standalone preview rendering and UI smoke tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths
from phoofflineeeganalysis.offline_data_manager.media_catalog import SourceSpec, save_source_specs
from phoofflineeeganalysis.offline_data_manager.service import OfflineDataManagerService

SAMPLE_ANCHOR_DAY = datetime(2026, 11, 12, 0, 0, 0, tzinfo=timezone.utc)


def sample_stats() -> Dict[str, Any]:
    """Return aggregate stats matching the Manager Home left-rail density."""
    return {
        "total_sessions": 142,
        "sessions_by_status": {
            "discovered": 24,
            "computing": 1,
            "computed": 88,
            "exported": 26,
            "failed": 3,
        },
        "total_media": 5434,
        "total_media_all": 5790,
        "duplicate_media": 356,
        "media_by_category": {
            "EyeRecordings": 412,
            "WebcamRecordings": 1820,
            "ScreenRecordings": 1580,
            "Screenshots": 1204,
            "AudioRecordings": 418,
        },
        "media_by_kind": {
            "video": 3812,
            "screenshot": 1204,
            "audio": 418,
        },
    }


def sample_day_records() -> List[Dict[str, Any]]:
    """Return normalized chronological day rows across EEG, video, screenshot, and audio modalities."""
    return [
        {
            "kind": "session",
            "category": "EEG",
            "status": "computed",
            "path": "D:/LabRecorder/sub-P001/recording_2026-11-12_140211.xdf",
            "xdf_path": "D:/LabRecorder/sub-P001/recording_2026-11-12_140211.xdf",
            "filename": "recording_2026-11-12_140211.xdf",
            "streams": ["EEG", "Motion", "CQ", "Markers"],
            "duration_sec": 2832.0,
            "recording_datetime": datetime(2026, 11, 12, 14, 2, 11, tzinfo=timezone.utc),
            "fif_path": "E:/Pickled/merged/recording_2026-11-12_140211_raw.fif",
            "mat_path": None,
            "source_root": "D:/LabRecorder/sub-P001",
            "error": None,
        },
        {
            "kind": "video",
            "category": "WebcamRecordings",
            "path": "D:/Captures/PhoBehaviorProject/WebcamRecordings/CAM_2026-11-12T140502.mkv",
            "video_path": "D:/Captures/PhoBehaviorProject/WebcamRecordings/CAM_2026-11-12T140502.mkv",
            "filename": "CAM_2026-11-12T140502.mkv",
            "duration_sec": 720.0,
            "recording_datetime": datetime(2026, 11, 12, 14, 5, 2, tzinfo=timezone.utc),
            "source_root": "D:/Captures/PhoBehaviorProject/WebcamRecordings",
            "consolidated_path": None,
        },
        {
            "kind": "screenshot",
            "category": "Screenshots",
            "path": "E:/Dropbox (Personal)/Screenshots/ShareX Screenshots/ShareX_2026-11-12_14-08-44.png",
            "video_path": "E:/Dropbox (Personal)/Screenshots/ShareX Screenshots/ShareX_2026-11-12_14-08-44.png",
            "filename": "ShareX_2026-11-12_14-08-44.png",
            "duration_sec": None,
            "recording_datetime": datetime(2026, 11, 12, 14, 8, 44, tzinfo=timezone.utc),
            "source_root": "E:/Dropbox (Personal)/Screenshots/ShareX Screenshots",
            "consolidated_path": None,
        },
        {
            "kind": "audio",
            "category": "AudioRecordings",
            "path": "D:/Captures/Just Press Record/2026-11-12/VoiceMemo_141001.m4a",
            "video_path": "D:/Captures/Just Press Record/2026-11-12/VoiceMemo_141001.m4a",
            "filename": "VoiceMemo_141001.m4a",
            "duration_sec": 204.0,
            "recording_datetime": datetime(2026, 11, 12, 14, 10, 1, tzinfo=timezone.utc),
            "source_root": "D:/Captures/Just Press Record",
            "consolidated_path": None,
        },
        {
            "kind": "video",
            "category": "EyeRecordings",
            "path": "H:/ScreenRecordings/EyeTrackerVR_Recordings/EyeVR_2026-11-12_161500.mp4",
            "video_path": "H:/ScreenRecordings/EyeTrackerVR_Recordings/EyeVR_2026-11-12_161500.mp4",
            "filename": "EyeVR_2026-11-12_161500.mp4",
            "duration_sec": 930.0,
            "recording_datetime": datetime(2026, 11, 12, 16, 15, 0, tzinfo=timezone.utc),
            "source_root": "H:/ScreenRecordings/EyeTrackerVR_Recordings",
            "consolidated_path": None,
        },
        {
            "kind": "session",
            "category": "EEG",
            "status": "discovered",
            "path": "D:/LabRecorder/sub-P001/recording_2026-11-12_183000.xdf",
            "xdf_path": "D:/LabRecorder/sub-P001/recording_2026-11-12_183000.xdf",
            "filename": "recording_2026-11-12_183000.xdf",
            "streams": ["EEG", "Motion", "CQ"],
            "duration_sec": 1540.0,
            "recording_datetime": datetime(2026, 11, 12, 18, 30, 0, tzinfo=timezone.utc),
            "fif_path": None,
            "mat_path": None,
            "source_root": "D:/LabRecorder/sub-P001",
            "error": None,
        },
    ]


def build_populated_sample_service(base_dir: Path) -> OfflineDataManagerService:
    """Create an isolated temporary ``OfflineDataManagerService`` populated with multimodal rows."""
    lab_online = base_dir / "LabRecorder_Primary"
    lab_online.mkdir(parents=True, exist_ok=True)
    lab_offline = base_dir / "UnmountedDrive_LabRecorder"

    webcam_dir = base_dir / "WebcamRecordings"
    webcam_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = base_dir / "ShareX_Screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = base_dir / "JustPressRecord"
    audio_dir.mkdir(parents=True, exist_ok=True)
    eye_missing = base_dir / "OfflineDrive_EyeTrackerVR"

    media_yaml = base_dir / "media_sources.yaml"
    specs = [
        SourceSpec(category="EyeRecordings", path=eye_missing, recursive=False),
        SourceSpec(category="WebcamRecordings", path=webcam_dir, recursive=False),
        SourceSpec(category="ScreenRecordings", path=base_dir / "ScreenRecordings", recursive=False, exclude=[webcam_dir]),
        SourceSpec(category="Screenshots", path=screenshots_dir, recursive=False),
        SourceSpec(category="AudioRecordings", path=audio_dir, recursive=True),
    ]
    save_source_specs(specs, dest_root=base_dir / "CONSOLIDATED", sources_yaml=media_yaml)

    paths = AppPaths(
        lab_recorder_dirs=[lab_online, lab_offline],
        pickled_data_path=base_dir / "pickled",
        session_catalog_path=base_dir / "sessions.sqlite",
        media_catalog_path=base_dir / "media.sqlite",
        media_sources_yaml=media_yaml,
        config_yaml_path=base_dir / "paths.yaml",
        local_yaml_path=base_dir / "paths.local.yaml",
    )
    svc = OfflineDataManagerService(paths=paths)

    sc = svc.session_catalog
    sc.upsert_session(
        xdf_path=lab_online / "recording_2026-11-12_140211.xdf",
        status="exported",
        source_root=lab_online,
        recording_datetime=datetime(2026, 11, 12, 14, 2, 11, tzinfo=timezone.utc),
        duration_sec=2832.0,
        streams=["EEG", "Motion", "CQ", "Markers"],
        fif_path=base_dir / "pickled" / "recording_2026-11-12_140211_raw.fif",
    )
    sc.upsert_session(
        xdf_path=lab_online / "recording_2026-11-12_183000.xdf",
        status="discovered",
        source_root=lab_online,
        recording_datetime=datetime(2026, 11, 12, 18, 30, 0, tzinfo=timezone.utc),
        duration_sec=1540.0,
        streams=["EEG", "Motion", "CQ"],
    )
    sc.upsert_session(
        xdf_path=lab_online / "recording_2026-11-10_093000.xdf",
        status="computed",
        source_root=lab_online,
        recording_datetime=datetime(2026, 11, 10, 9, 30, 0, tzinfo=timezone.utc),
        duration_sec=3600.0,
        streams=["EEG", "CQ"],
    )
    sc.upsert_session(
        xdf_path=lab_online / "recording_2026-11-08_211500.xdf",
        status="failed",
        source_root=lab_online,
        recording_datetime=datetime(2026, 11, 8, 21, 15, 0, tzinfo=timezone.utc),
        duration_sec=600.0,
        error="Truncated XDF footer",
    )

    mc = svc.media_catalog
    mc.upsert_media(
        media_path=webcam_dir / "CAM_2026-11-12T140502.mkv",
        category="WebcamRecordings",
        media_kind="video",
        source_root=webcam_dir,
        start_datetime=datetime(2026, 11, 12, 14, 5, 2, tzinfo=timezone.utc),
        duration_sec=720.0,
        file_size=4096,
    )
    mc.upsert_media(
        media_path=screenshots_dir / "ShareX_2026-11-12_14-08-44.png",
        category="Screenshots",
        media_kind="screenshot",
        source_root=screenshots_dir,
        start_datetime=datetime(2026, 11, 12, 14, 8, 44, tzinfo=timezone.utc),
        file_size=512,
    )
    mc.upsert_media(
        media_path=audio_dir / "VoiceMemo_2026-11-12_141001.m4a",
        category="AudioRecordings",
        media_kind="audio",
        source_root=audio_dir,
        start_datetime=datetime(2026, 11, 12, 14, 10, 1, tzinfo=timezone.utc),
        duration_sec=204.0,
        file_size=1024,
    )
    mc.upsert_media(
        media_path=eye_missing / "EyeVR_2026-11-10_100000.mp4",
        category="EyeRecordings",
        media_kind="video",
        source_root=eye_missing,
        start_datetime=datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc),
        duration_sec=1200.0,
        file_size=8192,
    )
    return svc


__all__ = [
    "SAMPLE_ANCHOR_DAY",
    "sample_stats",
    "sample_day_records",
    "build_populated_sample_service",
]
