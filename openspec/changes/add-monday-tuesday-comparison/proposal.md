# Change: Add Monday-vs-Tuesday paired EEG session comparison

## Why
The user wants to compare EEG measures from every Monday recording day against the paired Tuesday (next calendar day) recording day, to detect systematic day-of-week differences in spectral and manifold metrics. Per-session summary metrics already exist as CSVs (`session_summaries/*.csv` from `SpectogramPlottingHelper.compute_session_summary_metrics`), but there is no weekday-aware aggregation, pairing, or paired-statistics analysis. Session timestamps are stored in UTC while recording days are experienced in local time (America/Los_Angeles), so naive UTC weekday assignment misclassifies evening sessions (e.g. `2026-11-17 00:15 UTC` is Monday 16:15 local).

## What Changes
- New analysis module `src/phoofflineeeganalysis/analysis/weekday_pair_comparison.py` exposing a `MondayTuesdayComparison` class (classmethod style, consistent with existing helpers):
  - Load one or more `session_summaries` CSVs; parse `meas_date` as UTC, convert to a configurable local timezone (default `America/Los_Angeles`), derive local recording date and weekday.
  - Deduplicate sessions across CSVs by `xdf_filename` (fallback `meas_date`), keeping the most recent export.
  - Aggregate metrics per local day: duration-weighted mean of each numeric metric, plus `n_sessions` and `total_duration_s`.
  - Pair each Monday that has data with the Tuesday at Monday-date + 1 day; Mondays without a following Tuesday are reported as unpaired (never silently dropped).
  - Compute paired differences (Tuesday − Monday) per metric and paired statistics across week-pairs: mean/median difference, paired t-test, Wilcoxon signed-rank (guarded for small n / zero variance).
- Outputs written under `C:/AITEMP/PhoOfflineEEGAnalysisOutputs/monday_tuesday_comparison/<date prefix>/` (parameterized):
  - `daily_aggregates.csv`, `monday_tuesday_pairs.csv` (long format: one row per pair × metric, with diff), `paired_stats.csv`.
  - Matplotlib PNG plots: paired Monday→Tuesday line plots per metric.
  - Single interactive HTML report (Panel/HoloViews/Bokeh): paired-line hvplots per metric + daily/pair/stats tables + unpaired-Monday listing.
- New runner script `run_monday_tuesday_comparison.py` at repo root (pattern of `run_discover_sessions.py`) with CLI args: `--summary-csv` (repeatable; default: newest CSV in the session_summaries folder), `--outputs-dir`, `--timezone`.
- Unit tests (pytest, synthetic data) for timezone-aware weekday assignment, dedup, duration-weighted aggregation, pairing with missing Tuesdays, and paired-statistics guards.

## Impact
- Affected specs: new capability `weekday-pair-comparison`
- Affected code: new `analysis/weekday_pair_comparison.py`, new `run_monday_tuesday_comparison.py`, new `tests/test_weekday_pair_comparison.py`; no changes to existing modules.
- Dependencies: only existing project deps (pandas, numpy, scipy, matplotlib, holoviews/hvplot/panel, zoneinfo via stdlib).
