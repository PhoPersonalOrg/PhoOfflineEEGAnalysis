## 1. Implementation
- [x] 1.1 Create `analysis/weekday_pair_comparison.py` with CSV loading, UTC→local conversion, and dedup (`load_session_metrics`)
- [x] 1.2 Implement duration-weighted daily aggregation (`aggregate_daily_metrics`)
- [x] 1.3 Implement Monday→next-day-Tuesday pairing with unpaired reporting (`build_monday_tuesday_pairs`)
- [x] 1.4 Implement paired differences + paired t-test / Wilcoxon stats with small-n guards (`compute_paired_statistics`)
- [x] 1.5 Implement exports: CSVs, Matplotlib paired-line PNGs, interactive Panel/HoloViews HTML report (`export_comparison_outputs`)
- [x] 1.6 Create `run_monday_tuesday_comparison.py` CLI runner (newest-CSV default, `--summary-csv`, `--outputs-dir`, `--timezone`)

## 2. Verification
- [x] 2.1 Unit tests: local-weekday assignment across UTC midnight boundary, dedup, weighted aggregation, missing-Tuesday pairing, stats guards
- [x] 2.2 Run pytest suite for the new tests (7 passed)
- [x] 2.3 Run the CLI on the real `2026-11-23_session_summaries.csv` and verify CSV/PNG/HTML outputs (51 sessions → 25 local days, 2 week-pairs, 5 unpaired Mondays, 21 PNGs, HTML report)
