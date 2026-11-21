---
name: Add EEG Manifold Batch
overview: Wire the existing PhoPyMNEHelper EEG manifold (PCA/band-power) into the batch computation graph used by `main_analyze_run.py`, then export per-session scalar trend metrics into the session summaries CSV.
todos:
  - id: manifold-node
    content: Add EEGManifoldComputation SpecificComputationBase wrapping compute_eeg_manifold_from_detailed_df from Raw
    status: completed
  - id: register-goals
    content: Register node in eeg_registry and append eeg_manifold to EEG_COMPUTATION_IDS_ORDERED
    status: completed
  - id: summary-csv
    content: Extend compute_session_summary_metrics with PC std/range/trajectory scalars
    status: completed
  - id: unit-test
    content: Add synthetic RawArray unit test for the new computation node
    status: completed
isProject: false
---

# Add EEG Manifold to Batch Analysis

## Goal
Make `main_analyze_run.py` compute the existing `eeg_manifold` measure for every processed session (with disk cache), and add comparable per-session scalars to `session_summaries.csv` for historical trend plots.

## Approach
Reuse [`eeg_manifold.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\src\phopymnehelper\analysis\computations\eeg_manifold.py) as-is. Add a DAG node mirroring [`EEGSpectrogramComputation`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\src\phopymnehelper\analysis\computations\specific\EEG_Spectograms.py), register it, and include it in the default goal list that `process_XDFs_main` already runs via `run_eeg_graph_legacy_ordered`.

**Batch defaults (locked):** `method="pca"`, `feature_mode="band_power"`, `n_components=3`, `live_mode=False`. PCA is required for cross-session trend comparability (UMAP embeddings are not session-aligned).

```mermaid
flowchart LR
  xdf[XDF load merge] --> graph[run_eeg_graph_legacy_ordered]
  graph --> bad[time_independent_bad_channels]
  bad --> manifold[eeg_manifold node]
  graph --> spect[spectogram]
  manifold --> summary[compute_session_summary_metrics CSV]
  spect --> summary
```

## 1. New computation node in PhoPyMNEHelper

Add [`specific/eeg_manifold_computation.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\src\phopymnehelper\analysis\computations\specific\eeg_manifold_computation.py):

- `computation_id = "eeg_manifold"`, `version = "1"`, `deps = ("time_independent_bad_channels",)`
- `compute(ctx, params, dep_outputs)`:
  - Build `eeg_df` from `ctx.raw`: column `t` = `raw.times`, plus EEG channel columns from `raw.get_data(picks="eeg")`
  - Set `EEGManifoldConfig.session_bad_channels` from `dep_outputs["time_independent_bad_channels"]["all_bad_channels"]` when present
  - Call `compute_eeg_manifold_from_detailed_df(..., quality_df=None, config=cfg)` (batch graph has no Mode7 CQ today)
  - Return a cache-safe dict: `{times, embedding (n_win x n_comp), component_names, config_dict, n_windows}`

Fingerprint config params (`n_components`, `method`, `feature_mode`, window/hop/fit_history, `min_quality_to_keep`, etc.) like spectrogram/FMT nodes.

## 2. Register + run in default batch goals

Update [`eeg_registry.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\src\phopymnehelper\analysis\computations\eeg_registry.py):

- Import and `_register_node_if_absent(..., EEGManifoldComputation().to_computation_node())`
- Append `"eeg_manifold"` to `EEG_COMPUTATION_IDS_ORDERED` so [`main_analyze_run.py` line 1119](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\main_analyze_run.py) actually executes it (registered-but-not-ordered nodes like FMT currently do not run on this path)

Also export the class from [`computations/__init__.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\src\phopymnehelper\analysis\computations\__init__.py) if other nodes are exported there.

## 3. Session summary CSV columns

Extend [`compute_session_summary_metrics`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\main_analyze_run.py) to read `a_result["eeg_manifold"]` when present and add:

- `manifold_n_windows`
- `manifold_pc1_std`, `manifold_pc2_std`, `manifold_pc3_std`
- `manifold_pc1_range`, `manifold_pc2_range`, `manifold_pc3_range`
- `manifold_traj_path_length` (sum of Euclidean steps in PC space)
- `manifold_traj_speed_rms` (RMS of step length / Δt)

These are the longitudinal trend features; full embedding stays in the node cache / result dict.

## 4. Test

Add a small unit test next to [`tests/test_eeg_manifold.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoPyMNEHelper\tests\test_eeg_manifold.py) that builds a tiny synthetic `mne.io.RawArray`, runs `EEGManifoldComputation.compute` (or the node via registry), and asserts `PC*` shape / non-empty `times`.

## Out of scope
- Timeline auto-add / UI changes (already exist in pyPhoTimeline)
- UMAP in batch
- Wiring Mode7 quality into the batch graph
- Changing `__main__` session count (`n_most_recent_sessions_to_preprocess`)
