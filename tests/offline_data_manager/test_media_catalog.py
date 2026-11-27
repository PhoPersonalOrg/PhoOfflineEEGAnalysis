"""Unit tests for MediaCatalog screenshot/audio ingest, migration, dedupe, and calendar filters."""

from __future__ import annotations

import os
import sqlite3
import wave
from datetime import datetime, timezone
from pathlib import Path

from phoofflineeeganalysis.offline_data_manager.media_catalog import (
    MediaCatalog,
    SourceSpec,
    VideoCatalog,
    load_source_specs,
    save_source_specs,
)


def _write_test_wav(path: Path, duration_sec: float = 2.5, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def test_media_catalog_alias_and_legacy_sqlite_migration(tmp_path: Path) -> None:
    assert MediaCatalog is VideoCatalog
    db_path = tmp_path / "legacy_videos.sqlite"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE videos (
                video_path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                stem TEXT NOT NULL,
                extension TEXT NOT NULL,
                category TEXT NOT NULL,
                source_root TEXT NOT NULL,
                start_datetime TEXT,
                end_datetime TEXT,
                duration_sec REAL,
                fps REAL,
                width INTEGER,
                height INTEGER,
                file_size INTEGER,
                mtime REAL,
                format_hint TEXT,
                duplicate_group_id TEXT,
                is_canonical INTEGER NOT NULL DEFAULT 1,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                duplicate_reason TEXT,
                consolidated_path TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO videos (video_path, filename, stem, extension, category, source_root, updated_at)
            VALUES (?, 'shot.png', 'shot', '.png', 'Screenshots', ?, '2026-11-12T00:00:00+00:00')
            """,
            (str(tmp_path / "shot.png"), str(tmp_path)),
        )
        conn.commit()

    mc = MediaCatalog(db_path)
    df = mc.load_media_df()
    assert len(df) == 1
    assert df.iloc[0]["media_kind"] == "screenshot"


def test_media_catalog_screenshot_and_audio_discover_exclude_and_filters(tmp_path: Path) -> None:
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    shot_file = shots_dir / "ShareX_2026-11-12_14-08-44.png"
    shot_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    dup_sub = shots_dir / "_DUP"
    dup_sub.mkdir()
    (dup_sub / "ShareX_2026-11-12_14-08-44.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    audio_root_a = tmp_path / "audio_a"
    audio_root_b = tmp_path / "audio_b"
    excluded_sub = audio_root_a / "ignored_sub"
    _write_test_wav(audio_root_a / "nested" / "VoiceMemo_2026-11-12_141001.wav", duration_sec=3.0)
    _write_test_wav(excluded_sub / "ShouldSkip_2026-11-12_150000.wav", duration_sec=1.0)

    m4a_a = audio_root_a / "nested" / "Clip_2026-11-12_160000.m4a"
    m4a_b = audio_root_b / "Clip_2026-11-12_160000.m4a"
    audio_root_b.mkdir(parents=True, exist_ok=True)
    m4a_a.write_bytes(b"fake-m4a-payload-identical")
    m4a_b.write_bytes(b"fake-m4a-payload-identical")

    past = datetime(2026, 11, 12, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    for p in (shot_file, audio_root_a / "nested" / "VoiceMemo_2026-11-12_141001.wav", m4a_a, m4a_b):
        os.utime(p, (past, past))
    ## END for p in (shot_file, audio_root_a / "nested" / "VoiceMemo_2026-11-12_141001.wav", m4a_a, m4a_b)...

    yaml_path = tmp_path / "media_sources.yaml"
    specs = [
        SourceSpec(category="Screenshots", path=shots_dir, recursive=True),
        SourceSpec(category="AudioRecordings", path=audio_root_a, recursive=True, exclude=[excluded_sub]),
        SourceSpec(category="AudioRecordings", path=audio_root_b, recursive=False),
    ]
    save_source_specs(specs, dest_root=tmp_path / "DEST", sources_yaml=yaml_path)
    loaded_specs, loaded_dest = load_source_specs(yaml_path)
    assert len(loaded_specs) == 3
    assert loaded_dest == tmp_path / "DEST"

    mc = MediaCatalog(tmp_path / "media.sqlite")
    counts = mc.discover_and_upsert(yaml_path, min_age_s=0.0)
    assert counts["upserted_screenshot"] == 1
    assert counts["upserted_audio"] == 3

    dedupe = mc.recompute_duplicates()
    assert dedupe["groups"] == 1
    assert dedupe["duplicate_rows"] == 1

    cal_all = mc.load_calendar_dataframe(include_duplicates=False)
    assert len(cal_all) == 3

    cal_shots = mc.load_calendar_dataframe(categories=["Screenshots"])
    assert len(cal_shots) == 1
    assert cal_shots.iloc[0]["media_kind"] == "screenshot"
    assert cal_shots.iloc[0]["recording_day_date"] == datetime(2026, 11, 12, 0, 0, 0, tzinfo=timezone.utc)

    cal_audio = mc.load_calendar_dataframe(media_kinds=["audio"])
    assert len(cal_audio) == 2
    wav_rows = cal_audio[cal_audio["filename"].str.endswith(".wav")]
    assert len(wav_rows) == 1
    assert abs(float(wav_rows.iloc[0]["duration_sec"]) - 3.0) < 0.05
