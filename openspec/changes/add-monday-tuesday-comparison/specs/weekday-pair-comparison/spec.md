## ADDED Requirements

### Requirement: Local-Time Weekday Assignment
The system SHALL parse session `meas_date` values from session summary CSVs as UTC timestamps, convert them to a configurable IANA timezone (default `America/Los_Angeles`), and derive the local recording date and weekday used for all grouping.

#### Scenario: Evening session crossing UTC midnight
- **WHEN** a session has `meas_date` `2026-11-17 00:15:37` (UTC) and the timezone is `America/Los_Angeles`
- **THEN** the session is assigned local date `2026-11-16` and weekday Monday

#### Scenario: Sessions deduplicated across multiple CSVs
- **WHEN** two summary CSVs contain rows with the same `xdf_filename`
- **THEN** only one session row is kept, from the most recently exported CSV

### Requirement: Duration-Weighted Daily Aggregation
The system SHALL aggregate all numeric EEG metrics per local recording day using a duration-weighted mean (weights = `duration_s`, falling back to equal weights when durations are missing or zero), and SHALL report `n_sessions` and `total_duration_s` per day.

#### Scenario: Multiple sessions on one Monday
- **WHEN** a Monday has sessions of 1000 s (alpha_rel 0.2) and 3000 s (alpha_rel 0.4)
- **THEN** the daily alpha_rel aggregate is 0.35, n_sessions is 2, and total_duration_s is 4000

### Requirement: Monday-Tuesday Pairing
The system SHALL pair each Monday that has aggregated data with the Tuesday whose local date is exactly Monday date + 1 day, and SHALL explicitly report Mondays without a following Tuesday as unpaired rather than dropping them.

#### Scenario: Monday with following Tuesday
- **WHEN** local dates 2026-11-16 (Monday) and 2026-11-17 (Tuesday) both have data
- **THEN** one pair is produced containing both daily aggregates and per-metric differences (Tuesday − Monday)

#### Scenario: Monday without following Tuesday
- **WHEN** a Monday has data but the next calendar day has no sessions
- **THEN** the Monday appears in the unpaired-Monday report and contributes no pair to statistics

### Requirement: Paired Statistics
The system SHALL compute, per metric across all week-pairs, the mean and median paired difference (Tuesday − Monday), a paired t-test, and a Wilcoxon signed-rank test, guarding against insufficient sample size or zero variance by reporting NaN statistics instead of raising.

#### Scenario: Fewer than two pairs
- **WHEN** only one Monday-Tuesday pair exists for a metric
- **THEN** mean/median differences are reported and t-test/Wilcoxon p-values are NaN with the pair count recorded

#### Scenario: All-zero differences
- **WHEN** every paired difference for a metric is exactly zero
- **THEN** Wilcoxon p-value is reported as NaN without raising an exception

### Requirement: Comparison Outputs
The system SHALL export `daily_aggregates.csv`, `monday_tuesday_pairs.csv` (long format: one row per pair × metric including Monday value, Tuesday value, and difference), `paired_stats.csv`, Matplotlib PNG paired-line plots per metric, and a single interactive HTML report containing paired-line plots, tables, and the unpaired-Monday listing, all under a caller-specified output directory.

#### Scenario: Full export run
- **WHEN** the comparison runs on a summary CSV containing at least one Monday-Tuesday pair
- **THEN** all three CSVs, at least one PNG, and one HTML file exist in the output directory

### Requirement: CLI Runner
The system SHALL provide a `run_monday_tuesday_comparison.py` entry script accepting `--summary-csv` (repeatable), `--outputs-dir`, and `--timezone`, defaulting to the newest `*session_summaries.csv` in the configured session_summaries folder when no CSV is given.

#### Scenario: Default CSV discovery
- **WHEN** the runner is invoked without `--summary-csv`
- **THEN** it selects the most recently modified `*session_summaries.csv` in the session_summaries folder and prints the chosen path
