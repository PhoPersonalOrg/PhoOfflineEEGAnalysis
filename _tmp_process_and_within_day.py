"""Process missing preceding-two-month sessions (prefer NPZ reuse) + within-day early-vs-late analysis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from main_analyze_run import SpectogramPlottingHelper, lab_recorder_output_path, outputs_root_folder, pickled_data_path
from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog
from phoofflineeeganalysis.analysis.weekday_pair_comparison import DAILY_NON_METRIC_COLUMNS, DEFAULT_TIMEZONE, MondayTuesdayComparison


FOCUS_METRICS = [
	"alpha_rel", "beta_rel", "theta_rel", "delta_rel",
	"alpha_theta_ratio", "beta_alpha_ratio", "dominant_freq_hz",
	"total_power_1.0_to_40.0_hz",
]
EARLY_END_S = 30 * 60.0
MIN_SESSION_S_FOR_SPLIT = 45 * 60.0
TZ = ZoneInfo(DEFAULT_TIMEZONE)
NPZ_TS_RE = re.compile(r"spectrograms_(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})\.npz$")


def _band_masks(freqs: np.ndarray) -> dict[str, np.ndarray]:
	return {
		"delta": (freqs >= 1.0) & (freqs < 4.0),
		"theta": (freqs >= 4.0) & (freqs < 8.0),
		"alpha": (freqs >= 8.0) & (freqs < 13.0),
		"beta": (freqs >= 13.0) & (freqs < 30.0),
		"total": (freqs >= 1.0) & (freqs <= 40.0),
	}


def _safe_div(n: float, d: float) -> float:
	return float(n / d) if np.isfinite(n) and np.isfinite(d) and d != 0 else float("nan")


def _metrics_from_Sxx_slice(Sxx: np.ndarray, freqs: np.ndarray) -> dict[str, float]:
	"""Sxx shape (n_ch, n_freq[, n_time]); mean over channels and optional time."""
	masks = _band_masks(freqs)
	if Sxx.ndim == 3:
		vals = np.nanmean(Sxx, axis=(0, 2))
	else:
		vals = np.nanmean(Sxx, axis=0)
	total = float(np.nanmean(vals[masks["total"]])) if np.any(masks["total"]) else float("nan")
	delta = float(np.nanmean(vals[masks["delta"]])) if np.any(masks["delta"]) else float("nan")
	theta = float(np.nanmean(vals[masks["theta"]])) if np.any(masks["theta"]) else float("nan")
	alpha = float(np.nanmean(vals[masks["alpha"]])) if np.any(masks["alpha"]) else float("nan")
	beta = float(np.nanmean(vals[masks["beta"]])) if np.any(masks["beta"]) else float("nan")
	dom = float("nan")
	if np.any(masks["total"]):
		band_vals = vals[masks["total"]]
		band_f = freqs[masks["total"]]
		if np.any(np.isfinite(band_vals)):
			dom = float(band_f[int(np.nanargmax(band_vals))])
	return {
		"total_power_1.0_to_40.0_hz": total,
		"delta_power": delta,
		"theta_power": theta,
		"alpha_power": alpha,
		"beta_power": beta,
		"delta_rel": _safe_div(delta, total),
		"theta_rel": _safe_div(theta, total),
		"alpha_rel": _safe_div(alpha, total),
		"beta_rel": _safe_div(beta, total),
		"alpha_theta_ratio": _safe_div(alpha, theta),
		"beta_alpha_ratio": _safe_div(beta, alpha),
		"dominant_freq_hz": dom,
	}


def _time_series_metrics(Sxx: np.ndarray, freqs: np.ndarray, times: np.ndarray) -> pd.DataFrame:
	"""Per-timepoint channel-mean band metrics. Sxx: (n_ch, n_freq, n_time)."""
	masks = _band_masks(freqs)
	ch_mean = np.nanmean(Sxx, axis=0)  # (n_freq, n_time)
	total = np.nanmean(ch_mean[masks["total"], :], axis=0)
	delta = np.nanmean(ch_mean[masks["delta"], :], axis=0)
	theta = np.nanmean(ch_mean[masks["theta"], :], axis=0)
	alpha = np.nanmean(ch_mean[masks["alpha"], :], axis=0)
	beta = np.nanmean(ch_mean[masks["beta"], :], axis=0)
	with np.errstate(divide="ignore", invalid="ignore"):
		df = pd.DataFrame({
			"t_sec": times.astype(float),
			"total_power_1.0_to_40.0_hz": total,
			"delta_rel": delta / total,
			"theta_rel": theta / total,
			"alpha_rel": alpha / total,
			"beta_rel": beta / total,
			"alpha_theta_ratio": alpha / theta,
			"beta_alpha_ratio": beta / alpha,
		})
	return df


def _find_npz_for_meas(outputs_root: Path, meas_utc: datetime) -> Path | None:
	"""Newest npz matching spectrograms_YYYY-MM-DDTHH-MM-SS for this meas time."""
	stamp = meas_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
	# npz uses hyphens in time
	stamp_npz = stamp.replace(":", "-")
	cands = sorted(outputs_root.glob(f"*spectrograms_{stamp_npz}.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
	return cands[0] if cands else None


def _index_all_npz(outputs_root: Path) -> pd.DataFrame:
	rows = []
	for p in outputs_root.glob("*spectrograms_*.npz"):
		m = NPZ_TS_RE.search(p.name)
		if not m:
			continue
		meas_utc = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}").replace(tzinfo=timezone.utc)
		meas_local = meas_utc.astimezone(TZ)
		rows.append({
			"npz_path": str(p),
			"npz_mtime": p.stat().st_mtime,
			"meas_date_utc": meas_utc,
			"local_date": meas_local.strftime("%Y-%m-%d"),
			"meas_date_local": meas_local.strftime("%Y-%m-%d %H:%M:%S"),
			"weekday": meas_local.weekday(),
			"weekday_name": meas_local.strftime("%A"),
			"stamp": f"{m.group(1)}T{m.group(2)}-{m.group(3)}-{m.group(4)}",
		})
	## END for p in outputs_root.glob("*spectrograms_*.npz")...

	df = pd.DataFrame(rows)
	if df.empty:
		return df
	df = df.sort_values(["stamp", "npz_mtime"]).drop_duplicates("stamp", keep="last").reset_index(drop=True)
	return df


def _summary_row_from_npz(npz_path: Path, xdf_filename: str | None = None) -> dict | None:
	d = np.load(npz_path, allow_pickle=True)
	if "s0_Sxx" not in d or "s0_freqs" not in d or "s0_times" not in d:
		return None
	Sxx = np.asarray(d["s0_Sxx"], dtype=float)
	freqs = np.asarray(d["s0_freqs"], dtype=float)
	times = np.asarray(d["s0_times"], dtype=float)
	meas_sec = float(d["s0_meas_date_sec"]) if "s0_meas_date_sec" in d else float("nan")
	meas_dt = datetime.fromtimestamp(meas_sec, tz=timezone.utc) if np.isfinite(meas_sec) else None
	metrics = _metrics_from_Sxx_slice(Sxx, freqs)
	duration_s = float(times[-1] - times[0]) if times.size else float("nan")
	n_channels = int(Sxx.shape[0])
	return {
		"session_idx": 0,
		"xdf_filename": xdf_filename,
		"meas_date": meas_dt.strftime("%Y-%m-%d %H:%M:%S") if meas_dt else "",
		"duration_s": duration_s,
		"sfreq_hz": float("nan"),
		"n_channels": n_channels,
		**metrics,
		"manifold_n_windows": float("nan"),
		"manifold_pc1_std": float("nan"),
		"manifold_pc2_std": float("nan"),
		"manifold_pc3_std": float("nan"),
		"manifold_pc1_range": float("nan"),
		"manifold_pc2_range": float("nan"),
		"manifold_pc3_range": float("nan"),
		"manifold_traj_path_length": float("nan"),
		"manifold_traj_speed_rms": float("nan"),
		"_source_npz": str(npz_path),
	}


def process_missing_xdfs(missing_df: pd.DataFrame) -> Path | None:
	"""Process size>=2MB missing XDFs with streaming sink; return incremental summary CSV path."""
	to_process = missing_df.loc[missing_df["size_mb"] >= 2.0].copy()
	tiny = missing_df.loc[missing_df["size_mb"] < 2.0]
	print(f"Missing total={len(missing_df)}; process size>=2MB: {len(to_process)}; skip tiny: {len(tiny)}")
	for _, r in tiny.iterrows():
		print(f"  SKIP tiny {r['local_date']} {r['size_mb']} MB {r['name']}")
	## END for _, r in tiny.iterrows()...

	if to_process.empty:
		return None

	# Prefer deriving from existing NPZ first
	npz_index = _index_all_npz(outputs_root_folder)
	derived_rows = []
	still_need = []
	for _, r in to_process.iterrows():
		# match by filename UTC stamp -> npz stamp
		m = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})", r["name"])
		if m and not npz_index.empty:
			stamp = f"{m.group(1)}T{m.group(2)}-{m.group(3)}-{m.group(4)}"
			hit = npz_index.loc[npz_index["stamp"] == stamp]
			if not hit.empty:
				row = _summary_row_from_npz(Path(hit.iloc[0]["npz_path"]), xdf_filename=r["name"])
				if row is not None:
					derived_rows.append(row)
					print(f"  REUSE NPZ {r['name']} <- {Path(hit.iloc[0]['npz_path']).name}")
					continue
		still_need.append(r)
	## END for _, r in to_process.iterrows()...

	summaries_dir = outputs_root_folder / "session_summaries"
	summaries_dir.mkdir(parents=True, exist_ok=True)
	parts = []
	if derived_rows:
		derived_df = pd.DataFrame(derived_rows)
		derived_path = summaries_dir / f"{datetime.now().strftime('%Y-%m-%d_')}preceding_from_npz_session_summaries.csv"
		# drop helper
		out_cols = [c for c in derived_df.columns if not c.startswith("_")]
		derived_df[out_cols].to_csv(derived_path, index=False)
		print(f"Wrote NPZ-derived summaries: {derived_path} n={len(derived_df)}")
		parts.append(derived_path)

	if still_need:
		print(f"Need full XDF process for {len(still_need)} files...")
		usable = [r["name"] for r in still_need]
		paths = [Path(r["path"]) for r in still_need]
		catalog = get_or_create_catalog(pickled_data_path=pickled_data_path)
		summary_rows: list[dict] = []
		sink_lock = __import__("threading").Lock()

		def _sink(idx, eeg_raw, motion_raw, stream_infos, result):
			xdf_filename = None
			try:
				if stream_infos is not None and "xdf_filename" in getattr(stream_infos, "columns", []):
					xdf_filename = str(stream_infos["xdf_filename"].iloc[0])
			except Exception:
				pass
			row = SpectogramPlottingHelper._compute_one_session_summary_row(idx, eeg_raw, result, xdf_filename=xdf_filename) if hasattr(SpectogramPlottingHelper, "_compute_one_session_summary_row") else None
			# fallback: use public batch helper on single session
			if row is None:
				tmp = SpectogramPlottingHelper.compute_session_summary_metrics([eeg_raw], [result], stream_infos, summaries_dir / "_tmp_one", filename_prefix=f"one_{idx}_")
				# actually writes file; read it
				one_df = pd.read_csv(tmp)
				if len(one_df):
					rec = one_df.iloc[0].to_dict()
					if xdf_filename:
						rec["xdf_filename"] = xdf_filename
					with sink_lock:
						summary_rows.append(rec)
				return
			with sink_lock:
				summary_rows.append(row)

		# Simpler: retain_in_memory True but in chunks of 3 to limit RAM
		chunk_size = 3
		for start in range(0, len(usable), chunk_size):
			chunk_names = usable[start:start + chunk_size]
			chunk_paths = paths[start:start + chunk_size]
			print(f"Processing chunk {start // chunk_size + 1}: {chunk_names}")
			_sso, _uids, stream_infos_df, eeg_raws, _motion, results = SpectogramPlottingHelper.process_XDFs_main(
				n_most_recent_sessions_to_preprocess=None,
				should_write_final_merged_eeg_fif=False,
				included_xdf_file_names=chunk_names,
				included_xdf_paths=chunk_paths,
				should_load_preprocessed=False,
				use_computation_cache=True,
				absolute_max_workers=2,
				session_catalog=catalog,
				use_session_catalog=True,
				retain_in_memory=True,
			)
			prefix = datetime.now().strftime("%Y-%m-%d_") + f"preceding_chunk{start // chunk_size}_"
			csv_path = SpectogramPlottingHelper.compute_session_summary_metrics(eeg_raws, results, stream_infos_df, summaries_dir, filename_prefix=prefix)
			parts.append(csv_path)
			print(f"  chunk summary: {csv_path}")
			del eeg_raws, results, stream_infos_df
			import gc
			gc.collect()
		## END for start in range(0, len(usable), chunk_size)...

	return parts[0] if parts else None


def run_weekday_trend(sessions_csv: Path, start_local, end_local, out_dir: Path) -> None:
	sessions_df = MondayTuesdayComparison.load_session_metrics([sessions_csv], timezone_name=DEFAULT_TIMEZONE)
	sessions_df["local_date_dt"] = pd.to_datetime(sessions_df["local_date"])
	mask = (sessions_df["local_date_dt"].dt.date >= start_local) & (sessions_df["local_date_dt"].dt.date <= end_local)
	recent = sessions_df.loc[mask].copy()
	print(f"Weekday trend window {start_local} -> {end_local}: {len(recent)} sessions")
	daily = MondayTuesdayComparison.aggregate_daily_metrics(recent)
	weekday = daily.loc[daily["weekday"].between(0, 4)].copy()
	metric_cols = [c for c in weekday.columns if c not in DAILY_NON_METRIC_COLUMNS and pd.api.types.is_numeric_dtype(weekday[c])]
	out_dir.mkdir(parents=True, exist_ok=True)
	(out_dir / "plots").mkdir(parents=True, exist_ok=True)

	agg_kwargs = {"n_days": ("local_date", "count"), "total_duration_s": ("total_duration_s", "sum")}
	for m in metric_cols:
		agg_kwargs[m] = (m, "mean")
	weekday_means = weekday.groupby("weekday", as_index=False).agg(**agg_kwargs)
	weekday_means["weekday_name"] = weekday_means["weekday"].map({0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"})
	weekday_means.to_csv(out_dir / "weekday_means.csv", index=False)

	trend_rows = []
	for metric in metric_cols:
		x = weekday["weekday"].to_numpy(dtype=float)
		y = weekday[metric].to_numpy(dtype=float)
		valid = np.isfinite(x) & np.isfinite(y)
		x, y = x[valid], y[valid]
		row = {"metric": metric, "n_days": int(y.size), "slope_per_weekday": np.nan, "r_value": np.nan, "p_value": np.nan, "fri_minus_mon": np.nan}
		if y.size >= 3 and np.std(x) > 0 and np.std(y) > 0:
			slope, _i, r, p, _se = scipy_stats.linregress(x, y)
			row.update({"slope_per_weekday": float(slope), "r_value": float(r), "p_value": float(p)})
		mon = weekday.loc[weekday["weekday"] == 0, metric].to_numpy(dtype=float)
		fri = weekday.loc[weekday["weekday"] == 4, metric].to_numpy(dtype=float)
		mon, fri = mon[np.isfinite(mon)], fri[np.isfinite(fri)]
		if mon.size and fri.size:
			row["fri_minus_mon"] = float(np.mean(fri) - np.mean(mon))
		trend_rows.append(row)
	## END for metric in metric_cols...

	trend_df = pd.DataFrame(trend_rows).sort_values("p_value", kind="stable")
	trend_df.to_csv(out_dir / "weekday_linear_trends.csv", index=False)
	print("\n3mo weekday linear trends (focus):")
	print(trend_df.loc[trend_df["metric"].isin(FOCUS_METRICS), ["metric", "n_days", "slope_per_weekday", "p_value", "fri_minus_mon"]].to_string(index=False))


def run_within_day_analysis(start_local, end_local, out_dir: Path) -> None:
	"""Detect stark early→late drops in first recording of each day, and across-session daily course."""
	out_dir.mkdir(parents=True, exist_ok=True)
	plots_dir = out_dir / "plots"
	plots_dir.mkdir(parents=True, exist_ok=True)
	npz_index = _index_all_npz(outputs_root_folder)
	if npz_index.empty:
		print("No NPZ spectrograms found for within-day analysis")
		return
	npz_index["local_date_dt"] = pd.to_datetime(npz_index["local_date"])
	mask = (npz_index["local_date_dt"].dt.date >= start_local) & (npz_index["local_date_dt"].dt.date <= end_local)
	npz_index = npz_index.loc[mask].sort_values(["local_date", "meas_date_utc"]).reset_index(drop=True)
	print(f"Within-day NPZs in window: {len(npz_index)} across {npz_index['local_date'].nunique()} days")

	split_rows = []
	course_rows = []
	drop_events = []

	for local_date, day_df in npz_index.groupby("local_date", sort=True):
		day_df = day_df.sort_values("meas_date_utc").reset_index(drop=True)
		session_metrics = []
		for sess_i, (_, srow) in enumerate(day_df.iterrows()):
			npz_path = Path(srow["npz_path"])
			d = np.load(npz_path, allow_pickle=True)
			if "s0_Sxx" not in d:
				continue
			Sxx = np.asarray(d["s0_Sxx"], dtype=float)
			freqs = np.asarray(d["s0_freqs"], dtype=float)
			times = np.asarray(d["s0_times"], dtype=float)
			if times.size < 5:
				continue
			# normalize times to start at 0 relative to session
			t0 = float(times[0])
			t_rel = times - t0
			duration = float(t_rel[-1])
			overall = _metrics_from_Sxx_slice(Sxx, freqs)
			overall.update({
				"local_date": local_date,
				"session_ord": sess_i,
				"is_first_of_day": sess_i == 0,
				"meas_date_local": srow["meas_date_local"],
				"weekday_name": srow["weekday_name"],
				"duration_s": duration,
				"npz_path": str(npz_path),
			})
			session_metrics.append(overall)

			# Early vs late split for long enough sessions
			if duration >= MIN_SESSION_S_FOR_SPLIT:
				early_mask = t_rel <= EARLY_END_S
				late_mask = t_rel > EARLY_END_S
				if np.sum(early_mask) >= 3 and np.sum(late_mask) >= 3:
					early = _metrics_from_Sxx_slice(Sxx[:, :, early_mask], freqs)
					late = _metrics_from_Sxx_slice(Sxx[:, :, late_mask], freqs)
					for metric in FOCUS_METRICS:
						ev, lv = early[metric], late[metric]
						rel_change = _safe_div(lv - ev, abs(ev)) if np.isfinite(ev) else float("nan")
						split_rows.append({
							"local_date": local_date,
							"session_ord": sess_i,
							"is_first_of_day": sess_i == 0,
							"meas_date_local": srow["meas_date_local"],
							"weekday_name": srow["weekday_name"],
							"duration_s": duration,
							"metric": metric,
							"early_0_30min": ev,
							"late_after_30min": lv,
							"abs_change": lv - ev if np.isfinite(ev) and np.isfinite(lv) else float("nan"),
							"rel_change": rel_change,
						})
					# Stark drop heuristic on key metrics for first-of-day
					if sess_i == 0:
						for metric, direction in [("alpha_rel", "drop"), ("alpha_theta_ratio", "drop"), ("total_power_1.0_to_40.0_hz", "drop"), ("theta_rel", "rise"), ("beta_alpha_ratio", "rise")]:
							ev, lv = early[metric], late[metric]
							if not (np.isfinite(ev) and np.isfinite(lv) and abs(ev) > 1e-12):
								continue
							rel = (lv - ev) / abs(ev)
							is_stark = (direction == "drop" and rel <= -0.25) or (direction == "rise" and rel >= 0.25)
							if is_stark:
								drop_events.append({
									"local_date": local_date,
									"meas_date_local": srow["meas_date_local"],
									"weekday_name": srow["weekday_name"],
									"metric": metric,
									"direction": direction,
									"early": ev,
									"late": lv,
									"rel_change": rel,
									"duration_s": duration,
								})
		## END for sess_i, (_, srow) in enumerate(day_df.iterrows())...

		# Across-session course within the day
		for sm in session_metrics:
			for metric in FOCUS_METRICS:
				course_rows.append({
					"local_date": local_date,
					"session_ord": sm["session_ord"],
					"n_sessions_day": len(session_metrics),
					"meas_date_local": sm["meas_date_local"],
					"weekday_name": sm["weekday_name"],
					"duration_s": sm["duration_s"],
					"metric": metric,
					"value": sm[metric],
				})
		## END for sm in session_metrics...

	## END for local_date, day_df in npz_index.groupby("local_date", sort=True)...

	splits_df = pd.DataFrame(split_rows)
	course_df = pd.DataFrame(course_rows)
	drops_df = pd.DataFrame(drop_events)
	splits_df.to_csv(out_dir / "early_vs_late_splits.csv", index=False)
	course_df.to_csv(out_dir / "within_day_session_course.csv", index=False)
	drops_df.to_csv(out_dir / "stark_early_late_events.csv", index=False)

	# Aggregate first-of-day early vs late stats
	first_splits = splits_df.loc[splits_df["is_first_of_day"]].copy() if not splits_df.empty else splits_df
	agg_rows = []
	if not first_splits.empty:
		for metric, mdf in first_splits.groupby("metric"):
			diffs = mdf["abs_change"].to_numpy(dtype=float)
			rels = mdf["rel_change"].to_numpy(dtype=float)
			diffs = diffs[np.isfinite(diffs)]
			rels = rels[np.isfinite(rels)]
			row = {
				"metric": metric,
				"n_first_sessions": int(len(mdf)),
				"mean_abs_change": float(np.mean(diffs)) if diffs.size else np.nan,
				"median_abs_change": float(np.median(diffs)) if diffs.size else np.nan,
				"mean_rel_change": float(np.mean(rels)) if rels.size else np.nan,
				"median_rel_change": float(np.median(rels)) if rels.size else np.nan,
				"pct_drop_ge_25pct": float(np.mean(rels <= -0.25) * 100) if rels.size else np.nan,
				"pct_rise_ge_25pct": float(np.mean(rels >= 0.25) * 100) if rels.size else np.nan,
				"t_p_value": np.nan,
			}
			if diffs.size >= 2 and np.std(diffs) > 0:
				row["t_p_value"] = float(scipy_stats.ttest_1samp(diffs, 0.0).pvalue)
			agg_rows.append(row)
		## END for metric, mdf in first_splits.groupby("metric")...

	agg_df = pd.DataFrame(agg_rows).sort_values("t_p_value", kind="stable") if agg_rows else pd.DataFrame()
	agg_df.to_csv(out_dir / "first_session_early_vs_late_stats.csv", index=False)

	# Plot paired early/late for first-of-day focus metrics
	if not first_splits.empty:
		for metric in FOCUS_METRICS:
			mdf = first_splits.loc[first_splits["metric"] == metric].sort_values("local_date")
			if mdf.empty:
				continue
			fig, ax = plt.subplots(figsize=(6.5, 4.2))
			for _, r in mdf.iterrows():
				ax.plot([0, 1], [r["early_0_30min"], r["late_after_30min"]], marker="o", color="#4878a8", alpha=0.55, linewidth=1.2)
				ax.annotate(str(r["local_date"])[5:], xy=(0, r["early_0_30min"]), xytext=(-4, 0), textcoords="offset points", ha="right", va="center", fontsize=6, color="#555555")
			## END for _, r in mdf.iterrows()...

			ax.set_xticks([0, 1])
			ax.set_xticklabels(["0–30 min", "After 30 min"])
			ax.set_xlim(-0.55, 1.35)
			stat = agg_df.loc[agg_df["metric"] == metric]
			title = f"First-of-day {metric}"
			if not stat.empty:
				title += f"\nmean Δ={stat.iloc[0]['mean_abs_change']:.4g}, mean rel={stat.iloc[0]['mean_rel_change']:.3f}, p={stat.iloc[0]['t_p_value']:.3g}"
			ax.set_title(title, fontsize=9)
			ax.set_ylabel(metric)
			ax.grid(axis="y", alpha=0.3)
			fig.tight_layout()
			fig.savefig(plots_dir / f"first_session_early_late_{metric}.png", dpi=150)
			plt.close(fig)
		## END for metric in FOCUS_METRICS...

	# Plot multi-session day courses for days with >=2 sessions
	if not course_df.empty:
		multi = course_df.loc[course_df["n_sessions_day"] >= 2]
		for metric in ["alpha_rel", "theta_rel", "beta_alpha_ratio", "total_power_1.0_to_40.0_hz"]:
			mdf = multi.loc[multi["metric"] == metric]
			if mdf.empty:
				continue
			fig, ax = plt.subplots(figsize=(7.0, 4.2))
			for local_date, g in mdf.groupby("local_date"):
				g = g.sort_values("session_ord")
				ax.plot(g["session_ord"], g["value"], marker="o", alpha=0.6, linewidth=1.2, label=str(local_date)[5:])
			## END for local_date, g in mdf.groupby("local_date")...

			ax.set_xlabel("Session order within day (0=first)")
			ax.set_ylabel(metric)
			ax.set_title(f"Within-day session course: {metric}", fontsize=9)
			ax.grid(axis="y", alpha=0.3)
			if mdf["local_date"].nunique() <= 12:
				ax.legend(fontsize=6, ncol=2)
			fig.tight_layout()
			fig.savefig(plots_dir / f"day_course_{metric}.png", dpi=150)
			plt.close(fig)
		## END for metric in ...

	print("\n=== Within-day / first-session early vs late ===")
	print(f"Output: {out_dir.as_posix()}")
	if not agg_df.empty:
		print(agg_df.to_string(index=False))
	print(f"\nStark |rel_change|>=25% events on first-of-day: {len(drops_df)}")
	if not drops_df.empty:
		print(drops_df.sort_values(["local_date", "metric"]).to_string(index=False))


def main() -> None:
	summaries_dir = outputs_root_folder / "session_summaries"
	missing_csv = summaries_dir / "_missing_preceding_two_months.csv"
	missing_df = pd.read_csv(missing_csv)

	process_missing_xdfs(missing_df)

	# Merge all summary CSVs
	all_csvs = sorted([p for p in summaries_dir.glob("*session_summaries.csv") if not p.name.endswith("_empty.csv") and not p.name.startswith("_")], key=lambda p: p.stat().st_mtime)
	print(f"Merging {len(all_csvs)} summary CSVs")
	sessions_loaded = MondayTuesdayComparison.load_session_metrics(all_csvs, timezone_name=DEFAULT_TIMEZONE)
	merged_out = summaries_dir / f"{datetime.now().strftime('%Y-%m-%d_')}session_summaries.csv"
	sessions_export = sessions_loaded.copy()
	sessions_export["meas_date"] = sessions_export["meas_date_utc"].dt.strftime("%Y-%m-%d %H:%M:%S")
	template_cols = list(pd.read_csv(all_csvs[-1]).columns)
	for c in template_cols:
		if c not in sessions_export.columns:
			sessions_export[c] = np.nan
	## END for c in template_cols...

	sessions_export[template_cols].to_csv(merged_out, index=False)
	print(f"Merged summary: {merged_out.as_posix()} n={len(sessions_export)}")

	end_local = datetime.now(TZ).date()
	start_local = end_local - timedelta(days=92)
	trend_dir = outputs_root_folder / "monday_friday_trend" / datetime.now().strftime("%Y-%m-%d_monday_friday_trend_3mo")
	within_dir = outputs_root_folder / "within_day_trend" / datetime.now().strftime("%Y-%m-%d_within_day")

	run_weekday_trend(merged_out, start_local, end_local, trend_dir)
	run_within_day_analysis(start_local, end_local, within_dir)
	print("\nDONE")


if __name__ == "__main__":
	main()
