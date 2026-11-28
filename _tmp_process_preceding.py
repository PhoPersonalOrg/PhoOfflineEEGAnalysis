"""Process missing sessions from the two months preceding the last-month window, then re-run Mon→Fri trends over the full ~3 months."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from main_analyze_run import SpectogramPlottingHelper, lab_recorder_output_path, outputs_root_folder, pickled_data_path
from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog
from phoofflineeeganalysis.analysis.weekday_pair_comparison import DAILY_NON_METRIC_COLUMNS, DEFAULT_TIMEZONE, MondayTuesdayComparison, find_newest_summary_csv


FOCUS_METRICS = [
	"alpha_rel", "beta_rel", "theta_rel", "delta_rel",
	"alpha_theta_ratio", "beta_alpha_ratio", "dominant_freq_hz",
	"total_power_1.0_to_40.0_hz",
	"manifold_traj_speed_rms", "manifold_pc1_std",
]


def _all_summary_csvs(summaries_dir: Path) -> list[Path]:
	candidates = sorted(summaries_dir.glob("*session_summaries.csv"), key=lambda p: p.stat().st_mtime)
	return [p for p in candidates if not p.name.endswith("_empty.csv") and not p.name.startswith("_")]


def main() -> None:
	summaries_dir = outputs_root_folder / "session_summaries"
	missing_csv = summaries_dir / "_missing_preceding_two_months.csv"
	missing_df = pd.read_csv(missing_csv)
	# Skip tiny likely-empty files (<2 MB) first pass; still try medium+ files
	to_process = missing_df.loc[missing_df["size_mb"] >= 2.0].copy()
	tiny = missing_df.loc[missing_df["size_mb"] < 2.0]
	print(f"Missing total={len(missing_df)}; processing size>=2MB: {len(to_process)}; skipping tiny: {len(tiny)}")
	for _, r in tiny.iterrows():
		print(f"  SKIP tiny {r['local_date']} {r['size_mb']} MB {r['name']}")
	## END for _, r in tiny.iterrows()...

	usable = to_process["name"].tolist()
	paths = [Path(p) for p in to_process["path"].tolist()]
	catalog = get_or_create_catalog(pickled_data_path=pickled_data_path)

	new_csv = None
	if usable:
		_sso, _unique_idxs, stream_infos_df, active_only_out_eeg_raws, _motion_raws, results = SpectogramPlottingHelper.process_XDFs_main(
			n_most_recent_sessions_to_preprocess=None,
			should_write_final_merged_eeg_fif=False,
			included_xdf_file_names=usable,
			included_xdf_paths=paths,
			should_load_preprocessed=False,
			use_computation_cache=True,
			absolute_max_workers=2,
			session_catalog=catalog,
			use_session_catalog=True,
			retain_in_memory=True,
		)
		new_prefix = datetime.now().strftime("%Y-%m-%d_") + "preceding_incremental_"
		new_csv = SpectogramPlottingHelper.compute_session_summary_metrics(active_only_out_eeg_raws, results, stream_infos_df, summaries_dir, filename_prefix=new_prefix)
		print(f"new incremental summary: {new_csv} rows_guess={len(active_only_out_eeg_raws)}")
	else:
		print("No usable missing files to process")

	# Merge ALL summary CSVs (older exports may still hold Sep/early-Oct rows)
	all_csvs = _all_summary_csvs(summaries_dir)
	if new_csv is not None and Path(new_csv) not in all_csvs:
		all_csvs.append(Path(new_csv))
	print(f"Merging {len(all_csvs)} summary CSV(s)")
	for p in all_csvs:
		print(f"  - {p.name}")
	## END for p in all_csvs...

	sessions_loaded = MondayTuesdayComparison.load_session_metrics(all_csvs, timezone_name=DEFAULT_TIMEZONE)
	merged_out = summaries_dir / f"{datetime.now().strftime('%Y-%m-%d_')}session_summaries.csv"
	sessions_export = sessions_loaded.copy()
	sessions_export["meas_date"] = sessions_export["meas_date_utc"].dt.strftime("%Y-%m-%d %H:%M:%S")
	# Prefer column order from newest non-incremental export if possible
	template = find_newest_summary_csv(summaries_dir)
	old_cols = list(pd.read_csv(template).columns)
	for c in old_cols:
		if c not in sessions_export.columns:
			sessions_export[c] = np.nan
	## END for c in old_cols...

	sessions_export[old_cols].to_csv(merged_out, index=False)
	print(f"merged summary: {merged_out.as_posix()} n={len(sessions_export)}")

	# Full analysis window: start of preceding-two-months through today (~3 months)
	tz = ZoneInfo(DEFAULT_TIMEZONE)
	end_local = datetime.now(tz).date()
	start_local = end_local - timedelta(days=92)  # ~3 months
	sessions_df = MondayTuesdayComparison.load_session_metrics([merged_out], timezone_name=DEFAULT_TIMEZONE)
	sessions_df["local_date_dt"] = pd.to_datetime(sessions_df["local_date"])
	mask = (sessions_df["local_date_dt"].dt.date >= start_local) & (sessions_df["local_date_dt"].dt.date <= end_local)
	recent = sessions_df.loc[mask].copy()
	print(f"Window {start_local} -> {end_local}: {len(recent)} sessions")
	print(recent.groupby(["local_date", "weekday_name"]).size().to_string())

	daily = MondayTuesdayComparison.aggregate_daily_metrics(recent)
	weekday = daily.loc[daily["weekday"].between(0, 4)].copy()
	metric_cols = [c for c in weekday.columns if c not in DAILY_NON_METRIC_COLUMNS and pd.api.types.is_numeric_dtype(weekday[c])]

	out_dir = outputs_root_folder / "monday_friday_trend" / datetime.now().strftime("%Y-%m-%d_monday_friday_trend_3mo")
	plots_dir = out_dir / "plots"
	out_dir.mkdir(parents=True, exist_ok=True)
	plots_dir.mkdir(parents=True, exist_ok=True)

	agg_kwargs = {"n_days": ("local_date", "count"), "total_duration_s": ("total_duration_s", "sum")}
	for m in metric_cols:
		agg_kwargs[m] = (m, "mean")
	## END for m in metric_cols...

	weekday_means = weekday.groupby("weekday", as_index=False).agg(**agg_kwargs)
	weekday_means["weekday_name"] = weekday_means["weekday"].map({0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"})
	weekday_means.to_csv(out_dir / "weekday_means.csv", index=False)

	trend_rows = []
	for metric in metric_cols:
		x = weekday["weekday"].to_numpy(dtype=float)
		y = weekday[metric].to_numpy(dtype=float)
		valid = np.isfinite(x) & np.isfinite(y)
		x, y = x[valid], y[valid]
		row = {"metric": metric, "n_days": int(y.size), "slope_per_weekday": np.nan, "r_value": np.nan, "p_value": np.nan, "mon_mean": np.nan, "fri_mean": np.nan, "fri_minus_mon": np.nan}
		if y.size >= 3 and np.std(x) > 0 and np.std(y) > 0:
			slope, _intercept, r, p, _se = scipy_stats.linregress(x, y)
			row.update({"slope_per_weekday": float(slope), "r_value": float(r), "p_value": float(p)})
		mon = weekday.loc[weekday["weekday"] == 0, metric].to_numpy(dtype=float)
		fri = weekday.loc[weekday["weekday"] == 4, metric].to_numpy(dtype=float)
		mon = mon[np.isfinite(mon)]
		fri = fri[np.isfinite(fri)]
		if mon.size:
			row["mon_mean"] = float(np.mean(mon))
		if fri.size:
			row["fri_mean"] = float(np.mean(fri))
		if mon.size and fri.size:
			row["fri_minus_mon"] = row["fri_mean"] - row["mon_mean"]
		trend_rows.append(row)
	## END for metric in metric_cols...

	trend_df = pd.DataFrame(trend_rows).sort_values("p_value", kind="stable")
	trend_df.to_csv(out_dir / "weekday_linear_trends.csv", index=False)

	weekday["iso_year"] = pd.to_datetime(weekday["local_date"]).dt.isocalendar().year.astype(int)
	weekday["iso_week"] = pd.to_datetime(weekday["local_date"]).dt.isocalendar().week.astype(int)
	week_slope_rows = []
	week_profile_rows = []
	for (year, week), wdf in weekday.groupby(["iso_year", "iso_week"]):
		if wdf["weekday"].nunique() < 3:
			continue
		for metric in FOCUS_METRICS:
			if metric not in wdf.columns:
				continue
			x = wdf["weekday"].to_numpy(dtype=float)
			y = wdf[metric].to_numpy(dtype=float)
			valid = np.isfinite(x) & np.isfinite(y)
			x, y = x[valid], y[valid]
			if y.size < 3 or np.std(x) == 0:
				continue
			slope, _intercept, r, p, _se = scipy_stats.linregress(x, y)
			monday_date = str(wdf.loc[wdf["weekday"] == 0, "local_date"].iloc[0]) if (wdf["weekday"] == 0).any() else ""
			week_slope_rows.append({"iso_year": year, "iso_week": int(week), "metric": metric, "n_days": int(y.size), "slope_per_weekday": float(slope), "r_value": float(r), "p_value": float(p), "monday_date": monday_date})
		## END for metric in FOCUS_METRICS...

		for _, r in wdf.iterrows():
			week_profile_rows.append({"iso_year": year, "iso_week": int(week), "local_date": r["local_date"], "weekday": int(r["weekday"]), "weekday_name": r["weekday_name"], **{m: r[m] for m in FOCUS_METRICS if m in wdf.columns}})
		## END for _, r in wdf.iterrows()...

	## END for (year, week), wdf in weekday.groupby(["iso_year", "iso_week"])...

	week_slopes = pd.DataFrame(week_slope_rows)
	week_profiles = pd.DataFrame(week_profile_rows)
	week_slopes.to_csv(out_dir / "within_week_slopes.csv", index=False)
	week_profiles.to_csv(out_dir / "week_profiles.csv", index=False)

	summary_rows = []
	if not week_slopes.empty:
		for metric, mdf in week_slopes.groupby("metric"):
			slopes = mdf["slope_per_weekday"].to_numpy(dtype=float)
			slopes = slopes[np.isfinite(slopes)]
			row = {"metric": metric, "n_weeks": int(slopes.size), "mean_within_week_slope": float(np.mean(slopes)) if slopes.size else np.nan, "median_within_week_slope": float(np.median(slopes)) if slopes.size else np.nan, "pct_weeks_negative_slope": float(np.mean(slopes < 0) * 100) if slopes.size else np.nan, "pct_weeks_positive_slope": float(np.mean(slopes > 0) * 100) if slopes.size else np.nan, "t_p_value": np.nan}
			if slopes.size >= 2 and np.std(slopes) > 0:
				t = scipy_stats.ttest_1samp(slopes, 0.0)
				row["t_p_value"] = float(t.pvalue)
			summary_rows.append(row)
		## END for metric, mdf in week_slopes.groupby("metric")...

	within_summary = pd.DataFrame(summary_rows).sort_values("t_p_value", kind="stable") if summary_rows else pd.DataFrame()
	within_summary.to_csv(out_dir / "within_week_slope_summary.csv", index=False)

	pair_rows = []
	for (year, week), wdf in weekday.groupby(["iso_year", "iso_week"]):
		mon = wdf.loc[wdf["weekday"] == 0]
		fri = wdf.loc[wdf["weekday"] == 4]
		if mon.empty or fri.empty:
			continue
		mon_r, fri_r = mon.iloc[0], fri.iloc[0]
		for metric in metric_cols:
			pair_rows.append({"iso_year": year, "iso_week": int(week), "monday_date": mon_r["local_date"], "friday_date": fri_r["local_date"], "metric": metric, "monday_value": float(mon_r[metric]), "friday_value": float(fri_r[metric]), "difference": float(fri_r[metric]) - float(mon_r[metric])})
		## END for metric in metric_cols...

	## END for (year, week), wdf in weekday.groupby(["iso_year", "iso_week"])...

	pairs = pd.DataFrame(pair_rows)
	pairs.to_csv(out_dir / "monday_friday_pairs.csv", index=False)
	pair_stats = MondayTuesdayComparison.compute_paired_statistics(pairs)
	pair_stats.to_csv(out_dir / "monday_friday_paired_stats.csv", index=False)

	name_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
	rng = np.random.default_rng(0)
	for metric in FOCUS_METRICS:
		if metric not in weekday.columns:
			continue
		fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
		ax = axes[0]
		for wd in range(5):
			sub = weekday.loc[weekday["weekday"] == wd]
			jitter = rng.uniform(-0.08, 0.08, len(sub))
			ax.scatter(np.full(len(sub), wd) + jitter, sub[metric], alpha=0.65, color="#4878a8", s=28)
		## END for wd in range(5)...

		means = [weekday.loc[weekday["weekday"] == wd, metric].mean() for wd in range(5)]
		ax.plot(range(5), means, color="#c44e52", marker="o", linewidth=2)
		tr = trend_df.loc[trend_df["metric"] == metric]
		title = metric
		if not tr.empty:
			title += f"\nslope/day={tr.iloc[0]['slope_per_weekday']:.4g}, p={tr.iloc[0]['p_value']:.3g}, Fri-Mon={tr.iloc[0]['fri_minus_mon']:.4g}"
		ax.set_xticks(range(5))
		ax.set_xticklabels([name_map[i] for i in range(5)])
		ax.set_title(title, fontsize=9)
		ax.grid(axis="y", alpha=0.3)
		ax.set_ylabel(metric)

		ax = axes[1]
		if not week_profiles.empty and metric in week_profiles.columns:
			for (_year, _week), wdf in week_profiles.groupby(["iso_year", "iso_week"]):
				wdf = wdf.sort_values("weekday")
				ax.plot(wdf["weekday"], wdf[metric], marker="o", alpha=0.55, linewidth=1.2)
			## END for (_year, _week), wdf in week_profiles.groupby(["iso_year", "iso_week"])...

		ax.set_xticks(range(5))
		ax.set_xticklabels([name_map[i] for i in range(5)])
		ax.set_title(f"{metric}: within-week trajectories", fontsize=9)
		ax.grid(axis="y", alpha=0.3)
		fig.tight_layout()
		fig.savefig(plots_dir / f"trend_{metric}.png", dpi=150)
		plt.close(fig)
	## END for metric in FOCUS_METRICS...

	print("\n=== Monday→Friday trend (~3 months) ===")
	print(f"Output: {out_dir.as_posix()}")
	print(f"Daily Mon-Fri coverage: {weekday.groupby('weekday_name').size().to_dict()}")
	print("\nWeekday means (selected):")
	show_cols = ["weekday_name", "n_days"] + [m for m in FOCUS_METRICS if m in weekday_means.columns]
	print(weekday_means[show_cols].to_string(index=False))
	print("\nLinear trend across weekday (selected):")
	print(trend_df.loc[trend_df["metric"].isin(FOCUS_METRICS), ["metric", "n_days", "slope_per_weekday", "r_value", "p_value", "fri_minus_mon"]].to_string(index=False))
	if not within_summary.empty:
		print("\nWithin-week slope summary (selected):")
		print(within_summary.loc[within_summary["metric"].isin(FOCUS_METRICS)].to_string(index=False))
	if not pair_stats.empty:
		print("\nPaired Mon vs Fri (Fri − Mon):")
		print(pair_stats.loc[pair_stats["metric"].isin(FOCUS_METRICS)].to_string(index=False))

	print("\n=== Verdict heuristics ===")
	degrade_if_negative = {"alpha_rel", "alpha_theta_ratio", "beta_alpha_ratio", "dominant_freq_hz"}
	degrade_if_positive = {"theta_rel", "delta_rel", "total_power_1.0_to_40.0_hz"}
	notable = []
	for _, r in trend_df.iterrows():
		m = r["metric"]
		if m not in FOCUS_METRICS:
			continue
		if not np.isfinite(r["p_value"]) or r["p_value"] > 0.10:
			continue
		if m in degrade_if_negative and r["slope_per_weekday"] < 0:
			notable.append((m, "DECLINE toward Friday", r["slope_per_weekday"], r["p_value"]))
		elif m in degrade_if_positive and r["slope_per_weekday"] > 0:
			notable.append((m, "INCREASE toward Friday (possible fatigue/noise)", r["slope_per_weekday"], r["p_value"]))
		else:
			notable.append((m, "significant but not classic degradation direction", r["slope_per_weekday"], r["p_value"]))
	## END for _, r in trend_df.iterrows()...

	if not notable:
		print("No statistically notable Mon→Fri linear trends at p<0.10 among focus metrics.")
	else:
		for item in notable:
			print(item)
		## END for item in notable...

	n_pairs = 0 if pairs.empty else int(pairs["monday_date"].nunique())
	print(f"Paired Mon-Fri weeks: {n_pairs}")


if __name__ == "__main__":
	main()
