## Context
Per-session EEG summary metrics already exist as CSVs produced by `SpectogramPlottingHelper.compute_session_summary_metrics` (`main_analyze_run.py`). Timestamps are UTC; the user records in America/Los_Angeles. Multiple sessions per day are common; some Mondays have no following Tuesday recording.

## Goals / Non-Goals
- Goals: weekday-correct daily aggregates, explicit Mon→Tue pairing, paired statistics, CSV/PNG/HTML outputs from existing summary CSVs.
- Non-Goals: recomputing EEG metrics from XDF, per-channel analysis, other weekday pairs, GUI integration.

## Decisions
- Decision: operate on summary CSVs (fast, metrics already computed). Alternative: recompute via EEG graph — rejected for speed; can be added later behind the same aggregation API.
- Decision: duration-weighted daily mean (longer sessions contribute more). Alternative: equal-weight session mean — rejected because short test recordings (e.g. 223 s) would skew days.
- Decision: pair Monday date with Monday+1 Tuesday only (strict adjacent-day pairing). Alternative: nearest-timestamp session pairing — rejected by user choice.
- Decision: scipy `ttest_rel` + `wilcoxon` with guards (n≥2 for t-test; n≥1 nonzero diffs for Wilcoxon; NaN otherwise). No multiple-comparison correction in v1; pair counts reported so results can be interpreted cautiously.
- Decision: single self-contained HTML via Panel with HoloViews paired-line plots and DataFrame tables; Matplotlib PNGs for static per-metric plots.
- Decision: timezone via stdlib `zoneinfo` (Python ≥3.9 constraint satisfied); `tzdata` fallback handled by pandas/pytz already in env if zoneinfo db missing on Windows — use `zoneinfo.ZoneInfo` and surface a clear error if unavailable.

## Risks / Trade-offs
- Small number of week-pairs (≈4–6 in current data) → low statistical power; report n_pairs prominently.
- UTC-parsed `meas_date` strings are naive in CSV → explicitly localize to UTC before conversion (matches how catalog stores UTC).
- Metrics with NaN in some sessions (e.g. manifold scalars) → weighted mean skips NaN weights/values per metric.

## Open Questions
- None blocking; multiple-comparison correction and other weekday pairs deferred.
