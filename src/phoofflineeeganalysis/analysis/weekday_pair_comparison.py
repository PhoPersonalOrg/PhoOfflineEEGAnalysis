"""Paired comparison of Monday vs next-day Tuesday EEG session summary metrics.

Loads per-session summary CSVs (from SpectogramPlottingHelper.compute_session_summary_metrics),
assigns local-time weekdays, aggregates metrics per day (duration-weighted), pairs each Monday
with the following calendar-day Tuesday, computes paired statistics, and exports CSV/PNG/HTML.

Usage:
    from phoofflineeeganalysis.analysis.weekday_pair_comparison import MondayTuesdayComparison

    result = MondayTuesdayComparison.run_comparison(summary_csv_paths=[csv_path], output_dir=out_dir)
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_SUMMARIES_DIR = Path("C:/AITEMP/PhoOfflineEEGAnalysisOutputs/session_summaries")
DEFAULT_OUTPUTS_DIR = Path("C:/AITEMP/PhoOfflineEEGAnalysisOutputs/monday_tuesday_comparison")

# Columns that identify/describe a session rather than being comparable EEG measures.
NON_METRIC_COLUMNS = {"session_idx", "xdf_filename", "meas_date", "meas_date_utc", "meas_date_local", "local_date", "weekday", "weekday_name", "duration_s", "sfreq_hz", "n_channels", "_source_csv", "_source_mtime", "_dedup_key"}
# Daily bookkeeping columns produced by aggregation; not EEG measures.
DAILY_NON_METRIC_COLUMNS = {"local_date", "weekday", "weekday_name", "n_sessions", "total_duration_s"}


def find_newest_summary_csv(summaries_dir: Union[str, Path] = DEFAULT_SUMMARIES_DIR) -> Path:
    """Return the most recently modified *session_summaries.csv under summaries_dir."""
    summaries_dir = Path(summaries_dir)
    candidates = sorted(summaries_dir.glob("*session_summaries.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = [p for p in candidates if not p.name.endswith("_empty.csv")]
    if not candidates:
        raise FileNotFoundError(f"No *session_summaries.csv found under '{summaries_dir.as_posix()}'")
    return candidates[0]


def _metric_columns(sessions_df: pd.DataFrame) -> List[str]:
    """Numeric metric columns eligible for comparison (excludes identifiers and session descriptors)."""
    return [c for c in sessions_df.columns if c not in NON_METRIC_COLUMNS and pd.api.types.is_numeric_dtype(sessions_df[c])]


def _weighted_nanmean(values: np.ndarray, weights: np.ndarray) -> float:
    """Duration-weighted mean skipping NaN values; equal weights when all weights are non-positive/non-finite."""
    values = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    valid = np.isfinite(values)
    if not np.any(valid):
        return float("nan")
    values = values[valid]
    weights = weights[valid]
    if not np.all(np.isfinite(weights)) or np.all(weights <= 0):
        return float(np.mean(values))
    weights = np.clip(weights, 0.0, None)
    if weights.sum() <= 0:
        return float(np.mean(values))
    return float(np.sum(values * weights) / np.sum(weights))


class MondayTuesdayComparison:
    """Weekday-aware Monday→Tuesday paired analysis of session summary metrics."""

    @classmethod
    def load_session_metrics(cls, summary_csv_paths: Sequence[Union[str, Path]], timezone_name: str = DEFAULT_TIMEZONE) -> pd.DataFrame:
        """Load summary CSVs, parse meas_date as UTC, convert to local time, dedup by xdf_filename (newest export wins)."""
        local_tz = ZoneInfo(timezone_name)
        frames: List[pd.DataFrame] = []
        for csv_path in summary_csv_paths:
            csv_path = Path(csv_path)
            df = pd.read_csv(csv_path)
            df["_source_csv"] = str(csv_path)
            df["_source_mtime"] = csv_path.stat().st_mtime
            frames.append(df)
        ## END for csv_path in summary_csv_paths....

        if not frames:
            raise ValueError("No summary CSV paths provided")
        sessions_df = pd.concat(frames, ignore_index=True)
        if "meas_date" not in sessions_df.columns:
            raise ValueError("Summary CSV is missing required 'meas_date' column")

        meas_utc = pd.to_datetime(sessions_df["meas_date"], errors="coerce", utc=True)
        sessions_df = sessions_df.loc[meas_utc.notna()].copy()
        meas_utc = meas_utc.loc[meas_utc.notna()]
        sessions_df["meas_date_utc"] = meas_utc
        meas_local = meas_utc.dt.tz_convert(local_tz)
        sessions_df["meas_date_local"] = meas_local.dt.strftime("%Y-%m-%d %H:%M:%S")
        sessions_df["local_date"] = meas_local.dt.strftime("%Y-%m-%d")
        sessions_df["weekday"] = meas_local.dt.weekday  # Monday=0 ... Sunday=6
        sessions_df["weekday_name"] = meas_local.dt.day_name()

        # Dedup: prefer xdf_filename key; fall back to UTC meas_date when filename missing.
        filename_key = sessions_df.get("xdf_filename")
        if filename_key is None:
            filename_key = pd.Series([""] * len(sessions_df), index=sessions_df.index)
        filename_key = filename_key.fillna("").astype(str)
        fallback_key = "meas:" + sessions_df["meas_date_utc"].dt.strftime("%Y-%m-%dT%H:%M:%S")
        sessions_df["_dedup_key"] = filename_key.where(filename_key.str.len() > 0, fallback_key)
        sessions_df = sessions_df.sort_values(["_source_mtime", "_source_csv"], kind="stable")
        sessions_df = sessions_df.drop_duplicates(subset="_dedup_key", keep="last")
        sessions_df = sessions_df.sort_values("meas_date_utc", kind="stable").reset_index(drop=True)
        return sessions_df

    @classmethod
    def aggregate_daily_metrics(cls, sessions_df: pd.DataFrame) -> pd.DataFrame:
        """Duration-weighted mean of each numeric metric per local recording day; adds n_sessions, total_duration_s."""
        metric_cols = _metric_columns(sessions_df)
        rows: List[Dict[str, Any]] = []
        for local_date, day_df in sessions_df.groupby("local_date", sort=True):
            durations = pd.to_numeric(day_df.get("duration_s"), errors="coerce").to_numpy(dtype=np.float64) if "duration_s" in day_df.columns else np.full(len(day_df), np.nan)
            row: Dict[str, Any] = {
                "local_date": local_date,
                "weekday": int(day_df["weekday"].iloc[0]),
                "weekday_name": str(day_df["weekday_name"].iloc[0]),
                "n_sessions": int(len(day_df)),
                "total_duration_s": float(np.nansum(durations)) if np.any(np.isfinite(durations)) else float("nan"),
            }
            for metric in metric_cols:
                row[metric] = _weighted_nanmean(day_df[metric].to_numpy(dtype=np.float64), durations)
            ## END for metric in metric_cols....

            rows.append(row)
        ## END for local_date, day_df in sessions_df.groupby("local_date", sort=True)....

        return pd.DataFrame.from_records(rows)

    @classmethod
    def build_monday_tuesday_pairs(cls, daily_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Pair each Monday with Tuesday at Monday+1 day. Returns (pairs_long_df, unpaired_mondays_df)."""
        metric_cols = [c for c in daily_df.columns if c not in DAILY_NON_METRIC_COLUMNS and pd.api.types.is_numeric_dtype(daily_df[c])]
        daily_by_date = {row["local_date"]: row for _, row in daily_df.iterrows()}
        pair_rows: List[Dict[str, Any]] = []
        unpaired_rows: List[Dict[str, Any]] = []
        monday_df = daily_df.loc[daily_df["weekday"] == 0].sort_values("local_date")
        for _, monday_row in monday_df.iterrows():
            monday_date = pd.Timestamp(monday_row["local_date"])
            tuesday_date_str = (monday_date + timedelta(days=1)).strftime("%Y-%m-%d")
            tuesday_row = daily_by_date.get(tuesday_date_str)
            if tuesday_row is None or int(tuesday_row["weekday"]) != 1:
                unpaired_rows.append({"monday_date": monday_row["local_date"], "expected_tuesday_date": tuesday_date_str, "reason": "no Tuesday sessions on following day", "monday_n_sessions": monday_row["n_sessions"], "monday_total_duration_s": monday_row["total_duration_s"]})
                continue
            for metric in metric_cols:
                monday_value = float(monday_row[metric])
                tuesday_value = float(tuesday_row[metric])
                pair_rows.append({
                    "monday_date": monday_row["local_date"],
                    "tuesday_date": tuesday_date_str,
                    "metric": metric,
                    "monday_value": monday_value,
                    "tuesday_value": tuesday_value,
                    "difference": tuesday_value - monday_value,
                    "monday_n_sessions": int(monday_row["n_sessions"]),
                    "tuesday_n_sessions": int(tuesday_row["n_sessions"]),
                })
            ## END for metric in metric_cols....

        ## END for _, monday_row in monday_df.iterrows()....

        pairs_df = pd.DataFrame.from_records(pair_rows, columns=["monday_date", "tuesday_date", "metric", "monday_value", "tuesday_value", "difference", "monday_n_sessions", "tuesday_n_sessions"])
        unpaired_df = pd.DataFrame.from_records(unpaired_rows, columns=["monday_date", "expected_tuesday_date", "reason", "monday_n_sessions", "monday_total_duration_s"])
        return pairs_df, unpaired_df

    @classmethod
    def compute_paired_statistics(cls, pairs_df: pd.DataFrame) -> pd.DataFrame:
        """Per-metric paired stats across week-pairs: mean/median diff, paired t-test, Wilcoxon (NaN when guarded)."""
        rows: List[Dict[str, Any]] = []
        if pairs_df.empty:
            return pd.DataFrame.from_records(rows, columns=["metric", "n_pairs", "mean_difference", "median_difference", "t_statistic", "t_p_value", "wilcoxon_statistic", "wilcoxon_p_value"])
        for metric, metric_df in pairs_df.groupby("metric", sort=True):
            diffs = metric_df["difference"].to_numpy(dtype=np.float64)
            diffs = diffs[np.isfinite(diffs)]
            n_pairs = int(diffs.size)
            row: Dict[str, Any] = {"metric": metric, "n_pairs": n_pairs, "mean_difference": float(np.mean(diffs)) if n_pairs else float("nan"), "median_difference": float(np.median(diffs)) if n_pairs else float("nan"), "t_statistic": float("nan"), "t_p_value": float("nan"), "wilcoxon_statistic": float("nan"), "wilcoxon_p_value": float("nan")}
            if n_pairs >= 2 and np.std(diffs) > 0:
                t_result = scipy_stats.ttest_1samp(diffs, popmean=0.0)
                row["t_statistic"] = float(t_result.statistic)
                row["t_p_value"] = float(t_result.pvalue)
            if n_pairs >= 1 and np.any(diffs != 0):
                try:
                    w_result = scipy_stats.wilcoxon(diffs)
                    row["wilcoxon_statistic"] = float(w_result.statistic)
                    row["wilcoxon_p_value"] = float(w_result.pvalue)
                except ValueError:
                    pass
            rows.append(row)
        ## END for metric, metric_df in pairs_df.groupby("metric", sort=True)....

        return pd.DataFrame.from_records(rows)

    @classmethod
    def export_csvs(cls, daily_df: pd.DataFrame, pairs_df: pd.DataFrame, stats_df: pd.DataFrame, unpaired_df: pd.DataFrame, output_dir: Union[str, Path]) -> Dict[str, Path]:
        """Write daily_aggregates.csv, monday_tuesday_pairs.csv, paired_stats.csv, unpaired_mondays.csv."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "daily_aggregates": output_dir / "daily_aggregates.csv",
            "monday_tuesday_pairs": output_dir / "monday_tuesday_pairs.csv",
            "paired_stats": output_dir / "paired_stats.csv",
            "unpaired_mondays": output_dir / "unpaired_mondays.csv",
        }
        daily_df.to_csv(paths["daily_aggregates"], index=False)
        pairs_df.to_csv(paths["monday_tuesday_pairs"], index=False)
        stats_df.to_csv(paths["paired_stats"], index=False)
        unpaired_df.to_csv(paths["unpaired_mondays"], index=False)
        return paths

    @classmethod
    def export_paired_line_pngs(cls, pairs_df: pd.DataFrame, stats_df: pd.DataFrame, output_dir: Union[str, Path]) -> List[Path]:
        """One Matplotlib PNG per metric: Monday→Tuesday paired lines across week-pairs."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        output_dir = Path(output_dir)
        plots_dir = output_dir / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        if pairs_df.empty:
            return written
        stats_by_metric = stats_df.set_index("metric") if not stats_df.empty else None
        for metric, metric_df in pairs_df.groupby("metric", sort=True):
            metric_df = metric_df.sort_values("monday_date").reset_index(drop=True)
            fig, ax = plt.subplots(figsize=(6.0, 4.0))
            n_pairs = len(metric_df)
            for pair_idx, pair_row in metric_df.iterrows():
                ax.plot([0, 1], [pair_row["monday_value"], pair_row["tuesday_value"]], marker="o", color="#4878a8", alpha=0.65, linewidth=1.2)
                ax.annotate(str(pair_row["monday_date"]), xy=(0, pair_row["monday_value"]), xytext=(-6, 0), textcoords="offset points", ha="right", va="center", fontsize=6, color="#555555")
            ## END for pair_idx, pair_row in metric_df.iterrows()....

            ax.set_xticks([0, 1])
            ax.set_xticklabels(["Monday", "Tuesday"])
            ax.set_xlim(-0.6, 1.4)
            ax.set_ylabel(metric)
            title = f"{metric} (Tue − Mon), n_pairs={n_pairs}"
            if stats_by_metric is not None and metric in stats_by_metric.index:
                stat_row = stats_by_metric.loc[metric]
                title += f"\nmean Δ={stat_row['mean_difference']:.4g}, t p={stat_row['t_p_value']:.3g}, Wilcoxon p={stat_row['wilcoxon_p_value']:.3g}"
            ax.set_title(title, fontsize=9)
            ax.grid(axis="y", alpha=0.3)
            fig.tight_layout()
            png_path = plots_dir / f"paired_{metric}.png"
            fig.savefig(png_path, dpi=150)
            plt.close(fig)
            written.append(png_path)
        ## END for metric, metric_df in pairs_df.groupby("metric", sort=True)....

        return written

    @classmethod
    def export_html_report(cls, daily_df: pd.DataFrame, pairs_df: pd.DataFrame, stats_df: pd.DataFrame, unpaired_df: pd.DataFrame, output_dir: Union[str, Path], timezone_name: str = DEFAULT_TIMEZONE, source_csv_paths: Optional[Sequence[Union[str, Path]]] = None) -> Path:
        """Single self-contained interactive HTML report (Panel + HoloViews paired-line plots + tables)."""
        import holoviews as hv
        import panel as pn

        hv.extension("bokeh", logo=False)
        pn.extension()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        widgets: List[Any] = []
        source_list = ", ".join(Path(p).name for p in (source_csv_paths or []))
        widgets.append(pn.pane.Markdown(f"# Monday vs Tuesday EEG comparison\nWeekdays assigned in **{timezone_name}**. Sources: {source_list or '(none)'}.\n\n**{len(pairs_df['monday_date'].unique()) if not pairs_df.empty else 0} week-pairs** — interpret p-values cautiously with small n."))

        if not stats_df.empty:
            widgets.append(pn.pane.Markdown("## Paired statistics (Tuesday − Monday)"))
            widgets.append(pn.widgets.DataFrame(stats_df, autosize_mode="fit_columns", height=min(40 + 28 * len(stats_df), 420)))

        if not pairs_df.empty:
            widgets.append(pn.pane.Markdown("## Paired lines per metric"))
            for metric, metric_df in pairs_df.groupby("metric", sort=True):
                metric_df = metric_df.sort_values("monday_date").reset_index(drop=True)
                segments: List[hv.Curve] = []
                for pair_idx, pair_row in metric_df.iterrows():
                    label = f"{pair_row['monday_date']} → {pair_row['tuesday_date']}"
                    segments.append(hv.Curve(([0, 1], [pair_row["monday_value"], pair_row["tuesday_value"]]), label=label).opts(color="#4878a8", alpha=0.6, line_width=1.5))
                ## END for pair_idx, pair_row in metric_df.iterrows()....

                points = hv.Points(metric_df.melt(id_vars=["monday_date"], value_vars=["monday_value", "tuesday_value"], var_name="day", value_name="value").assign(weekday_idx=lambda d: d["day"].map({"monday_value": 0, "tuesday_value": 1})), kdims=["weekday_idx", "value"]).opts(size=6, color="#c44e52", tools=["hover"])
                overlay = hv.Overlay(segments) * points
                overlay = overlay.opts(title=f"{metric}: Monday → Tuesday", xlabel="", ylabel=metric, width=560, height=300, xticks=[(0, "Monday"), (1, "Tuesday")], xlim=(-0.35, 1.35), show_legend=False, toolbar="above")
                widgets.append(pn.pane.HoloViews(overlay, sizing_mode="stretch_width"))
            ## END for metric, metric_df in pairs_df.groupby("metric", sort=True)....

        if not unpaired_df.empty:
            widgets.append(pn.pane.Markdown("## Unpaired Mondays (no following-Tuesday data)"))
            widgets.append(pn.widgets.DataFrame(unpaired_df, autosize_mode="fit_columns", height=min(40 + 28 * len(unpaired_df), 260)))

        widgets.append(pn.pane.Markdown("## Daily aggregates (duration-weighted)"))
        widgets.append(pn.widgets.DataFrame(daily_df, autosize_mode="fit_columns", height=420))

        report = pn.Column(*widgets, sizing_mode="stretch_width")
        html_path = output_dir / "monday_tuesday_comparison_report.html"
        report.save(html_path, embed=True, title="Monday vs Tuesday EEG comparison")
        return html_path

    @classmethod
    def run_comparison(cls, summary_csv_paths: Sequence[Union[str, Path]], output_dir: Union[str, Path], timezone_name: str = DEFAULT_TIMEZONE, should_export_pngs: bool = True, should_export_html: bool = True) -> Dict[str, Any]:
        """End-to-end: load → aggregate → pair → stats → exports. Returns dict of dataframes and written paths."""
        sessions_df = cls.load_session_metrics(summary_csv_paths, timezone_name=timezone_name)
        daily_df = cls.aggregate_daily_metrics(sessions_df)
        pairs_df, unpaired_df = cls.build_monday_tuesday_pairs(daily_df)
        stats_df = cls.compute_paired_statistics(pairs_df)
        output_dir = Path(output_dir)
        csv_paths = cls.export_csvs(daily_df, pairs_df, stats_df, unpaired_df, output_dir)
        png_paths = cls.export_paired_line_pngs(pairs_df, stats_df, output_dir) if should_export_pngs else []
        html_path = cls.export_html_report(daily_df, pairs_df, stats_df, unpaired_df, output_dir, timezone_name=timezone_name, source_csv_paths=summary_csv_paths) if should_export_html else None
        n_pairs = int(pairs_df["monday_date"].nunique()) if not pairs_df.empty else 0
        print(f"Loaded {len(sessions_df)} sessions → {len(daily_df)} local days; {n_pairs} Mon→Tue week-pairs; {len(unpaired_df)} unpaired Mondays")
        return {"sessions_df": sessions_df, "daily_df": daily_df, "pairs_df": pairs_df, "unpaired_df": unpaired_df, "stats_df": stats_df, "csv_paths": csv_paths, "png_paths": png_paths, "html_path": html_path}
