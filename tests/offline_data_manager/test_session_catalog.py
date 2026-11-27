"""Unit tests for SessionCatalog streams_json, source_root, and sync_from_processed_batch chips."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from phoofflineeeganalysis.offline_data_manager.media_modalities import stream_names_to_chips
from phoofflineeeganalysis.offline_data_manager.session_catalog import SessionCatalog


def test_stream_names_to_chips_mapping() -> None:
    chips = stream_names_to_chips(["Epoc X", "Epoc X Motion", "Epoc X eQuality", "TextLogger"])
    assert chips == ["EEG", "Motion", "CQ", "Markers"]


def test_session_catalog_streams_roundtrip_and_batch_sync(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.sqlite"
    sc = SessionCatalog(db_path)
    lab_dir = tmp_path / "sub-P001"
    lab_dir.mkdir()
    xdf_a = lab_dir / "ses_a.xdf"

    sc.upsert_session(
        xdf_path=xdf_a,
        status="discovered",
        source_root=lab_dir,
        recording_datetime=datetime(2026, 11, 12, 14, 0, 0, tzinfo=timezone.utc),
        duration_sec=1200.0,
        streams=["EEG", "Motion", "CQ"],
    )

    cal_df = sc.load_calendar_dataframe()
    assert len(cal_df) == 1
    assert cal_df.iloc[0]["streams"] == ["EEG", "Motion", "CQ"]

    batch_df = pd.DataFrame(
        [
            {"xdf_dataset_idx": 0, "xdf_filename": "ses_a.xdf", "name": "Epoc X", "recording_datetime": "2026-11-12T14:00:00+00:00", "eeg_duration_sec": 1205.0, "proccessed_fif_filename": str(tmp_path / "ses_a_raw.fif")},
            {"xdf_dataset_idx": 0, "xdf_filename": "ses_a.xdf", "name": "Epoc X Motion"},
            {"xdf_dataset_idx": 0, "xdf_filename": "ses_a.xdf", "name": "Epoc X eQuality"},
            {"xdf_dataset_idx": 0, "xdf_filename": "ses_a.xdf", "name": "EventBoard"},
        ]
    )
    n_synced = sc.sync_from_processed_batch(batch_df, [], recordings_dir=lab_dir, status="computed")
    assert n_synced == 1

    updated_df = sc.load_calendar_dataframe()
    row = updated_df.iloc[0]
    assert row["status"] == "exported"
    assert row["streams"] == ["EEG", "Motion", "CQ", "Markers"]
    assert str(row["fif_path"]).endswith("ses_a_raw.fif")

    src_summary = sc.source_summary_by_root(candidate_roots=[lab_dir])
    assert len(src_summary) == 1
    only_entry = next(iter(src_summary.values()))
    assert only_entry["count"] == 1
