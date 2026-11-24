"""Unit tests for MondayTuesdayComparison (synthetic data, no real recordings)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from phoofflineeeganalysis.analysis.weekday_pair_comparison import MondayTuesdayComparison, find_newest_summary_csv


def _write_summary_csv(path: Path, rows: list) -> Path:
    pd.DataFrame.from_records(rows).to_csv(path, index=False)
    return path


def test_local_weekday_assignment_across_utc_midnight(tmp_path: Path):
    # 2026-11-17 00:15:37 UTC == 2026-11-16 16:15:37 PST (Monday local)
    csv_path = _write_summary_csv(tmp_path / "a_session_summaries.csv", [
        {"xdf_filename": "evening.xdf", "meas_date": "2026-11-17 00:15:37", "duration_s": 700.0, "alpha_rel": 0.3},
        {"xdf_filename": "daytime.xdf", "meas_date": "2026-11-17 18:00:00", "duration_s": 3000.0, "alpha_rel": 0.4},
    ])
    sessions_df = MondayTuesdayComparison.load_session_metrics([csv_path])
    by_name = sessions_df.set_index("xdf_filename")
    assert by_name.loc["evening.xdf", "local_date"] == "2026-11-16"
    assert by_name.loc["evening.xdf", "weekday_name"] == "Monday"
    assert by_name.loc["daytime.xdf", "local_date"] == "2026-11-17"
    assert by_name.loc["daytime.xdf", "weekday_name"] == "Tuesday"


def test_dedup_keeps_newest_export(tmp_path: Path):
    older = _write_summary_csv(tmp_path / "2026-11-20_session_summaries.csv", [
        {"xdf_filename": "s1.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.1},
    ])
    newer = _write_summary_csv(tmp_path / "2026-11-23_session_summaries.csv", [
        {"xdf_filename": "s1.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.9},
    ])
    import os
    os.utime(older, (1000000, 1000000))
    os.utime(newer, (2000000, 2000000))
    sessions_df = MondayTuesdayComparison.load_session_metrics([newer, older])
    assert len(sessions_df) == 1
    assert sessions_df.iloc[0]["alpha_rel"] == pytest.approx(0.9)


def test_duration_weighted_daily_aggregation(tmp_path: Path):
    csv_path = _write_summary_csv(tmp_path / "a_session_summaries.csv", [
        {"xdf_filename": "m1.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.2},
        {"xdf_filename": "m2.xdf", "meas_date": "2026-11-16 23:00:00", "duration_s": 3000.0, "alpha_rel": 0.4},
    ])
    sessions_df = MondayTuesdayComparison.load_session_metrics([csv_path])
    daily_df = MondayTuesdayComparison.aggregate_daily_metrics(sessions_df)
    assert len(daily_df) == 1
    row = daily_df.iloc[0]
    assert row["local_date"] == "2026-11-16"
    assert row["n_sessions"] == 2
    assert row["total_duration_s"] == pytest.approx(4000.0)
    assert row["alpha_rel"] == pytest.approx(0.35)


def test_pairing_with_missing_tuesday(tmp_path: Path):
    csv_path = _write_summary_csv(tmp_path / "a_session_summaries.csv", [
        {"xdf_filename": "mon1.xdf", "meas_date": "2026-11-09 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.2},
        {"xdf_filename": "tue1.xdf", "meas_date": "2026-11-10 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.3},
        {"xdf_filename": "mon2.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.5},
        # 2026-11-18 is Wednesday local → must NOT pair with Monday 2026-11-16's next day
        {"xdf_filename": "wed1.xdf", "meas_date": "2026-11-18 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.9},
    ])
    sessions_df = MondayTuesdayComparison.load_session_metrics([csv_path])
    daily_df = MondayTuesdayComparison.aggregate_daily_metrics(sessions_df)
    pairs_df, unpaired_df = MondayTuesdayComparison.build_monday_tuesday_pairs(daily_df)
    assert pairs_df["monday_date"].unique().tolist() == ["2026-11-09"]
    alpha_pair = pairs_df.loc[pairs_df["metric"] == "alpha_rel"].iloc[0]
    assert alpha_pair["tuesday_date"] == "2026-11-10"
    assert alpha_pair["difference"] == pytest.approx(0.1)
    assert unpaired_df["monday_date"].tolist() == ["2026-11-16"]


def test_paired_stats_guards():
    # single pair → p-values NaN, diffs still reported
    single_pair_df = pd.DataFrame([{"metric": "alpha_rel", "monday_date": "2026-11-09", "tuesday_date": "2026-11-10", "monday_value": 0.2, "tuesday_value": 0.3, "difference": 0.1}])
    stats_df = MondayTuesdayComparison.compute_paired_statistics(single_pair_df)
    row = stats_df.iloc[0]
    assert row["n_pairs"] == 1
    assert row["mean_difference"] == pytest.approx(0.1)
    assert np.isnan(row["t_p_value"])
    # all-zero differences → Wilcoxon guarded to NaN, no raise
    zero_pairs_df = pd.DataFrame([{"metric": "alpha_rel", "monday_date": f"2026-11-{d:02d}", "tuesday_date": f"2026-11-{d+1:02d}", "monday_value": 0.2, "tuesday_value": 0.2, "difference": 0.0} for d in (2, 9, 16)])
    stats_df = MondayTuesdayComparison.compute_paired_statistics(zero_pairs_df)
    row = stats_df.iloc[0]
    assert row["n_pairs"] == 3
    assert np.isnan(row["wilcoxon_p_value"])
    assert np.isnan(row["t_p_value"])
    # varying differences → both tests produce finite p-values
    varying_df = pd.DataFrame([{"metric": "alpha_rel", "monday_date": f"2026-11-{d:02d}", "tuesday_date": f"2026-11-{d+1:02d}", "monday_value": 0.2, "tuesday_value": 0.2 + delta, "difference": delta} for d, delta in ((2, 0.1), (9, 0.15), (16, 0.05))])
    stats_df = MondayTuesdayComparison.compute_paired_statistics(varying_df)
    row = stats_df.iloc[0]
    assert np.isfinite(row["t_p_value"])
    assert np.isfinite(row["wilcoxon_p_value"])


def test_find_newest_summary_csv(tmp_path: Path):
    import os
    older = _write_summary_csv(tmp_path / "2026-11-20_session_summaries.csv", [{"xdf_filename": "a.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1.0, "alpha_rel": 0.1}])
    newer = _write_summary_csv(tmp_path / "2026-11-23_session_summaries.csv", [{"xdf_filename": "a.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 1.0, "alpha_rel": 0.2}])
    os.utime(older, (1000000, 1000000))
    os.utime(newer, (2000000, 2000000))
    assert find_newest_summary_csv(tmp_path) == newer


def test_full_export_run(tmp_path: Path):
    csv_path = _write_summary_csv(tmp_path / "a_session_summaries.csv", [
        {"xdf_filename": "mon1.xdf", "meas_date": "2026-11-09 20:00:00", "duration_s": 1000.0, "alpha_rel": 0.2, "theta_rel": 0.3},
        {"xdf_filename": "tue1.xdf", "meas_date": "2026-11-10 20:00:00", "duration_s": 1200.0, "alpha_rel": 0.3, "theta_rel": 0.25},
        {"xdf_filename": "mon2.xdf", "meas_date": "2026-11-16 20:00:00", "duration_s": 900.0, "alpha_rel": 0.25, "theta_rel": 0.28},
        {"xdf_filename": "tue2.xdf", "meas_date": "2026-11-17 20:00:00", "duration_s": 1100.0, "alpha_rel": 0.32, "theta_rel": 0.22},
    ])
    output_dir = tmp_path / "out"
    result = MondayTuesdayComparison.run_comparison([csv_path], output_dir=output_dir)
    for key in ("daily_aggregates", "monday_tuesday_pairs", "paired_stats", "unpaired_mondays"):
        assert result["csv_paths"][key].exists()
    ## END for key in ("daily_aggregates", "monday_tuesday_pairs", "paired_stats", "unpaired_mondays")....

    assert len(result["png_paths"]) >= 2
    assert all(p.exists() for p in result["png_paths"])
    assert result["html_path"] is not None and result["html_path"].exists()
    assert result["pairs_df"]["monday_date"].nunique() == 2
