# Standard library imports
import gc
import hashlib
import json
import os
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple, Optional, Any, Set

# Third-party imports
import h5py
import numpy as np
import pandas as pd
import xarray as xr
from attrs import define, field

# MNE imports
import mne
from mne import set_log_level

# Visualization imports
import holoviews as hv
import hvplot.xarray
import panel as pn

# IPython imports

# Project-specific imports
from phopymnehelper.MNE_helpers import MNEHelpers
from phopymnehelper.EEG_data import EEGComputations, EEGData
from phopymnehelper.analysis.computations.cache import DiskComputationCache
from phopymnehelper.analysis.computations.eeg_registry import EEGComputationRegistryHelper
from phopymnehelper.SavedSessionsProcessor import SavedSessionsProcessor, DataModalityType
from phopymnehelper.xdf_files import LabRecorderXDF, XDFDataStreamAccessor

from phoofflineeeganalysis.analysis.session_catalog import SessionCatalog, get_or_create_catalog

COMPUTATION_HISTORY_COLUMNS = ["cache_key_hex", "xdf_path", "xdf_mtime", "params_json", "result_path", "fif_filename", "computed_at"]


def _reset_stream_infos_index_for_groupby(stream_infos_df: pd.DataFrame) -> pd.DataFrame:
	"""reset_index without clash when xdf_dataset_idx is both the index name and a column."""
	sidf = stream_infos_df
	if sidf.index.name == 'xdf_dataset_idx' and 'xdf_dataset_idx' in sidf.columns:
		sidf = sidf.drop(columns=['xdf_dataset_idx'])
	return sidf.reset_index()
# Computation cache handling is managed via phopymnehelper.analysis.computations.DiskComputationCache and BatchSessionExecutor.





def _get_channel_bad_intervals(raw: mne.io.BaseRaw, channel_names: List[str]) -> Dict[str, List[Tuple[float, float]]]:
	"""Build per-channel bad (onset, end) intervals in seconds from raw.annotations (BAD_* descriptions, optional ch_names)."""
	out: Dict[str, List[Tuple[float, float]]] = {ch: [] for ch in channel_names}
	if raw.annotations is None or len(raw.annotations) == 0:
		return out
	ch_names_attr = getattr(raw.annotations, "ch_names", None)
	for i in range(len(raw.annotations)):
		desc = raw.annotations.description[i]
		if not (isinstance(desc, str) and desc.upper().startswith("BAD_")):
			continue
		onset = float(raw.annotations.onset[i])
		duration = float(raw.annotations.duration[i])
		end = onset + duration
		seg_ch_names = ch_names_attr[i] if (ch_names_attr is not None and i < len(ch_names_attr)) else None
		if not isinstance(seg_ch_names, (list, tuple)) or len(seg_ch_names) == 0:
			for ch in channel_names:
				out[ch].append((onset, end))
		else:
			for ch in seg_ch_names:
				if ch in out:
					out[ch].append((onset, end))
	return out


class SpectogramPlottingHelper:
	@classmethod
	def _style_spectrogram_bokeh_plots(cls, plot_handle):
		"""Recursively style Bokeh plot(s): title white box with black border, light grey/blue background, subtle y-grid."""
		if hasattr(plot_handle, 'title') and hasattr(plot_handle.title, 'background_fill_color'):
			plot_handle.title.background_fill_color = 'white'
			plot_handle.title.border_line_color = 'black'
			plot_handle.title.border_line_width = 1
		if hasattr(plot_handle, 'background_fill_color'):
			plot_handle.background_fill_color = '#e8eef2'
		if hasattr(plot_handle, 'ygrid') and hasattr(plot_handle.ygrid, 'grid_line_color'):
			plot_handle.ygrid.grid_line_color = '#c0c8d0'
			plot_handle.ygrid.grid_line_alpha = 0.8
		if hasattr(plot_handle, 'children'):
			for child in plot_handle.children:
				if child is not None:
					cls._style_spectrogram_bokeh_plots(child)


	@classmethod
	def export_session_spectrograms_html(cls, active_only_out_eeg_raws, results, output_folder: Path, freq_min: float = 1.0, freq_max: float = 40.0, filename_prefix: str = "", session_label: Optional[str] = None):
		"""
		Export interactive HTML spectrograms for each EEG session using HoloViews.

		Creates individual HTML files for each session with:
		- Interactive spectrogram heatmaps for each channel
		- Zoomable/pannable time and frequency axes
		- Hover tooltips showing exact values
		- Session title in format YYYY-MM-DD/HH-MM-SS or YYYY-MM-DD/HH-MM-SS - session_label

		Args:
			active_only_out_eeg_raws: List of mne.io.Raw EEG sessions
			results: List of computation results containing spectrogram data
			output_folder: Path to save HTML files
			freq_min: Minimum frequency to display (Hz)
			freq_max: Maximum frequency to display (Hz)
			filename_prefix: Optional prefix for output filenames
			session_label: Optional label appended to session title (e.g. "cog_bad")

		Returns:
			List of Path objects for created HTML files

		Usage:
			html_files = export_session_spectrograms_html(
				active_only_out_eeg_raws, results,
				outputs_root_folder / "spectrograms_html",
				session_label="cog_bad"
			)
		"""
		output_folder = Path(output_folder)
		output_folder.mkdir(parents=True, exist_ok=True)

		html_files = []

		for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
			try:
				if a_result is None or a_result.get('spectogram') is None:
					continue
				# Get session metadata
				meas_date = a_raw.info.get('meas_date')
				if meas_date:
					session_name = meas_date.strftime('%Y-%m-%d_%H-%M-%S')
					session_title_display = meas_date.strftime('%Y-%m-%d/%H-%M-%S')
					if session_label:
						session_title_display = f"{session_title_display} - {session_label}"
				else:
					session_name = f"session_{idx:03d}"
					session_title_display = f"Session {idx}" if not session_label else f"Session {idx} - {session_label}"

				# Extract spectrogram data
				spectogram_result_dict = a_result['spectogram']['spectogram_result_dict']
				fs = a_result['spectogram']['fs']
				channel_bad_intervals = _get_channel_bad_intervals(a_raw, list(spectogram_result_dict.keys()))

				def _spectrogram_style_hook(hv_plot, _element):
					bokeh_root = hv_plot.handles.get('plot')
					if bokeh_root is not None:
						cls._style_spectrogram_bokeh_plots(bokeh_root)

				# Create HoloViews plots for each channel
				channel_plots = []

				for ch_idx, (ch_name, (f, t, Sxx)) in enumerate(spectogram_result_dict.items()):
					# Convert to dB scale
					Sxx_db = 10 * np.log10(Sxx + 1e-12)

					# Filter frequency range
					freq_mask = (f >= freq_min) & (f <= freq_max)
					f_filtered = f[freq_mask]
					Sxx_filtered = Sxx_db[freq_mask, :]

					# Create xarray DataArray for easier plotting
					da = xr.DataArray(
						Sxx_filtered,
						coords={'frequency': f_filtered, 'time': t},
						dims=['frequency', 'time'],
						name=f'{ch_name}_power'
					)

					# Create HoloViews image with hvplot (match preferred appearance: no colorbar, no x-label, session title)
					img = da.hvplot.image(
						x='time', y='frequency',
						cmap='viridis',
						clim=(Sxx_filtered.min(), Sxx_filtered.max()),
						title=session_title_display,
						xlabel='',
						ylabel='Frequency (Hz)',
						width=900,
						height=150,
						colorbar=False,
						tools=['hover', 'pan', 'wheel_zoom', 'box_zoom', 'reset'],
						hover_tooltips=[('Time', '@time{0.2f}s'),
									   ('Freq', '@frequency{0.1f}Hz'),
									   ('Power', '@image{0.2f}dB')]
					)

					bad_intervals = channel_bad_intervals.get(ch_name, [])
					t_min, t_max = float(np.min(t)), float(np.max(t))
					f_min, f_max = float(f_filtered.min()), float(f_filtered.max())
					if bad_intervals:
						rect_data = []
						for start, end in bad_intervals:
							start_c = max(start, t_min)
							end_c = min(end, t_max)
							if start_c < end_c:
								rect_data.append((start_c, f_min, end_c, f_max))
						if rect_data:
							rects = hv.Rectangles(rect_data, kdims=['time', 'frequency', 'time', 'frequency']).opts(alpha=0.35, color='red', line_alpha=0)
							channel_plots.append((img * rects).opts(hooks=[_spectrogram_style_hook]))
						else:
							channel_plots.append(img.opts(hooks=[_spectrogram_style_hook]))
					else:
						channel_plots.append(img.opts(hooks=[_spectrogram_style_hook]))

				# Stack all channel plots vertically (hooks on Layout are not supported in recent HoloViews)
				layout = hv.Layout(channel_plots).cols(1).opts(title=session_title_display, shared_axes=True)

				# Save to HTML
				html_path = output_folder / f"{filename_prefix}spectrogram_{session_name}.html"
				hv.save(layout, html_path, backend='bokeh')
				html_files.append(html_path)

				print(f'  Saved spectrogram HTML for session {idx+1}/{len(active_only_out_eeg_raws)}: {html_path.name}')

			except Exception as e:
				print(f'  ERROR exporting session {idx}: {e}')
				continue

		print(f'\nExported {len(html_files)} spectrogram HTML files to: {output_folder}')
		return html_files


	@classmethod
	def export_combined_spectrograms_html(cls, active_only_out_eeg_raws, results, output_path: Path, freq_min: float = 1.0, freq_max: float = 40.0, max_sessions_per_page: int = 10):
		"""
		Export a single HTML file with all session spectrograms in a scrollable layout.

		Creates one HTML file with:
		- All sessions stacked vertically
		- Session selector dropdown
		- Synchronized time axes across channels
		- Compact view for comparison

		Args:
			active_only_out_eeg_raws: List of mne.io.Raw EEG sessions
			results: List of computation results
			output_path: Path for the output HTML file
			freq_min: Minimum frequency (Hz)
			freq_max: Maximum frequency (Hz)
			max_sessions_per_page: Maximum sessions to include (for performance)

		Returns:
			Path to created HTML file

		Usage:
			html_file = export_combined_spectrograms_html(
				active_only_out_eeg_raws, results,
				outputs_root_folder / "all_spectrograms.html"
			)
		"""
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)

		# Limit sessions for performance
		n_sessions = min(len(active_only_out_eeg_raws), max_sessions_per_page)

		all_session_layouts = []

		for idx in range(n_sessions):
			a_raw = active_only_out_eeg_raws[idx]
			a_result = results[idx]

			try:
				# Get session metadata
				meas_date = a_raw.info.get('meas_date')
				if meas_date:
					session_title = meas_date.strftime('%Y-%m-%d %H:%M:%S')
				else:
					session_title = f"Session {idx}"

				# Extract spectrogram data
				spectogram_result_dict = a_result['spectogram']['spectogram_result_dict']
				channel_bad_intervals = _get_channel_bad_intervals(a_raw, list(spectogram_result_dict.keys()))

				# Create compact channel plots
				channel_plots = []

				for ch_name, (f, t, Sxx) in spectogram_result_dict.items():
					Sxx_db = 10 * np.log10(Sxx + 1e-12)

					# Filter frequency
					freq_mask = (f >= freq_min) & (f <= freq_max)
					f_filtered = f[freq_mask]
					Sxx_filtered = Sxx_db[freq_mask, :]

					# Create compact plot
					da = xr.DataArray(
						Sxx_filtered,
						coords={'frequency': f_filtered, 'time': t},
						dims=['frequency', 'time']
					)

					img = da.hvplot.image(
						x='time', y='frequency',
						cmap='viridis',
						title=f'{ch_name}',
						xlabel='',
						ylabel='Hz',
						width=800,
						height=80,
						colorbar=False,
						tools=['hover', 'pan', 'wheel_zoom', 'reset']
					)

					bad_intervals = channel_bad_intervals.get(ch_name, [])
					t_min, t_max = float(np.min(t)), float(np.max(t))
					f_min, f_max = float(f_filtered.min()), float(f_filtered.max())
					if bad_intervals:
						rect_data = []
						for start, end in bad_intervals:
							start_c = max(start, t_min)
							end_c = min(end, t_max)
							if start_c < end_c:
								rect_data.append((start_c, f_min, end_c, f_max))
						if rect_data:
							rects = hv.Rectangles(rect_data, kdims=['time', 'frequency', 'time', 'frequency']).opts(alpha=0.35, color='red', line_alpha=0)
							channel_plots.append(img * rects)
						else:
							channel_plots.append(img)
					else:
						channel_plots.append(img)

				# Create session layout
				session_layout = hv.Layout(channel_plots).cols(1).opts(
					title=f'{session_title}'
				)

				all_session_layouts.append(session_layout)

			except Exception as e:
				print(f'  ERROR processing session {idx}: {e}')
				continue

		# Combine all sessions
		combined = hv.Layout(all_session_layouts).cols(1)

		# Save to HTML
		hv.save(combined, output_path, backend='bokeh')

		print(f'\nExported combined spectrogram HTML with {len(all_session_layouts)} sessions to: {output_path}')
		return output_path


	@classmethod
	def _safe_meas_date_sec(cls, raw: Optional[mne.io.BaseRaw]) -> float:
		if raw is None:
			return float("nan")
		meas_date = raw.info.get("meas_date")
		if meas_date is None:
			return float("nan")
		if getattr(meas_date, "tzinfo", None) is None:
			meas_date = meas_date.replace(tzinfo=timezone.utc)
		return float(meas_date.timestamp())


	@classmethod
	def _extract_raw_export_payload(cls, raw: Optional[mne.io.BaseRaw]) -> Dict[str, Any]:
		if raw is None:
			return {"has_data": False, "raw_data": np.empty((0, 0), dtype=np.float64), "raw_times_sec": np.empty((0,), dtype=np.float64), "channel_names": [], "sfreq_hz": float("nan"), "time_origin_meas_date_sec": float("nan")}
		try:
			raw_data = np.asarray(raw.get_data(), dtype=np.float64)
			raw_times_sec = np.asarray(raw.times, dtype=np.float64)
			channel_names = [str(v) for v in raw.info.get("ch_names", [])]
			sfreq_hz = float(raw.info.get("sfreq", float("nan")))
			return {"has_data": True, "raw_data": raw_data, "raw_times_sec": raw_times_sec, "channel_names": channel_names, "sfreq_hz": sfreq_hz, "time_origin_meas_date_sec": cls._safe_meas_date_sec(raw)}
		except Exception:
			return {"has_data": False, "raw_data": np.empty((0, 0), dtype=np.float64), "raw_times_sec": np.empty((0,), dtype=np.float64), "channel_names": [], "sfreq_hz": float("nan"), "time_origin_meas_date_sec": float("nan")}


	@classmethod
	def _compute_nearest_spectrogram_bin_idx(cls, sample_times_sec: np.ndarray, spectrogram_times_sec: np.ndarray, max_samples_for_map: int = 2_000_000) -> Tuple[np.ndarray, bool]:
		sample_times_sec = np.asarray(sample_times_sec, dtype=np.float64)
		spectrogram_times_sec = np.asarray(spectrogram_times_sec, dtype=np.float64)
		if sample_times_sec.size == 0:
			return np.empty((0,), dtype=np.int64), True
		if spectrogram_times_sec.size == 0:
			return np.full(sample_times_sec.shape, -1, dtype=np.int64), True
		if sample_times_sec.size > max_samples_for_map:
			return np.empty((0,), dtype=np.int64), False
		right_idx = np.searchsorted(spectrogram_times_sec, sample_times_sec, side="left")
		right_idx = np.clip(right_idx, 0, spectrogram_times_sec.size - 1)
		left_idx = np.clip(right_idx - 1, 0, spectrogram_times_sec.size - 1)
		left_dist = np.abs(sample_times_sec - spectrogram_times_sec[left_idx])
		right_dist = np.abs(sample_times_sec - spectrogram_times_sec[right_idx])
		nearest = np.where(right_dist < left_dist, right_idx, left_idx).astype(np.int64)
		return nearest, True


	@classmethod
	def _build_alignment_export_payload(cls, spectrogram_times_sec: np.ndarray, eeg_payload: Dict[str, Any], motion_payload: Dict[str, Any]) -> Dict[str, Any]:
		spectrogram_times_sec = np.asarray(spectrogram_times_sec, dtype=np.float64)
		eeg_nearest_idx, eeg_has_nearest_map = cls._compute_nearest_spectrogram_bin_idx(eeg_payload["raw_times_sec"], spectrogram_times_sec)
		motion_nearest_idx, motion_has_nearest_map = cls._compute_nearest_spectrogram_bin_idx(motion_payload["raw_times_sec"], spectrogram_times_sec)
		if spectrogram_times_sec.size > 0:
			spectrogram_start_time_sec = float(spectrogram_times_sec[0])
			spectrogram_end_time_sec = float(spectrogram_times_sec[-1])
		else:
			spectrogram_start_time_sec = float("nan")
			spectrogram_end_time_sec = float("nan")
		return {"alignment_method": "raw_plus_map", "spectrogram_times_sec": spectrogram_times_sec, "spectrogram_start_time_sec": spectrogram_start_time_sec, "spectrogram_end_time_sec": spectrogram_end_time_sec, "eeg_time_origin_meas_date_sec": float(eeg_payload["time_origin_meas_date_sec"]), "motion_time_origin_meas_date_sec": float(motion_payload["time_origin_meas_date_sec"]), "eeg_nearest_spectrogram_bin_idx": eeg_nearest_idx, "motion_nearest_spectrogram_bin_idx": motion_nearest_idx, "eeg_has_nearest_map": bool(eeg_has_nearest_map), "motion_has_nearest_map": bool(motion_has_nearest_map)}


	@classmethod
	def _unpack_spectrogram_stack(cls, a_result: Dict[str, Any], freq_min: float = 1.0, freq_max: float = 40.0) -> Optional[Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]]:
		"""Return (channel_names, freqs, times, Sxx_stack) for [freq_min, freq_max], or None if missing."""
		if a_result is None or "spectogram" not in a_result:
			return None
		spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
		channel_names = list(spectogram_result_dict.keys())
		if len(channel_names) == 0:
			return None
		f0, t0, _Sxx0 = next(iter(spectogram_result_dict.values()))
		freq_mask = (f0 >= freq_min) & (f0 <= freq_max)
		freqs = np.asarray(f0)[freq_mask]
		times = np.asarray(t0)
		Sxx_list = []
		for ch_name in channel_names:
			_f, _t, Sxx = spectogram_result_dict[ch_name]
			Sxx_list.append(np.asarray(Sxx)[freq_mask, :])
		## END for ch_name in channel_names...
		return channel_names, freqs, times, np.stack(Sxx_list, axis=0)


	@classmethod
	def write_one_session_hdf5(cls, sessions_grp: h5py.Group, session_key: str, a_raw: mne.io.BaseRaw, a_result: Dict[str, Any], motion_raw: Optional[mne.io.BaseRaw] = None, xdf_filename: Optional[str] = None, freq_min: float = 1.0, freq_max: float = 40.0) -> bool:
		"""Write one session under sessions_grp[session_key]. Returns True on success."""
		unpacked = cls._unpack_spectrogram_stack(a_result, freq_min=freq_min, freq_max=freq_max)
		if unpacked is None:
			return False
		channel_names, freqs, times, Sxx_stack = unpacked
		meas_date = a_raw.info.get("meas_date")
		if meas_date is None:
			meas_date_sec = float("nan")
			meas_date_iso = ""
		else:
			if getattr(meas_date, "tzinfo", None) is None:
				meas_date = meas_date.replace(tzinfo=timezone.utc)
			meas_date_sec = meas_date.timestamp()
			meas_date_iso = meas_date.isoformat()
		eeg_payload = cls._extract_raw_export_payload(a_raw)
		motion_payload = cls._extract_raw_export_payload(motion_raw)
		alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
		try:
			duration_s = float(a_raw.times[-1] - a_raw.times[0]) if a_raw.times.size > 0 else float("nan")
		except Exception:
			duration_s = float("nan")
		sfreq_hz = float(a_raw.info.get("sfreq", float("nan")))
		sgrp = sessions_grp.create_group(session_key)
		sgrp.create_dataset("freqs", data=freqs, dtype=np.float64)
		sgrp.create_dataset("times", data=times, dtype=np.float64)
		dt_str = h5py.special_dtype(vlen=str)
		ch_dset = sgrp.create_dataset("channel_names", (len(channel_names),), dtype=dt_str)
		ch_dset[:] = channel_names
		sgrp.create_dataset("spectrogram", data=Sxx_stack, dtype=np.float64)
		eeg_grp = sgrp.create_group("eeg")
		eeg_grp.create_dataset("raw_data", data=eeg_payload["raw_data"], dtype=np.float64)
		eeg_grp.create_dataset("raw_times_sec", data=eeg_payload["raw_times_sec"], dtype=np.float64)
		eeg_ch_dset = eeg_grp.create_dataset("channel_names", (len(eeg_payload["channel_names"]),), dtype=dt_str)
		if len(eeg_payload["channel_names"]) > 0:
			eeg_ch_dset[:] = eeg_payload["channel_names"]
		eeg_grp.attrs["sfreq_hz"] = float(eeg_payload["sfreq_hz"])
		eeg_grp.attrs["time_origin_meas_date_sec"] = float(eeg_payload["time_origin_meas_date_sec"])
		motion_grp = sgrp.create_group("motion")
		motion_grp.attrs["has_motion"] = bool(motion_payload["has_data"])
		motion_grp.create_dataset("raw_data", data=motion_payload["raw_data"], dtype=np.float64)
		motion_grp.create_dataset("raw_times_sec", data=motion_payload["raw_times_sec"], dtype=np.float64)
		motion_ch_dset = motion_grp.create_dataset("channel_names", (len(motion_payload["channel_names"]),), dtype=dt_str)
		if len(motion_payload["channel_names"]) > 0:
			motion_ch_dset[:] = motion_payload["channel_names"]
		motion_grp.attrs["sfreq_hz"] = float(motion_payload["sfreq_hz"])
		motion_grp.attrs["time_origin_meas_date_sec"] = float(motion_payload["time_origin_meas_date_sec"])
		alignment_grp = sgrp.create_group("alignment")
		alignment_grp.attrs["alignment_method"] = alignment_payload["alignment_method"]
		alignment_grp.attrs["eeg_has_nearest_map"] = bool(alignment_payload["eeg_has_nearest_map"])
		alignment_grp.attrs["motion_has_nearest_map"] = bool(alignment_payload["motion_has_nearest_map"])
		alignment_grp.attrs["spectrogram_start_time_sec"] = float(alignment_payload["spectrogram_start_time_sec"])
		alignment_grp.attrs["spectrogram_end_time_sec"] = float(alignment_payload["spectrogram_end_time_sec"])
		alignment_grp.create_dataset("spectrogram_times_sec", data=alignment_payload["spectrogram_times_sec"], dtype=np.float64)
		alignment_grp.create_dataset("eeg_nearest_spectrogram_bin_idx", data=alignment_payload["eeg_nearest_spectrogram_bin_idx"], dtype=np.int64)
		alignment_grp.create_dataset("motion_nearest_spectrogram_bin_idx", data=alignment_payload["motion_nearest_spectrogram_bin_idx"], dtype=np.int64)
		sgrp.attrs["meas_date_iso"] = meas_date_iso
		sgrp.attrs["meas_date_sec"] = meas_date_sec
		sgrp.attrs["sfreq_hz"] = sfreq_hz
		sgrp.attrs["duration_s"] = duration_s
		sgrp.attrs["n_channels"] = len(channel_names)
		sgrp.attrs["has_motion"] = bool(motion_payload["has_data"])
		if xdf_filename is not None:
			sgrp.attrs["xdf_filename"] = str(xdf_filename)
		return True


	@classmethod
	def export_one_session_spectrogram_html(cls, idx: int, a_raw: mne.io.BaseRaw, a_result: Dict[str, Any], output_folder: Path, freq_min: float = 1.0, freq_max: float = 40.0, filename_prefix: str = "", session_label: Optional[str] = None, n_sessions_hint: Optional[int] = None) -> Optional[Path]:
		"""Export one session's interactive HTML spectrogram. Returns path or None."""
		if a_result is None or a_result.get("spectogram") is None:
			return None
		output_folder = Path(output_folder)
		output_folder.mkdir(parents=True, exist_ok=True)
		meas_date = a_raw.info.get("meas_date")
		if meas_date:
			session_name = meas_date.strftime("%Y-%m-%d_%H-%M-%S")
			session_title_display = meas_date.strftime("%Y-%m-%d/%H-%M-%S")
			if session_label:
				session_title_display = f"{session_title_display} - {session_label}"
		else:
			session_name = f"session_{idx:03d}"
			session_title_display = f"Session {idx}" if not session_label else f"Session {idx} - {session_label}"
		spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
		channel_bad_intervals = _get_channel_bad_intervals(a_raw, list(spectogram_result_dict.keys()))

		def _spectrogram_style_hook(hv_plot, _element):
			bokeh_root = hv_plot.handles.get("plot")
			if bokeh_root is not None:
				cls._style_spectrogram_bokeh_plots(bokeh_root)

		channel_plots = []
		for ch_name, (f, t, Sxx) in spectogram_result_dict.items():
			Sxx_db = 10 * np.log10(Sxx + 1e-12)
			freq_mask = (f >= freq_min) & (f <= freq_max)
			f_filtered = f[freq_mask]
			Sxx_filtered = Sxx_db[freq_mask, :]
			da = xr.DataArray(Sxx_filtered, coords={"frequency": f_filtered, "time": t}, dims=["frequency", "time"], name=f"{ch_name}_power")
			img = da.hvplot.image(x="time", y="frequency", cmap="viridis", clim=(Sxx_filtered.min(), Sxx_filtered.max()), title=session_title_display, xlabel="", ylabel="Frequency (Hz)", width=900, height=150, colorbar=False, tools=["hover", "pan", "wheel_zoom", "box_zoom", "reset"], hover_tooltips=[("Time", "@time{0.2f}s"), ("Freq", "@frequency{0.1f}Hz"), ("Power", "@image{0.2f}dB")])
			bad_intervals = channel_bad_intervals.get(ch_name, [])
			t_min, t_max = float(np.min(t)), float(np.max(t))
			f_min, f_max = float(f_filtered.min()), float(f_filtered.max())
			if bad_intervals:
				rect_data = []
				for start, end in bad_intervals:
					start_c = max(start, t_min)
					end_c = min(end, t_max)
					if start_c < end_c:
						rect_data.append((start_c, f_min, end_c, f_max))
				## END for start, end in bad_intervals...
				if rect_data:
					rects = hv.Rectangles(rect_data, kdims=["time", "frequency", "time", "frequency"]).opts(alpha=0.35, color="red", line_alpha=0)
					channel_plots.append((img * rects).opts(hooks=[_spectrogram_style_hook]))
				else:
					channel_plots.append(img.opts(hooks=[_spectrogram_style_hook]))
			else:
				channel_plots.append(img.opts(hooks=[_spectrogram_style_hook]))
		## END for ch_name, (f, t, Sxx) in spectogram_result_dict.items()...
		layout = hv.Layout(channel_plots).cols(1).opts(title=session_title_display, shared_axes=True)
		html_path = output_folder / f"{filename_prefix}spectrogram_{session_name}.html"
		hv.save(layout, html_path, backend="bokeh")
		hint = f"{idx+1}/{n_sessions_hint}" if n_sessions_hint is not None else f"{idx+1}"
		print(f"  Saved spectrogram HTML for session {hint}: {html_path.name}")
		return html_path


	@classmethod
	def export_one_session_spectrogram_npz(cls, idx: int, a_raw: mne.io.BaseRaw, a_result: Dict[str, Any], output_dir: Path, freq_min: float = 1.0, freq_max: float = 40.0, filename_prefix: str = "", motion_raw: Optional[mne.io.BaseRaw] = None, used_session_ids: Optional[Set[str]] = None) -> Optional[Path]:
		"""Export one session spectrogram .npz for Rerun. Returns path or None."""
		unpacked = cls._unpack_spectrogram_stack(a_result, freq_min=freq_min, freq_max=freq_max)
		if unpacked is None:
			return None
		channel_names_list, freqs, times, Sxx_stack = unpacked
		output_dir = Path(output_dir)
		output_dir.mkdir(parents=True, exist_ok=True)
		if used_session_ids is None:
			used_session_ids = set()
		meas_date = a_raw.info.get("meas_date")
		if meas_date is None:
			meas_date_sec = float("nan")
			session_id = f"session_{idx:03d}"
		else:
			if getattr(meas_date, "tzinfo", None) is None:
				meas_date = meas_date.replace(tzinfo=timezone.utc)
			meas_date_sec = meas_date.timestamp()
			session_id = meas_date.strftime("%Y-%m-%dT%H-%M-%S")
		base_id = session_id
		while session_id in used_session_ids:
			suffix = sum(1 for s in used_session_ids if s == base_id or (isinstance(s, str) and s.startswith(base_id + "_")))
			session_id = f"{base_id}_{suffix}"
		## END while session_id in used_session_ids...
		used_session_ids.add(session_id)
		channel_names = np.array(channel_names_list, dtype=object)
		eeg_payload = cls._extract_raw_export_payload(a_raw)
		motion_payload = cls._extract_raw_export_payload(motion_raw)
		alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
		export_dict = {"freq_min": np.array(freq_min), "freq_max": np.array(freq_max), "session_indices": np.array([0])}
		export_dict["s0_meas_date_sec"] = np.array(meas_date_sec)
		export_dict["s0_channel_names"] = channel_names
		export_dict["s0_freqs"] = freqs
		export_dict["s0_times"] = times
		export_dict["s0_Sxx"] = Sxx_stack
		export_dict["s0_eeg_raw_data"] = eeg_payload["raw_data"]
		export_dict["s0_eeg_raw_times_sec"] = eeg_payload["raw_times_sec"]
		export_dict["s0_eeg_channel_names"] = np.array(eeg_payload["channel_names"], dtype=object)
		export_dict["s0_has_motion"] = np.array(bool(motion_payload["has_data"]))
		export_dict["s0_motion_raw_data"] = motion_payload["raw_data"]
		export_dict["s0_motion_raw_times_sec"] = motion_payload["raw_times_sec"]
		export_dict["s0_motion_channel_names"] = np.array(motion_payload["channel_names"], dtype=object)
		export_dict["s0_alignment_method"] = np.array(alignment_payload["alignment_method"], dtype=object)
		export_dict["s0_spectrogram_times_sec"] = alignment_payload["spectrogram_times_sec"]
		export_dict["s0_spectrogram_start_time_sec"] = np.array(alignment_payload["spectrogram_start_time_sec"])
		export_dict["s0_spectrogram_end_time_sec"] = np.array(alignment_payload["spectrogram_end_time_sec"])
		export_dict["s0_eeg_time_origin_meas_date_sec"] = np.array(alignment_payload["eeg_time_origin_meas_date_sec"])
		export_dict["s0_motion_time_origin_meas_date_sec"] = np.array(alignment_payload["motion_time_origin_meas_date_sec"])
		export_dict["s0_eeg_has_nearest_map"] = np.array(alignment_payload["eeg_has_nearest_map"])
		export_dict["s0_motion_has_nearest_map"] = np.array(alignment_payload["motion_has_nearest_map"])
		export_dict["s0_eeg_nearest_spectrogram_bin_idx"] = alignment_payload["eeg_nearest_spectrogram_bin_idx"]
		export_dict["s0_motion_nearest_spectrogram_bin_idx"] = alignment_payload["motion_nearest_spectrogram_bin_idx"]
		out_path = output_dir / f"{filename_prefix}spectrograms_{session_id}.npz"
		np.savez_compressed(out_path, **export_dict)
		return out_path


	@classmethod
	def compute_one_session_summary_row(cls, idx: int, a_raw: mne.io.BaseRaw, a_result: Dict[str, Any], xdf_filename: Optional[str] = None, freq_min: float = 1.0, freq_max: float = 40.0) -> Optional[Dict[str, Any]]:
		"""Compute one summary-metrics dict for a session, or None if spectrogram missing."""
		if a_result is None or "spectogram" not in a_result:
			return None

		def _band_mean(Sxx_vals: np.ndarray, freqs_vals: np.ndarray, low: float, high: float) -> float:
			band_mask = (freqs_vals >= low) & (freqs_vals < high)
			if not np.any(band_mask):
				return float("nan")
			return float(np.nanmean(Sxx_vals[:, band_mask]))

		def _manifold_summary_scalars(manifold_result) -> Dict[str, float]:
			out: Dict[str, float] = {"manifold_n_windows": float("nan"), "manifold_pc1_std": float("nan"), "manifold_pc2_std": float("nan"), "manifold_pc3_std": float("nan"), "manifold_pc1_range": float("nan"), "manifold_pc2_range": float("nan"), "manifold_pc3_range": float("nan"), "manifold_traj_path_length": float("nan"), "manifold_traj_speed_rms": float("nan")}
			if not isinstance(manifold_result, dict):
				return out
			try:
				embedding = np.asarray(manifold_result.get("embedding"), dtype=float)
				times = np.asarray(manifold_result.get("times"), dtype=float)
				n_windows = int(manifold_result.get("n_windows", embedding.shape[0] if embedding.ndim == 2 else 0))
				out["manifold_n_windows"] = float(n_windows)
				if embedding.ndim != 2 or embedding.shape[0] == 0:
					return out
				n_comp = min(3, embedding.shape[1])
				for i in range(n_comp):
					col = embedding[:, i]
					finite = col[np.isfinite(col)]
					if finite.size == 0:
						continue
					out[f"manifold_pc{i + 1}_std"] = float(np.nanstd(finite))
					out[f"manifold_pc{i + 1}_range"] = float(np.nanmax(finite) - np.nanmin(finite))
				## END for i in range(n_comp)...
				if embedding.shape[0] >= 2:
					diffs = np.diff(embedding, axis=0)
					step_lengths = np.linalg.norm(diffs, axis=1)
					finite_steps = step_lengths[np.isfinite(step_lengths)]
					if finite_steps.size > 0:
						out["manifold_traj_path_length"] = float(np.nansum(finite_steps))
					if times.size == embedding.shape[0]:
						dt = np.diff(times)
						with np.errstate(divide="ignore", invalid="ignore"):
							speeds = step_lengths / dt
						finite_speeds = speeds[np.isfinite(speeds)]
						if finite_speeds.size > 0:
							out["manifold_traj_speed_rms"] = float(np.sqrt(np.nanmean(np.square(finite_speeds))))
			except Exception:
				return out
			return out

		try:
			spect_dict = a_result["spectogram"]
			Sxx_avg = spect_dict["Sxx_avg"]
			freqs_vals = np.asarray(Sxx_avg.coords["freqs"])
			Sxx_vals = np.asarray(Sxx_avg)
		except Exception as e:
			print(f"  WARN: failed to unpack spectrogram for session {idx}: {e}")
			return None
		main_band_mask = (freqs_vals >= freq_min) & (freqs_vals <= freq_max)
		if not np.any(main_band_mask):
			print(f"  WARN: no frequencies within [{freq_min}, {freq_max}] Hz for session {idx}")
			return None
		Sxx_main = Sxx_vals[:, main_band_mask]
		freqs_main = freqs_vals[main_band_mask]
		total_power = float(np.nanmean(Sxx_main))
		delta_power = _band_mean(Sxx_vals, freqs_vals, 1.0, 4.0)
		theta_power = _band_mean(Sxx_vals, freqs_vals, 4.0, 8.0)
		alpha_power = _band_mean(Sxx_vals, freqs_vals, 8.0, 13.0)
		beta_power = _band_mean(Sxx_vals, freqs_vals, 13.0, 30.0)

		def _safe_div(n: float, d: float) -> float:
			return float(n / d) if np.isfinite(n) and np.isfinite(d) and d != 0 else float("nan")

		try:
			global_psd = np.nanmean(Sxx_main, axis=0)
			peak_idx = int(np.nanargmax(global_psd))
			dominant_freq_hz = float(freqs_main[peak_idx])
		except Exception:
			dominant_freq_hz = float("nan")
		meas_date = a_raw.info.get("meas_date")
		if isinstance(meas_date, datetime):
			meas_date_str = meas_date.strftime("%Y-%m-%d %H:%M:%S")
		else:
			meas_date_str = str(meas_date)
		try:
			times = a_raw.times
			duration_s = float(times[-1] - times[0]) if times.size > 0 else float("nan")
		except Exception:
			duration_s = float("nan")
		sfreq = float(a_raw.info.get("sfreq", float("nan")))
		n_channels = len(a_raw.info.get("ch_names", []))
		manifold_scalars = _manifold_summary_scalars(a_result.get("eeg_manifold"))
		return {"session_idx": idx, "xdf_filename": xdf_filename, "meas_date": meas_date_str, "duration_s": duration_s, "sfreq_hz": sfreq, "n_channels": n_channels, f"total_power_{freq_min:.1f}_to_{freq_max:.1f}_hz": total_power, "delta_power": delta_power, "theta_power": theta_power, "alpha_power": alpha_power, "beta_power": beta_power, "delta_rel": _safe_div(delta_power, total_power), "theta_rel": _safe_div(theta_power, total_power), "alpha_rel": _safe_div(alpha_power, total_power), "beta_rel": _safe_div(beta_power, total_power), "alpha_theta_ratio": _safe_div(alpha_power, theta_power), "beta_alpha_ratio": _safe_div(beta_power, alpha_power), "dominant_freq_hz": dominant_freq_hz, **manifold_scalars}


	@classmethod
	def export_netcdf_from_hdf5(cls, hdf5_path: Path, output_path: Path) -> Path:
		"""Rebuild NetCDF interchange file by reading a finished spectrograms HDF5 (no Raw needed)."""
		hdf5_path = Path(hdf5_path)
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		session_rows: List[Dict[str, Any]] = []
		with h5py.File(hdf5_path, "r") as f:
			freq_min = float(f.attrs.get("freq_min", 1.0))
			freq_max = float(f.attrs.get("freq_max", 40.0))
			sessions_grp = f["sessions"]
			for session_key in sorted(sessions_grp.keys()):
				sgrp = sessions_grp[session_key]
				channel_names = [str(v) for v in list(sgrp["channel_names"][()])]
				freqs = np.asarray(sgrp["freqs"][()], dtype=np.float64)
				times = np.asarray(sgrp["times"][()], dtype=np.float64)
				Sxx_stack = np.asarray(sgrp["spectrogram"][()], dtype=np.float64)
				eeg_grp = sgrp["eeg"]
				motion_grp = sgrp["motion"]
				alignment_grp = sgrp["alignment"]
				eeg_payload = {"has_data": eeg_grp["raw_data"].size > 0, "raw_data": np.asarray(eeg_grp["raw_data"][()], dtype=np.float64), "raw_times_sec": np.asarray(eeg_grp["raw_times_sec"][()], dtype=np.float64), "channel_names": [str(v) for v in list(eeg_grp["channel_names"][()])], "sfreq_hz": float(eeg_grp.attrs.get("sfreq_hz", float("nan"))), "time_origin_meas_date_sec": float(eeg_grp.attrs.get("time_origin_meas_date_sec", float("nan")))}
				motion_payload = {"has_data": bool(motion_grp.attrs.get("has_motion", False)), "raw_data": np.asarray(motion_grp["raw_data"][()], dtype=np.float64), "raw_times_sec": np.asarray(motion_grp["raw_times_sec"][()], dtype=np.float64), "channel_names": [str(v) for v in list(motion_grp["channel_names"][()])], "sfreq_hz": float(motion_grp.attrs.get("sfreq_hz", float("nan"))), "time_origin_meas_date_sec": float(motion_grp.attrs.get("time_origin_meas_date_sec", float("nan")))}
				alignment_payload = {"alignment_method": str(alignment_grp.attrs.get("alignment_method", "raw_plus_map")), "spectrogram_times_sec": np.asarray(alignment_grp["spectrogram_times_sec"][()], dtype=np.float64), "spectrogram_start_time_sec": float(alignment_grp.attrs.get("spectrogram_start_time_sec", float("nan"))), "spectrogram_end_time_sec": float(alignment_grp.attrs.get("spectrogram_end_time_sec", float("nan"))), "eeg_time_origin_meas_date_sec": float(eeg_grp.attrs.get("time_origin_meas_date_sec", float("nan"))), "motion_time_origin_meas_date_sec": float(motion_grp.attrs.get("time_origin_meas_date_sec", float("nan"))), "eeg_nearest_spectrogram_bin_idx": np.asarray(alignment_grp["eeg_nearest_spectrogram_bin_idx"][()], dtype=np.int64), "motion_nearest_spectrogram_bin_idx": np.asarray(alignment_grp["motion_nearest_spectrogram_bin_idx"][()], dtype=np.int64), "eeg_has_nearest_map": bool(alignment_grp.attrs.get("eeg_has_nearest_map", False)), "motion_has_nearest_map": bool(alignment_grp.attrs.get("motion_has_nearest_map", False))}
				try:
					session_idx = int(str(session_key).split("_")[-1])
				except Exception:
					session_idx = len(session_rows)
				session_rows.append({"session_idx": session_idx, "meas_date_iso": str(sgrp.attrs.get("meas_date_iso", "")), "meas_date_sec": float(sgrp.attrs.get("meas_date_sec", float("nan"))), "sfreq_hz": float(sgrp.attrs.get("sfreq_hz", float("nan"))), "duration_s": float(sgrp.attrs.get("duration_s", float("nan"))), "n_channels": int(sgrp.attrs.get("n_channels", len(channel_names))), "xdf_filename": str(sgrp.attrs.get("xdf_filename", "")), "channel_names": channel_names, "freqs": freqs, "times": times, "spectrogram": Sxx_stack, "eeg": eeg_payload, "motion": motion_payload, "alignment": alignment_payload})
			## END for session_key in sorted(sessions_grp.keys())...
		if not session_rows:
			ds_empty = xr.Dataset(attrs={"format_version": "1.1", "freq_min": float(freq_min), "freq_max": float(freq_max), "n_sessions": 0})
			ds_empty.to_netcdf(output_path)
			print(f"Exported spectrograms to NetCDF: {output_path.as_posix()}")
			return output_path
		# Reuse padding/write logic via temporary call structure — build padded arrays like export_spectrograms_netcdf
		n_sessions = len(session_rows)
		n_channels = max(r["spectrogram"].shape[0] for r in session_rows)
		n_freq = max(r["freqs"].size for r in session_rows)
		n_time = max(r["times"].size for r in session_rows)
		n_eeg_channels = max((r["eeg"]["raw_data"].shape[0] for r in session_rows), default=0)
		n_eeg_samples = max((r["eeg"]["raw_times_sec"].size for r in session_rows), default=0)
		n_motion_channels = max((r["motion"]["raw_data"].shape[0] for r in session_rows), default=0)
		n_motion_samples = max((r["motion"]["raw_times_sec"].size for r in session_rows), default=0)
		spectrogram_padded = np.full((n_sessions, n_channels, n_freq, n_time), np.nan, dtype=np.float64)
		freqs_padded = np.full((n_sessions, n_freq), np.nan, dtype=np.float64)
		times_padded = np.full((n_sessions, n_time), np.nan, dtype=np.float64)
		channel_names_padded = np.empty((n_sessions, n_channels), dtype=object)
		channel_names_padded[:] = ""
		eeg_raw_data_padded = np.full((n_sessions, max(n_eeg_channels, 1), max(n_eeg_samples, 1)), np.nan, dtype=np.float64)
		eeg_raw_times_padded = np.full((n_sessions, max(n_eeg_samples, 1)), np.nan, dtype=np.float64)
		eeg_channel_names_padded = np.empty((n_sessions, max(n_eeg_channels, 1)), dtype=object)
		eeg_channel_names_padded[:] = ""
		eeg_nearest_idx_padded = np.full((n_sessions, max(n_eeg_samples, 1)), -1, dtype=np.int64)
		motion_raw_data_padded = np.full((n_sessions, max(n_motion_channels, 1), max(n_motion_samples, 1)), np.nan, dtype=np.float64)
		motion_raw_times_padded = np.full((n_sessions, max(n_motion_samples, 1)), np.nan, dtype=np.float64)
		motion_channel_names_padded = np.empty((n_sessions, max(n_motion_channels, 1)), dtype=object)
		motion_channel_names_padded[:] = ""
		motion_nearest_idx_padded = np.full((n_sessions, max(n_motion_samples, 1)), -1, dtype=np.int64)
		for i, r in enumerate(session_rows):
			spec = r["spectrogram"]
			spectrogram_padded[i, :spec.shape[0], :spec.shape[1], :spec.shape[2]] = spec
			freqs_padded[i, :r["freqs"].size] = r["freqs"]
			times_padded[i, :r["times"].size] = r["times"]
			eeg_data = r["eeg"]["raw_data"]
			eeg_times = r["eeg"]["raw_times_sec"]
			if eeg_data.ndim == 2 and eeg_data.size > 0:
				eeg_raw_data_padded[i, :eeg_data.shape[0], :eeg_data.shape[1]] = eeg_data
			if eeg_times.size > 0:
				eeg_raw_times_padded[i, :eeg_times.size] = eeg_times
			eeg_idx = r["alignment"]["eeg_nearest_spectrogram_bin_idx"]
			if r["alignment"]["eeg_has_nearest_map"] and eeg_idx.size > 0:
				eeg_nearest_idx_padded[i, :eeg_idx.size] = eeg_idx
			motion_data = r["motion"]["raw_data"]
			motion_times = r["motion"]["raw_times_sec"]
			if motion_data.ndim == 2 and motion_data.size > 0:
				motion_raw_data_padded[i, :motion_data.shape[0], :motion_data.shape[1]] = motion_data
			if motion_times.size > 0:
				motion_raw_times_padded[i, :motion_times.size] = motion_times
			motion_idx = r["alignment"]["motion_nearest_spectrogram_bin_idx"]
			if r["alignment"]["motion_has_nearest_map"] and motion_idx.size > 0:
				motion_nearest_idx_padded[i, :motion_idx.size] = motion_idx
			for j, name in enumerate(r["channel_names"]):
				if j < n_channels:
					channel_names_padded[i, j] = name
			for j, name in enumerate(r["eeg"]["channel_names"]):
				if j < max(n_eeg_channels, 1):
					eeg_channel_names_padded[i, j] = name
			for j, name in enumerate(r["motion"]["channel_names"]):
				if j < max(n_motion_channels, 1):
					motion_channel_names_padded[i, j] = name
		## END for i, r in enumerate(session_rows)...
		ds = xr.Dataset(
			{"spectrogram": (("session", "channel", "freq", "time"), spectrogram_padded), "freqs": (("session", "freq"), freqs_padded), "times": (("session", "time"), times_padded), "meas_date_iso": (("session",), np.array([r["meas_date_iso"] for r in session_rows], dtype=object)), "meas_date_sec": (("session",), np.array([r["meas_date_sec"] for r in session_rows], dtype=np.float64)), "sfreq_hz": (("session",), np.array([r["sfreq_hz"] for r in session_rows], dtype=np.float64)), "duration_s": (("session",), np.array([r["duration_s"] for r in session_rows], dtype=np.float64)), "n_channels": (("session",), np.array([r["n_channels"] for r in session_rows], dtype=np.int64)), "xdf_filename": (("session",), np.array([r["xdf_filename"] for r in session_rows], dtype=object)), "channel_names": (("session", "channel"), channel_names_padded), "eeg_raw_data": (("session", "eeg_channel", "eeg_sample"), eeg_raw_data_padded), "eeg_raw_times_sec": (("session", "eeg_sample"), eeg_raw_times_padded), "eeg_channel_names": (("session", "eeg_channel"), eeg_channel_names_padded), "has_motion": (("session",), np.array([1 if r["motion"]["has_data"] else 0 for r in session_rows], dtype=np.int8)), "motion_raw_data": (("session", "motion_channel", "motion_sample"), motion_raw_data_padded), "motion_raw_times_sec": (("session", "motion_sample"), motion_raw_times_padded), "motion_channel_names": (("session", "motion_channel"), motion_channel_names_padded), "alignment_method": (("session",), np.array([r["alignment"]["alignment_method"] for r in session_rows], dtype=object)), "spectrogram_start_time_sec": (("session",), np.array([r["alignment"]["spectrogram_start_time_sec"] for r in session_rows], dtype=np.float64)), "spectrogram_end_time_sec": (("session",), np.array([r["alignment"]["spectrogram_end_time_sec"] for r in session_rows], dtype=np.float64)), "eeg_time_origin_meas_date_sec": (("session",), np.array([r["alignment"]["eeg_time_origin_meas_date_sec"] for r in session_rows], dtype=np.float64)), "motion_time_origin_meas_date_sec": (("session",), np.array([r["alignment"]["motion_time_origin_meas_date_sec"] for r in session_rows], dtype=np.float64)), "eeg_has_nearest_map": (("session",), np.array([1 if r["alignment"]["eeg_has_nearest_map"] else 0 for r in session_rows], dtype=np.int8)), "motion_has_nearest_map": (("session",), np.array([1 if r["alignment"]["motion_has_nearest_map"] else 0 for r in session_rows], dtype=np.int8)), "eeg_nearest_spectrogram_bin_idx": (("session", "eeg_sample"), eeg_nearest_idx_padded), "motion_nearest_spectrogram_bin_idx": (("session", "motion_sample"), motion_nearest_idx_padded)},
			coords={"session": np.arange(n_sessions), "channel": np.arange(n_channels), "freq": np.arange(n_freq), "time": np.arange(n_time), "eeg_channel": np.arange(max(n_eeg_channels, 1)), "eeg_sample": np.arange(max(n_eeg_samples, 1)), "motion_channel": np.arange(max(n_motion_channels, 1)), "motion_sample": np.arange(max(n_motion_samples, 1))},
			attrs={"format_version": "1.1", "freq_min": float(freq_min), "freq_max": float(freq_max), "n_sessions": n_sessions},
		)
		ds.to_netcdf(output_path)
		print(f"Exported spectrograms to NetCDF: {output_path.as_posix()}")
		return output_path


	@classmethod
	def export_parquet_from_hdf5(cls, hdf5_path: Path, output_path: Path) -> Path:
		"""Rebuild Parquet interchange file by reading a finished spectrograms HDF5 (no Raw needed)."""
		hdf5_path = Path(hdf5_path)
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		rows: List[Dict[str, Any]] = []
		with h5py.File(hdf5_path, "r") as f:
			sessions_grp = f["sessions"]
			for session_key in sorted(sessions_grp.keys()):
				sgrp = sessions_grp[session_key]
				channel_names = [str(v) for v in list(sgrp["channel_names"][()])]
				freqs = np.asarray(sgrp["freqs"][()], dtype=np.float64)
				times = np.asarray(sgrp["times"][()], dtype=np.float64)
				Sxx_stack = np.asarray(sgrp["spectrogram"][()], dtype=np.float64)
				eeg_grp = sgrp["eeg"]
				motion_grp = sgrp["motion"]
				alignment_grp = sgrp["alignment"]
				try:
					session_idx = int(str(session_key).split("_")[-1])
				except Exception:
					session_idx = len(rows)
				rows.append({"session_idx": session_idx, "meas_date_iso": str(sgrp.attrs.get("meas_date_iso", "")), "meas_date_sec": float(sgrp.attrs.get("meas_date_sec", float("nan"))), "xdf_filename": str(sgrp.attrs.get("xdf_filename", "")), "sfreq_hz": float(sgrp.attrs.get("sfreq_hz", float("nan"))), "duration_s": float(sgrp.attrs.get("duration_s", float("nan"))), "n_channels": int(sgrp.attrs.get("n_channels", len(channel_names))), "channel_names": channel_names, "freqs": freqs.tolist(), "times": times.tolist(), "spectrogram": Sxx_stack.tolist(), "eeg_raw_data": np.asarray(eeg_grp["raw_data"][()], dtype=np.float64).tolist(), "eeg_raw_times_sec": np.asarray(eeg_grp["raw_times_sec"][()], dtype=np.float64).tolist(), "eeg_channel_names": [str(v) for v in list(eeg_grp["channel_names"][()])], "has_motion": bool(motion_grp.attrs.get("has_motion", False)), "motion_raw_data": np.asarray(motion_grp["raw_data"][()], dtype=np.float64).tolist(), "motion_raw_times_sec": np.asarray(motion_grp["raw_times_sec"][()], dtype=np.float64).tolist(), "motion_channel_names": [str(v) for v in list(motion_grp["channel_names"][()])], "alignment_method": str(alignment_grp.attrs.get("alignment_method", "raw_plus_map")), "spectrogram_times_sec": np.asarray(alignment_grp["spectrogram_times_sec"][()], dtype=np.float64).tolist(), "spectrogram_start_time_sec": float(alignment_grp.attrs.get("spectrogram_start_time_sec", float("nan"))), "spectrogram_end_time_sec": float(alignment_grp.attrs.get("spectrogram_end_time_sec", float("nan"))), "eeg_time_origin_meas_date_sec": float(eeg_grp.attrs.get("time_origin_meas_date_sec", float("nan"))), "motion_time_origin_meas_date_sec": float(motion_grp.attrs.get("time_origin_meas_date_sec", float("nan"))), "eeg_has_nearest_map": bool(alignment_grp.attrs.get("eeg_has_nearest_map", False)), "motion_has_nearest_map": bool(alignment_grp.attrs.get("motion_has_nearest_map", False)), "eeg_nearest_spectrogram_bin_idx": np.asarray(alignment_grp["eeg_nearest_spectrogram_bin_idx"][()], dtype=np.int64).tolist(), "motion_nearest_spectrogram_bin_idx": np.asarray(alignment_grp["motion_nearest_spectrogram_bin_idx"][()], dtype=np.int64).tolist()})
			## END for session_key in sorted(sessions_grp.keys())...
		df = pd.DataFrame.from_records(rows)
		df.to_parquet(output_path, index=False)
		print(f"Exported spectrograms to Parquet: {output_path.as_posix()}")
		return output_path


	@classmethod
	def export_spectrograms_for_rerun(cls, active_only_out_eeg_raws, results, output_dir: Path, freq_min: float = 1.0, freq_max: float = 40.0, filename_prefix: str = "", active_only_out_motion_raws: Optional[List[Optional[mne.io.BaseRaw]]] = None) -> List[Path]:
		"""
		Export spectrograms and session datetimes to one NumPy .npz file per session under output_dir for later viewing in Rerun (e.g. via view_spectrograms_rerun.py).

		Each file contains: freq_min, freq_max, session_indices=[0], s0_meas_date_sec, s0_channel_names, s0_freqs, s0_times, s0_Sxx.
		Frequency range is restricted to [freq_min, freq_max]. Each .npz can be passed to view_spectrograms_rerun.py independently.
		"""
		output_dir = Path(output_dir)
		output_dir.mkdir(parents=True, exist_ok=True)
		written_paths: List[Path] = []
		used_session_ids: set = set()
		for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
			try:
				if a_result is None or "spectogram" not in a_result:
					continue
				meas_date = a_raw.info.get("meas_date")
				if meas_date is None:
					meas_date_sec = float("nan")
					session_id = f"session_{idx:03d}"
				else:
					if getattr(meas_date, "tzinfo", None) is None:
						meas_date = meas_date.replace(tzinfo=timezone.utc)
					meas_date_sec = meas_date.timestamp()
					session_id = meas_date.strftime("%Y-%m-%dT%H-%M-%S")
				base_id = session_id
				while session_id in used_session_ids:
					suffix = sum(1 for s in used_session_ids if s == base_id or (isinstance(s, str) and s.startswith(base_id + "_")))
					session_id = f"{base_id}_{suffix}"
				used_session_ids.add(session_id)
				spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
				channel_names = np.array(list(spectogram_result_dict.keys()), dtype=object)
				f0, t0, Sxx0 = next(iter(spectogram_result_dict.values()))
				freq_mask = (f0 >= freq_min) & (f0 <= freq_max)
				freqs = np.asarray(f0)[freq_mask]
				times = np.asarray(t0)
				Sxx_list = []
				for ch_name in channel_names:
					f, t, Sxx = spectogram_result_dict[ch_name]
					Sxx_list.append(np.asarray(Sxx)[freq_mask, :])
				Sxx_stack = np.stack(Sxx_list, axis=0)
				eeg_payload = cls._extract_raw_export_payload(a_raw)
				motion_raw = active_only_out_motion_raws[idx] if (active_only_out_motion_raws is not None and idx < len(active_only_out_motion_raws)) else None
				motion_payload = cls._extract_raw_export_payload(motion_raw)
				alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
				export_dict = {"freq_min": np.array(freq_min), "freq_max": np.array(freq_max), "session_indices": np.array([0])}
				export_dict["s0_meas_date_sec"] = np.array(meas_date_sec)
				export_dict["s0_channel_names"] = channel_names
				export_dict["s0_freqs"] = freqs
				export_dict["s0_times"] = times
				export_dict["s0_Sxx"] = Sxx_stack
				export_dict["s0_eeg_raw_data"] = eeg_payload["raw_data"]
				export_dict["s0_eeg_raw_times_sec"] = eeg_payload["raw_times_sec"]
				export_dict["s0_eeg_channel_names"] = np.array(eeg_payload["channel_names"], dtype=object)
				export_dict["s0_has_motion"] = np.array(bool(motion_payload["has_data"]))
				export_dict["s0_motion_raw_data"] = motion_payload["raw_data"]
				export_dict["s0_motion_raw_times_sec"] = motion_payload["raw_times_sec"]
				export_dict["s0_motion_channel_names"] = np.array(motion_payload["channel_names"], dtype=object)
				export_dict["s0_alignment_method"] = np.array(alignment_payload["alignment_method"], dtype=object)
				export_dict["s0_spectrogram_times_sec"] = alignment_payload["spectrogram_times_sec"]
				export_dict["s0_spectrogram_start_time_sec"] = np.array(alignment_payload["spectrogram_start_time_sec"])
				export_dict["s0_spectrogram_end_time_sec"] = np.array(alignment_payload["spectrogram_end_time_sec"])
				export_dict["s0_eeg_time_origin_meas_date_sec"] = np.array(alignment_payload["eeg_time_origin_meas_date_sec"])
				export_dict["s0_motion_time_origin_meas_date_sec"] = np.array(alignment_payload["motion_time_origin_meas_date_sec"])
				export_dict["s0_eeg_has_nearest_map"] = np.array(alignment_payload["eeg_has_nearest_map"])
				export_dict["s0_motion_has_nearest_map"] = np.array(alignment_payload["motion_has_nearest_map"])
				export_dict["s0_eeg_nearest_spectrogram_bin_idx"] = alignment_payload["eeg_nearest_spectrogram_bin_idx"]
				export_dict["s0_motion_nearest_spectrogram_bin_idx"] = alignment_payload["motion_nearest_spectrogram_bin_idx"]
				session_filename = f"{filename_prefix}spectrograms_{session_id}.npz"
				out_path = output_dir / session_filename
				np.savez_compressed(out_path, **export_dict)
				written_paths.append(out_path)
			except Exception as e:
				print(f"  WARN: export_spectrograms_for_rerun skipped session {idx}: {e}")
				continue
		if written_paths:
			print(f"Exported {len(written_paths)} spectrogram .npz file(s) for Rerun to: {output_dir.as_posix()}")
		return written_paths


	@classmethod
	def export_spectrograms_hdf5(cls, active_only_out_eeg_raws, results, output_path: Path, freq_min: float = 1.0, freq_max: float = 40.0, stream_infos_df: Optional[pd.DataFrame] = None, active_only_out_motion_raws: Optional[List[Optional[mne.io.BaseRaw]]] = None) -> Path:
		"""
		Export spectrograms, timestamps, and recording metadata to a single HDF5 file for interchange.

		The output can be read by Python (h5py), MATLAB, R, Julia, and other tools. Each session is stored
		under /sessions/session_XXX with datasets: freqs (Hz), times (s), channel_names, spectrogram
		(n_channels x n_freqs x n_times), and group attributes: meas_date_iso, meas_date_sec, sfreq_hz,
		duration_s, n_channels; if stream_infos_df is provided, xdf_filename is attached per session.
		"""
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		dataset_to_filename: Dict[int, str] = {}
		if stream_infos_df is not None:
			try:
				tmp_df = _reset_stream_infos_index_for_groupby(stream_infos_df)
				if "xdf_dataset_idx" in tmp_df.columns and "xdf_filename" in tmp_df.columns:
					for ds_idx, grp in tmp_df.groupby("xdf_dataset_idx"):
						dataset_to_filename[int(ds_idx)] = str(grp["xdf_filename"].iloc[0])
			except Exception as e:
				print(f"  WARN: export_spectrograms_hdf5 failed to derive dataset -> filename mapping: {e}")
		session_indices: List[int] = []
		with h5py.File(output_path, "w") as f:
			f.attrs["format_version"] = "1.0"
			f.attrs["freq_min"] = float(freq_min)
			f.attrs["freq_max"] = float(freq_max)
			sessions_grp = f.create_group("sessions")
			for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
				try:
					if a_result is None or "spectogram" not in a_result:
						continue
					meas_date = a_raw.info.get("meas_date")
					if meas_date is None:
						meas_date_sec = float("nan")
						meas_date_iso = ""
					else:
						if getattr(meas_date, "tzinfo", None) is None:
							meas_date = meas_date.replace(tzinfo=timezone.utc)
						meas_date_sec = meas_date.timestamp()
						meas_date_iso = meas_date.isoformat()
					spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
					channel_names = list(spectogram_result_dict.keys())
					f0, t0, Sxx0 = next(iter(spectogram_result_dict.values()))
					freq_mask = (f0 >= freq_min) & (f0 <= freq_max)
					freqs = np.asarray(f0)[freq_mask]
					times = np.asarray(t0)
					Sxx_list = []
					for ch_name in channel_names:
						f_ch, t_ch, Sxx = spectogram_result_dict[ch_name]
						Sxx_list.append(np.asarray(Sxx)[freq_mask, :])
					Sxx_stack = np.stack(Sxx_list, axis=0)
					eeg_payload = cls._extract_raw_export_payload(a_raw)
					motion_raw = active_only_out_motion_raws[idx] if (active_only_out_motion_raws is not None and idx < len(active_only_out_motion_raws)) else None
					motion_payload = cls._extract_raw_export_payload(motion_raw)
					alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
					try:
						duration_s = float(a_raw.times[-1] - a_raw.times[0]) if a_raw.times.size > 0 else float("nan")
					except Exception:
						duration_s = float("nan")
					sfreq_hz = float(a_raw.info.get("sfreq", float("nan")))
					n_channels = len(channel_names)
					sgrp = sessions_grp.create_group(f"session_{idx:03d}")
					sgrp.create_dataset("freqs", data=freqs, dtype=np.float64)
					sgrp.create_dataset("times", data=times, dtype=np.float64)
					dt_str = h5py.special_dtype(vlen=str)
					ch_dset = sgrp.create_dataset("channel_names", (len(channel_names),), dtype=dt_str)
					ch_dset[:] = channel_names
					sgrp.create_dataset("spectrogram", data=Sxx_stack, dtype=np.float64)
					eeg_grp = sgrp.create_group("eeg")
					eeg_grp.create_dataset("raw_data", data=eeg_payload["raw_data"], dtype=np.float64)
					eeg_grp.create_dataset("raw_times_sec", data=eeg_payload["raw_times_sec"], dtype=np.float64)
					eeg_ch_dset = eeg_grp.create_dataset("channel_names", (len(eeg_payload["channel_names"]),), dtype=dt_str)
					if len(eeg_payload["channel_names"]) > 0:
						eeg_ch_dset[:] = eeg_payload["channel_names"]
					eeg_grp.attrs["sfreq_hz"] = float(eeg_payload["sfreq_hz"])
					eeg_grp.attrs["time_origin_meas_date_sec"] = float(eeg_payload["time_origin_meas_date_sec"])
					motion_grp = sgrp.create_group("motion")
					motion_grp.attrs["has_motion"] = bool(motion_payload["has_data"])
					motion_grp.create_dataset("raw_data", data=motion_payload["raw_data"], dtype=np.float64)
					motion_grp.create_dataset("raw_times_sec", data=motion_payload["raw_times_sec"], dtype=np.float64)
					motion_ch_dset = motion_grp.create_dataset("channel_names", (len(motion_payload["channel_names"]),), dtype=dt_str)
					if len(motion_payload["channel_names"]) > 0:
						motion_ch_dset[:] = motion_payload["channel_names"]
					motion_grp.attrs["sfreq_hz"] = float(motion_payload["sfreq_hz"])
					motion_grp.attrs["time_origin_meas_date_sec"] = float(motion_payload["time_origin_meas_date_sec"])
					alignment_grp = sgrp.create_group("alignment")
					alignment_grp.attrs["alignment_method"] = alignment_payload["alignment_method"]
					alignment_grp.attrs["eeg_has_nearest_map"] = bool(alignment_payload["eeg_has_nearest_map"])
					alignment_grp.attrs["motion_has_nearest_map"] = bool(alignment_payload["motion_has_nearest_map"])
					alignment_grp.attrs["spectrogram_start_time_sec"] = float(alignment_payload["spectrogram_start_time_sec"])
					alignment_grp.attrs["spectrogram_end_time_sec"] = float(alignment_payload["spectrogram_end_time_sec"])
					alignment_grp.create_dataset("spectrogram_times_sec", data=alignment_payload["spectrogram_times_sec"], dtype=np.float64)
					alignment_grp.create_dataset("eeg_nearest_spectrogram_bin_idx", data=alignment_payload["eeg_nearest_spectrogram_bin_idx"], dtype=np.int64)
					alignment_grp.create_dataset("motion_nearest_spectrogram_bin_idx", data=alignment_payload["motion_nearest_spectrogram_bin_idx"], dtype=np.int64)
					sgrp.attrs["meas_date_iso"] = meas_date_iso
					sgrp.attrs["meas_date_sec"] = meas_date_sec
					sgrp.attrs["sfreq_hz"] = sfreq_hz
					sgrp.attrs["duration_s"] = duration_s
					sgrp.attrs["n_channels"] = n_channels
					sgrp.attrs["has_motion"] = bool(motion_payload["has_data"])
					xdf_filename = dataset_to_filename.get(idx)
					if xdf_filename is not None:
						sgrp.attrs["xdf_filename"] = str(xdf_filename)
					session_indices.append(idx)
				except Exception as e:
					print(f"  WARN: export_spectrograms_hdf5 skipped session {idx}: {e}")
					continue
			f.attrs["n_sessions"] = len(session_indices)
		print(f"Exported spectrograms to HDF5: {output_path.as_posix()}")
		return output_path


	@classmethod
	def export_spectrograms_netcdf(cls, active_only_out_eeg_raws, results, output_path: Path, freq_min: float = 1.0, freq_max: float = 40.0, stream_infos_df: Optional[pd.DataFrame] = None, active_only_out_motion_raws: Optional[List[Optional[mne.io.BaseRaw]]] = None) -> Path:
		"""
		Export spectrograms, timestamps, and recording metadata to a single CF-friendly NetCDF file for interchange.

		The output can be read by Python (xarray/netCDF4), R, Julia, MATLAB, and other tools. Stores spectrograms
		(session, channel, freq, time), time/freq axes with NaN padding for variable-length sessions, and
		session-level metadata (meas_date_iso, meas_date_sec, sfreq_hz, duration_s, n_channels, xdf_filename).
		"""
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		dataset_to_filename: Dict[int, str] = {}
		if stream_infos_df is not None:
			try:
				tmp_df = _reset_stream_infos_index_for_groupby(stream_infos_df)
				if "xdf_dataset_idx" in tmp_df.columns and "xdf_filename" in tmp_df.columns:
					for ds_idx, grp in tmp_df.groupby("xdf_dataset_idx"):
						dataset_to_filename[int(ds_idx)] = str(grp["xdf_filename"].iloc[0])
			except Exception as e:
				print(f"  WARN: export_spectrograms_netcdf failed to derive dataset -> filename mapping: {e}")
		session_rows: List[Dict[str, Any]] = []
		for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
			try:
				if a_result is None or "spectogram" not in a_result:
					continue
				meas_date = a_raw.info.get("meas_date")
				if meas_date is None:
					meas_date_sec = float("nan")
					meas_date_iso = ""
				else:
					if getattr(meas_date, "tzinfo", None) is None:
						meas_date = meas_date.replace(tzinfo=timezone.utc)
					meas_date_sec = meas_date.timestamp()
					meas_date_iso = meas_date.isoformat()
				spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
				channel_names = list(spectogram_result_dict.keys())
				f0, t0, Sxx0 = next(iter(spectogram_result_dict.values()))
				freq_mask = (f0 >= freq_min) & (f0 <= freq_max)
				freqs = np.asarray(f0)[freq_mask]
				times = np.asarray(t0)
				Sxx_list = []
				for ch_name in channel_names:
					f, t, Sxx = spectogram_result_dict[ch_name]
					Sxx_list.append(np.asarray(Sxx)[freq_mask, :])
				Sxx_stack = np.stack(Sxx_list, axis=0)
				eeg_payload = cls._extract_raw_export_payload(a_raw)
				motion_raw = active_only_out_motion_raws[idx] if (active_only_out_motion_raws is not None and idx < len(active_only_out_motion_raws)) else None
				motion_payload = cls._extract_raw_export_payload(motion_raw)
				alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
				try:
					duration_s = float(a_raw.times[-1] - a_raw.times[0]) if a_raw.times.size > 0 else float("nan")
				except Exception:
					duration_s = float("nan")
				sfreq_hz = float(a_raw.info.get("sfreq", float("nan")))
				xdf_filename = dataset_to_filename.get(idx)
				session_rows.append({"session_idx": idx, "meas_date_iso": meas_date_iso, "meas_date_sec": meas_date_sec, "sfreq_hz": sfreq_hz, "duration_s": duration_s, "n_channels": len(channel_names), "xdf_filename": str(xdf_filename) if xdf_filename is not None else "", "channel_names": channel_names, "freqs": freqs, "times": times, "spectrogram": Sxx_stack, "eeg": eeg_payload, "motion": motion_payload, "alignment": alignment_payload})
			except Exception as e:
				print(f"  WARN: export_spectrograms_netcdf skipped session {idx}: {e}")
				continue
		if not session_rows:
			print("  WARN: export_spectrograms_netcdf had no valid sessions; writing empty NetCDF.")
			ds_empty = xr.Dataset(attrs={"format_version": "1.1", "freq_min": float(freq_min), "freq_max": float(freq_max), "n_sessions": 0})
			ds_empty.to_netcdf(output_path)
			print(f"Exported spectrograms to NetCDF: {output_path.as_posix()}")
			return output_path
		n_sessions = len(session_rows)
		n_channels = max(r["spectrogram"].shape[0] for r in session_rows)
		n_freq = max(r["freqs"].size for r in session_rows)
		n_time = max(r["times"].size for r in session_rows)
		n_eeg_channels = max((r["eeg"]["raw_data"].shape[0] for r in session_rows), default=0)
		n_eeg_samples = max((r["eeg"]["raw_times_sec"].size for r in session_rows), default=0)
		n_motion_channels = max((r["motion"]["raw_data"].shape[0] for r in session_rows), default=0)
		n_motion_samples = max((r["motion"]["raw_times_sec"].size for r in session_rows), default=0)
		spectrogram_padded = np.full((n_sessions, n_channels, n_freq, n_time), np.nan, dtype=np.float64)
		freqs_padded = np.full((n_sessions, n_freq), np.nan, dtype=np.float64)
		times_padded = np.full((n_sessions, n_time), np.nan, dtype=np.float64)
		eeg_raw_data_padded = np.full((n_sessions, n_eeg_channels, n_eeg_samples), np.nan, dtype=np.float64)
		eeg_raw_times_padded = np.full((n_sessions, n_eeg_samples), np.nan, dtype=np.float64)
		motion_raw_data_padded = np.full((n_sessions, n_motion_channels, n_motion_samples), np.nan, dtype=np.float64)
		motion_raw_times_padded = np.full((n_sessions, n_motion_samples), np.nan, dtype=np.float64)
		eeg_nearest_idx_padded = np.full((n_sessions, n_eeg_samples), -1, dtype=np.int64)
		motion_nearest_idx_padded = np.full((n_sessions, n_motion_samples), -1, dtype=np.int64)
		channel_names_padded = np.full((n_sessions, n_channels), "", dtype=object)
		eeg_channel_names_padded = np.full((n_sessions, n_eeg_channels), "", dtype=object)
		motion_channel_names_padded = np.full((n_sessions, n_motion_channels), "", dtype=object)
		for i, r in enumerate(session_rows):
			nc, nf, nt = r["spectrogram"].shape
			spectrogram_padded[i, :nc, :nf, :nt] = r["spectrogram"]
			freqs_padded[i, :r["freqs"].size] = r["freqs"]
			times_padded[i, :r["times"].size] = r["times"]
			eeg_data = r["eeg"]["raw_data"]
			eeg_times = r["eeg"]["raw_times_sec"]
			if eeg_data.ndim == 2 and eeg_data.size > 0:
				eeg_raw_data_padded[i, :eeg_data.shape[0], :eeg_data.shape[1]] = eeg_data
			if eeg_times.size > 0:
				eeg_raw_times_padded[i, :eeg_times.size] = eeg_times
			eeg_idx = r["alignment"]["eeg_nearest_spectrogram_bin_idx"]
			if r["alignment"]["eeg_has_nearest_map"] and eeg_idx.size > 0:
				eeg_nearest_idx_padded[i, :eeg_idx.size] = eeg_idx
			motion_data = r["motion"]["raw_data"]
			motion_times = r["motion"]["raw_times_sec"]
			if motion_data.ndim == 2 and motion_data.size > 0:
				motion_raw_data_padded[i, :motion_data.shape[0], :motion_data.shape[1]] = motion_data
			if motion_times.size > 0:
				motion_raw_times_padded[i, :motion_times.size] = motion_times
			motion_idx = r["alignment"]["motion_nearest_spectrogram_bin_idx"]
			if r["alignment"]["motion_has_nearest_map"] and motion_idx.size > 0:
				motion_nearest_idx_padded[i, :motion_idx.size] = motion_idx
			for j, name in enumerate(r["channel_names"]):
				if j < n_channels:
					channel_names_padded[i, j] = name
			for j, name in enumerate(r["eeg"]["channel_names"]):
				if j < n_eeg_channels:
					eeg_channel_names_padded[i, j] = name
			for j, name in enumerate(r["motion"]["channel_names"]):
				if j < n_motion_channels:
					motion_channel_names_padded[i, j] = name
		meas_date_iso_arr = np.array([r["meas_date_iso"] for r in session_rows], dtype=object)
		meas_date_sec_arr = np.array([r["meas_date_sec"] for r in session_rows], dtype=np.float64)
		sfreq_hz_arr = np.array([r["sfreq_hz"] for r in session_rows], dtype=np.float64)
		duration_s_arr = np.array([r["duration_s"] for r in session_rows], dtype=np.float64)
		n_channels_arr = np.array([r["n_channels"] for r in session_rows], dtype=np.int64)
		xdf_filename_arr = np.array([r["xdf_filename"] for r in session_rows], dtype=object)
		has_motion_arr = np.array([1 if r["motion"]["has_data"] else 0 for r in session_rows], dtype=np.int8)
		alignment_method_arr = np.array([r["alignment"]["alignment_method"] for r in session_rows], dtype=object)
		spectrogram_start_time_sec_arr = np.array([r["alignment"]["spectrogram_start_time_sec"] for r in session_rows], dtype=np.float64)
		spectrogram_end_time_sec_arr = np.array([r["alignment"]["spectrogram_end_time_sec"] for r in session_rows], dtype=np.float64)
		eeg_time_origin_meas_date_sec_arr = np.array([r["alignment"]["eeg_time_origin_meas_date_sec"] for r in session_rows], dtype=np.float64)
		motion_time_origin_meas_date_sec_arr = np.array([r["alignment"]["motion_time_origin_meas_date_sec"] for r in session_rows], dtype=np.float64)
		eeg_has_nearest_map_arr = np.array([1 if r["alignment"]["eeg_has_nearest_map"] else 0 for r in session_rows], dtype=np.int8)
		motion_has_nearest_map_arr = np.array([1 if r["alignment"]["motion_has_nearest_map"] else 0 for r in session_rows], dtype=np.int8)
		ds = xr.Dataset(
			{
				"spectrogram": (("session", "channel", "freq", "time"), spectrogram_padded),
				"freqs": (("session", "freq"), freqs_padded),
				"times": (("session", "time"), times_padded),
				"meas_date_iso": (("session",), meas_date_iso_arr),
				"meas_date_sec": (("session",), meas_date_sec_arr),
				"sfreq_hz": (("session",), sfreq_hz_arr),
				"duration_s": (("session",), duration_s_arr),
				"n_channels": (("session",), n_channels_arr),
				"xdf_filename": (("session",), xdf_filename_arr),
				"channel_names": (("session", "channel"), channel_names_padded),
				"eeg_raw_data": (("session", "eeg_channel", "eeg_sample"), eeg_raw_data_padded),
				"eeg_raw_times_sec": (("session", "eeg_sample"), eeg_raw_times_padded),
				"eeg_channel_names": (("session", "eeg_channel"), eeg_channel_names_padded),
				"has_motion": (("session",), has_motion_arr),
				"motion_raw_data": (("session", "motion_channel", "motion_sample"), motion_raw_data_padded),
				"motion_raw_times_sec": (("session", "motion_sample"), motion_raw_times_padded),
				"motion_channel_names": (("session", "motion_channel"), motion_channel_names_padded),
				"alignment_method": (("session",), alignment_method_arr),
				"spectrogram_start_time_sec": (("session",), spectrogram_start_time_sec_arr),
				"spectrogram_end_time_sec": (("session",), spectrogram_end_time_sec_arr),
				"eeg_time_origin_meas_date_sec": (("session",), eeg_time_origin_meas_date_sec_arr),
				"motion_time_origin_meas_date_sec": (("session",), motion_time_origin_meas_date_sec_arr),
				"eeg_has_nearest_map": (("session",), eeg_has_nearest_map_arr),
				"motion_has_nearest_map": (("session",), motion_has_nearest_map_arr),
				"eeg_nearest_spectrogram_bin_idx": (("session", "eeg_sample"), eeg_nearest_idx_padded),
				"motion_nearest_spectrogram_bin_idx": (("session", "motion_sample"), motion_nearest_idx_padded),
			},
			coords={"session": np.arange(n_sessions), "channel": np.arange(n_channels), "freq": np.arange(n_freq), "time": np.arange(n_time), "eeg_channel": np.arange(n_eeg_channels), "eeg_sample": np.arange(n_eeg_samples), "motion_channel": np.arange(n_motion_channels), "motion_sample": np.arange(n_motion_samples)},
			attrs={"format_version": "1.1", "freq_min": float(freq_min), "freq_max": float(freq_max), "n_sessions": n_sessions},
		)
		ds.to_netcdf(output_path)
		print(f"Exported spectrograms to NetCDF: {output_path.as_posix()}")
		return output_path


	@classmethod
	def export_spectrograms_parquet(cls, active_only_out_eeg_raws, results, output_path: Path, freq_min: float = 1.0, freq_max: float = 40.0, stream_infos_df: Optional[pd.DataFrame] = None, active_only_out_motion_raws: Optional[List[Optional[mne.io.BaseRaw]]] = None) -> Path:
		"""
		Export spectrograms, timestamps, and recording metadata to a single Parquet file for interchange and analytics.

		One row per session with scalar metadata and list/array columns for channel_names, freqs, times, and
		spectrogram (3D as list of list of list). Readable with pandas/PyArrow in Python, R, and other tools.
		"""
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		dataset_to_filename: Dict[int, str] = {}
		if stream_infos_df is not None:
			try:
				tmp_df = _reset_stream_infos_index_for_groupby(stream_infos_df)
				if "xdf_dataset_idx" in tmp_df.columns and "xdf_filename" in tmp_df.columns:
					for ds_idx, grp in tmp_df.groupby("xdf_dataset_idx"):
						dataset_to_filename[int(ds_idx)] = str(grp["xdf_filename"].iloc[0])
			except Exception as e:
				print(f"  WARN: export_spectrograms_parquet failed to derive dataset -> filename mapping: {e}")
		rows: List[Dict[str, Any]] = []
		for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
			try:
				if a_result is None or "spectogram" not in a_result:
					continue
				meas_date = a_raw.info.get("meas_date")
				if meas_date is None:
					meas_date_sec = float("nan")
					meas_date_iso = ""
				else:
					if getattr(meas_date, "tzinfo", None) is None:
						meas_date = meas_date.replace(tzinfo=timezone.utc)
					meas_date_sec = meas_date.timestamp()
					meas_date_iso = meas_date.isoformat()
				spectogram_result_dict = a_result["spectogram"]["spectogram_result_dict"]
				channel_names = list(spectogram_result_dict.keys())
				f0, t0, Sxx0 = next(iter(spectogram_result_dict.values()))
				freq_mask = (f0 >= freq_min) & (f0 <= freq_max)
				freqs = np.asarray(f0)[freq_mask]
				times = np.asarray(t0)
				Sxx_list = []
				for ch_name in channel_names:
					f, t, Sxx = spectogram_result_dict[ch_name]
					Sxx_list.append(np.asarray(Sxx)[freq_mask, :])
				Sxx_stack = np.stack(Sxx_list, axis=0)
				eeg_payload = cls._extract_raw_export_payload(a_raw)
				motion_raw = active_only_out_motion_raws[idx] if (active_only_out_motion_raws is not None and idx < len(active_only_out_motion_raws)) else None
				motion_payload = cls._extract_raw_export_payload(motion_raw)
				alignment_payload = cls._build_alignment_export_payload(times, eeg_payload, motion_payload)
				try:
					duration_s = float(a_raw.times[-1] - a_raw.times[0]) if a_raw.times.size > 0 else float("nan")
				except Exception:
					duration_s = float("nan")
				sfreq_hz = float(a_raw.info.get("sfreq", float("nan")))
				xdf_filename = dataset_to_filename.get(idx)
				spectrogram_nested = Sxx_stack.tolist()
				rows.append({"session_idx": idx, "meas_date_iso": meas_date_iso, "meas_date_sec": meas_date_sec, "xdf_filename": str(xdf_filename) if xdf_filename is not None else "", "sfreq_hz": sfreq_hz, "duration_s": duration_s, "n_channels": len(channel_names), "channel_names": channel_names, "freqs": freqs.tolist(), "times": times.tolist(), "spectrogram": spectrogram_nested, "eeg_raw_data": eeg_payload["raw_data"].tolist(), "eeg_raw_times_sec": eeg_payload["raw_times_sec"].tolist(), "eeg_channel_names": eeg_payload["channel_names"], "has_motion": bool(motion_payload["has_data"]), "motion_raw_data": motion_payload["raw_data"].tolist(), "motion_raw_times_sec": motion_payload["raw_times_sec"].tolist(), "motion_channel_names": motion_payload["channel_names"], "alignment_method": alignment_payload["alignment_method"], "spectrogram_times_sec": alignment_payload["spectrogram_times_sec"].tolist(), "spectrogram_start_time_sec": float(alignment_payload["spectrogram_start_time_sec"]), "spectrogram_end_time_sec": float(alignment_payload["spectrogram_end_time_sec"]), "eeg_time_origin_meas_date_sec": float(alignment_payload["eeg_time_origin_meas_date_sec"]), "motion_time_origin_meas_date_sec": float(alignment_payload["motion_time_origin_meas_date_sec"]), "eeg_has_nearest_map": bool(alignment_payload["eeg_has_nearest_map"]), "motion_has_nearest_map": bool(alignment_payload["motion_has_nearest_map"]), "eeg_nearest_spectrogram_bin_idx": alignment_payload["eeg_nearest_spectrogram_bin_idx"].tolist(), "motion_nearest_spectrogram_bin_idx": alignment_payload["motion_nearest_spectrogram_bin_idx"].tolist()})
			except Exception as e:
				print(f"  WARN: export_spectrograms_parquet skipped session {idx}: {e}")
				continue
		df = pd.DataFrame.from_records(rows)
		df.to_parquet(output_path, index=False)
		print(f"Exported spectrograms to Parquet: {output_path.as_posix()}")
		return output_path


	@classmethod
	def compute_session_summary_metrics(cls, active_only_out_eeg_raws, results, stream_infos_df: Optional[pd.DataFrame], output_folder: Path, freq_min: float = 1.0, freq_max: float = 40.0, filename_prefix: str = "") -> Path:
		"""
		Compute simple per-session summary metrics from the spectrogram outputs and save to CSV.

		Metrics are intended to support quick comparison across sessions and include:
		  - Duration, sampling rate, number of channels
		  - Bandpower (absolute and relative) for classical bands (delta/theta/alpha/beta)
		  - Dominant frequency within [freq_min, freq_max]
		  - EEG manifold (PCA) trajectory scalars when ``eeg_manifold`` is present in results

		Returns:
			Path to the created CSV file.
		"""
		output_folder = Path(output_folder)
		output_folder.mkdir(parents=True, exist_ok=True)

		rows = []

		# Precompute simple lookup from dataset index -> xdf_filename (if available)
		dataset_to_filename: Dict[int, str] = {}
		if stream_infos_df is not None:
			try:
				tmp_df = _reset_stream_infos_index_for_groupby(stream_infos_df)
				if "xdf_dataset_idx" in tmp_df.columns and "xdf_filename" in tmp_df.columns:
					for ds_idx, grp in tmp_df.groupby("xdf_dataset_idx"):
						# Use the first filename for this dataset index
						dataset_to_filename[int(ds_idx)] = str(grp["xdf_filename"].iloc[0])
					## END for ds_idx, grp in tmp_df.groupby("xdf_dataset_idx")...
			except Exception as e:
				print(f"  WARN: failed to derive dataset -> filename mapping: {e}")

		def _band_mean(Sxx_vals: np.ndarray, freqs_vals: np.ndarray, low: float, high: float) -> float:
			band_mask = (freqs_vals >= low) & (freqs_vals < high)
			if not np.any(band_mask):
				return float("nan")
			return float(np.nanmean(Sxx_vals[:, band_mask]))

		def _manifold_summary_scalars(manifold_result) -> Dict[str, float]:
			"""Extract per-session PC trajectory scalars from an ``eeg_manifold`` result dict."""
			out: Dict[str, float] = {
				"manifold_n_windows": float("nan"),
				"manifold_pc1_std": float("nan"),
				"manifold_pc2_std": float("nan"),
				"manifold_pc3_std": float("nan"),
				"manifold_pc1_range": float("nan"),
				"manifold_pc2_range": float("nan"),
				"manifold_pc3_range": float("nan"),
				"manifold_traj_path_length": float("nan"),
				"manifold_traj_speed_rms": float("nan"),
			}
			if not isinstance(manifold_result, dict):
				return out
			try:
				embedding = np.asarray(manifold_result.get("embedding"), dtype=float)
				times = np.asarray(manifold_result.get("times"), dtype=float)
				n_windows = int(manifold_result.get("n_windows", embedding.shape[0] if embedding.ndim == 2 else 0))
				out["manifold_n_windows"] = float(n_windows)
				if embedding.ndim != 2 or embedding.shape[0] == 0:
					return out
				n_comp = min(3, embedding.shape[1])
				for i in range(n_comp):
					col = embedding[:, i]
					finite = col[np.isfinite(col)]
					if finite.size == 0:
						continue
					out[f"manifold_pc{i + 1}_std"] = float(np.nanstd(finite))
					out[f"manifold_pc{i + 1}_range"] = float(np.nanmax(finite) - np.nanmin(finite))
				## END for i in range(n_comp)....
				if embedding.shape[0] >= 2:
					diffs = np.diff(embedding, axis=0)
					step_lengths = np.linalg.norm(diffs, axis=1)
					finite_steps = step_lengths[np.isfinite(step_lengths)]
					if finite_steps.size > 0:
						out["manifold_traj_path_length"] = float(np.nansum(finite_steps))
					if times.size == embedding.shape[0]:
						dt = np.diff(times)
						with np.errstate(divide="ignore", invalid="ignore"):
							speeds = step_lengths / dt
						finite_speeds = speeds[np.isfinite(speeds)]
						if finite_speeds.size > 0:
							out["manifold_traj_speed_rms"] = float(np.sqrt(np.nanmean(np.square(finite_speeds))))
			except Exception:
				return out
			return out

		for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results)):
			if a_result is None:
				continue
			if "spectogram" not in a_result:
				continue

			try:
				spect_dict = a_result["spectogram"]
				Sxx_avg = spect_dict["Sxx_avg"]  # xarray.DataArray: (channels, freqs)
				freqs_vals = np.asarray(Sxx_avg.coords["freqs"])
				Sxx_vals = np.asarray(Sxx_avg)  # (n_channels, n_freqs)
			except Exception as e:
				print(f"  WARN: failed to unpack spectrogram for session {idx}: {e}")
				continue

			# Restrict to the main comparison band for global metrics
			main_band_mask = (freqs_vals >= freq_min) & (freqs_vals <= freq_max)
			if not np.any(main_band_mask):
				print(f"  WARN: no frequencies within [{freq_min}, {freq_max}] Hz for session {idx}")
				continue

			Sxx_main = Sxx_vals[:, main_band_mask]
			freqs_main = freqs_vals[main_band_mask]

			# Total power in [freq_min, freq_max]
			total_power = float(np.nanmean(Sxx_main))

			# Classical bands
			delta_power = _band_mean(Sxx_vals, freqs_vals, 1.0, 4.0)
			theta_power = _band_mean(Sxx_vals, freqs_vals, 4.0, 8.0)
			alpha_power = _band_mean(Sxx_vals, freqs_vals, 8.0, 13.0)
			beta_power = _band_mean(Sxx_vals, freqs_vals, 13.0, 30.0)

			# Relative powers and simple ratios
			def _safe_div(n: float, d: float) -> float:
				return float(n / d) if np.isfinite(n) and np.isfinite(d) and d != 0 else float("nan")

			delta_rel = _safe_div(delta_power, total_power)
			theta_rel = _safe_div(theta_power, total_power)
			alpha_rel = _safe_div(alpha_power, total_power)
			beta_rel = _safe_div(beta_power, total_power)

			alpha_theta_ratio = _safe_div(alpha_power, theta_power)
			beta_alpha_ratio = _safe_div(beta_power, alpha_power)

			# Dominant frequency within [freq_min, freq_max] (global across channels)
			try:
				global_psd = np.nanmean(Sxx_main, axis=0)  # (n_freqs_main,)
				peak_idx = int(np.nanargmax(global_psd))
				dominant_freq_hz = float(freqs_main[peak_idx])
			except Exception:
				dominant_freq_hz = float("nan")

			# Basic session info
			meas_date = a_raw.info.get("meas_date")
			if isinstance(meas_date, datetime):
				meas_date_str = meas_date.strftime("%Y-%m-%d %H:%M:%S")
			else:
				meas_date_str = str(meas_date)

			try:
				times = a_raw.times
				duration_s = float(times[-1] - times[0]) if times.size > 0 else float("nan")
			except Exception:
				duration_s = float("nan")

			sfreq = float(a_raw.info.get("sfreq", float("nan")))
			n_channels = len(a_raw.info.get("ch_names", []))
			xdf_filename = dataset_to_filename.get(idx, None)
			manifold_scalars = _manifold_summary_scalars(a_result.get("eeg_manifold"))

			rows.append(
				{
					"session_idx": idx,
					"xdf_filename": xdf_filename,
					"meas_date": meas_date_str,
					"duration_s": duration_s,
					"sfreq_hz": sfreq,
					"n_channels": n_channels,
					f"total_power_{freq_min:.1f}_to_{freq_max:.1f}_hz": total_power,
					"delta_power": delta_power,
					"theta_power": theta_power,
					"alpha_power": alpha_power,
					"beta_power": beta_power,
					"delta_rel": delta_rel,
					"theta_rel": theta_rel,
					"alpha_rel": alpha_rel,
					"beta_rel": beta_rel,
					"alpha_theta_ratio": alpha_theta_ratio,
					"beta_alpha_ratio": beta_alpha_ratio,
					"dominant_freq_hz": dominant_freq_hz,
					**manifold_scalars,
				}
			)
		## END for idx, (a_raw, a_result) in enumerate(zip(active_only_out_eeg_raws, results))...

		if not rows:
			print("No valid spectrogram-based metrics were computed; skipping CSV export.")
			return output_folder.joinpath(f"{filename_prefix}session_summaries_empty.csv")

		metrics_df = pd.DataFrame.from_records(rows)
		csv_path = output_folder.joinpath(f"{filename_prefix}session_summaries.csv")
		metrics_df.to_csv(csv_path, index=False)

		print(f"\nSaved per-session summary metrics to: {csv_path.as_posix()}")
		return csv_path


	@classmethod
	def process_XDFs_main(cls, n_most_recent_sessions_to_preprocess: Optional[int] = 5, should_write_final_merged_eeg_fif: bool = True, included_xdf_file_names: Optional[List]=None, included_xdf_paths: Optional[List[Path]] = None, should_load_preprocessed: bool = False, use_computation_cache: bool = True, cache_root: Optional[Path] = None, use_mtime_in_cache_key: bool = False, use_graph_computation_cache: bool = False, absolute_max_workers: int = 2, session_catalog: Optional[SessionCatalog] = None, use_session_catalog: bool = True, retain_in_memory: bool = True, session_sink: Optional[Callable[[int, Any, Any, Any, Any], None]] = None, progress_callback: Optional[Callable[..., None]] = None, cancel_event: Optional[threading.Event] = None):
		""" Processes a single .xdf file independently to produce all exports

			from PhoOfflineEEGAnalysis.main_analyze_run import SpectogramPlottingHelper
			SpectogramPlottingHelper.process_XDFs_main(...)

			If *use_graph_computation_cache* is True (and *use_computation_cache* is True), EEG steps run via the phopymnehelper DAG executor with per-node disk cache under ``cache_root / "node_cache"``; the usual monolithic session pickle and lookup are unchanged.

			When *use_session_catalog* is True, discovery and per-file processing status are upserted into a SQLite session catalog (default under ``pickled_data_path/session_catalog``).

			*retain_in_memory* (default True) keeps every session's Raw + computation result in the returned lists (batch / notebook behavior). Set False for streaming Analyze Run: after optional *session_sink(idx, eeg_raw, motion_raw, stream_infos, result)*, heavy objects are dropped and empty Raw/result lists are returned (stream_infos_df is still populated).

			*progress_callback* may be called as ``progress_callback(message, current=i, total=n, active_sessions=[...])``.
			*cancel_event*: when set, pending (not-yet-started) futures are cancelled; in-flight sessions finish, then the batch returns early.

		"""
		if cache_root is None:
			cache_root = pickled_data_path / "computation_cache"
		cache_root = Path(cache_root)
		disk_node_cache: Optional[DiskComputationCache] = DiskComputationCache(cache_root / "node_cache") if use_computation_cache else None
		run_all_params: Dict[str, Any] = {"mask_bad_annotated_times": False}
		print(f"Computation node cache: root={(cache_root / 'node_cache').resolve().as_posix()}, enabled={disk_node_cache is not None}")

		# SavedSessionProcessor

		sso: SavedSessionsProcessor = SavedSessionsProcessor(eeg_recordings_file_path=eeg_recordings_file_path,
															headset_motion_recordings_file_path=headset_motion_recordings_file_path, WhisperVideoTranscripts_LSL_Converted_file_path=WhisperVideoTranscripts_LSL_Converted, pho_log_to_LSL_recordings_path=pho_log_to_LSL_recordings_path,
															eeg_analyzed_parent_export_path=eeg_analyzed_parent_export_path, 
															n_most_recent_sessions_to_preprocess=n_most_recent_sessions_to_preprocess, 
															should_load_data=True, should_load_preprocessed=should_load_preprocessed,
															#  should_load_data=True, should_load_preprocessed=True,
															)


		labRecorder_PostProcessed_path: Path = sso.eeg_analyzed_parent_export_path.joinpath(f'LabRecorder_PostProcessed')
		labRecorder_PostProcessed_path.mkdir(exist_ok=True)



		# Parallel XDF file processing
		if included_xdf_paths is not None:
			lab_recorder_xdf_files: list[Path] = [Path(p) for p in included_xdf_paths if Path(p).is_file()]
			n_total_found_files: int = len(lab_recorder_xdf_files)
		else:
			assert lab_recorder_output_path.exists()
			lab_recorder_xdf_files = list(lab_recorder_output_path.glob('*.xdf'))
			n_total_found_files = len(lab_recorder_xdf_files)

		# Filter by included file names if specified
		if included_xdf_file_names is not None and included_xdf_paths is None:
			print(f'limiting to included_xdf_file_names: {included_xdf_file_names}...')
			lab_recorder_xdf_files = [v for v in lab_recorder_xdf_files if v.name in included_xdf_file_names]
			n_filtered_found_files: int = len(lab_recorder_xdf_files)
			print(f'\tlimited to {n_filtered_found_files}/{n_total_found_files} files')

		# Limit to n_most_recent_sessions_to_preprocess most recent files
		if n_most_recent_sessions_to_preprocess is not None and len(lab_recorder_xdf_files) > n_most_recent_sessions_to_preprocess:
			print(f'Limiting to {n_most_recent_sessions_to_preprocess} most recent XDF files...')
			# Sort by modification time (most recent first)
			lab_recorder_xdf_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
			lab_recorder_xdf_files = lab_recorder_xdf_files[:n_most_recent_sessions_to_preprocess]
			print(f'\tLimited to {len(lab_recorder_xdf_files)}/{n_total_found_files} most recent files')

		if (labRecorder_PostProcessed_path is not None) and should_write_final_merged_eeg_fif:
			labRecorder_PostProcessed_path.mkdir(exist_ok=True)

		catalog: Optional[SessionCatalog] = session_catalog
		if use_session_catalog and catalog is None:
			catalog = get_or_create_catalog(pickled_data_path=pickled_data_path)
		if catalog is not None:
			if included_xdf_paths is not None:
				parent_dirs = sorted({p.parent for p in lab_recorder_xdf_files if p.parent.exists()}, key=lambda d: d.as_posix())
				n_discovered = catalog.discover_and_upsert(parent_dirs, recordings_extensions=['.xdf']) if parent_dirs else 0
				print(f"Session catalog: {catalog.db_path.as_posix()} (discovered/upserted {n_discovered} XDF files from explicit target parent(s))")
			elif lab_recorder_output_path.exists():
				n_discovered = catalog.discover_and_upsert(lab_recorder_output_path, recordings_extensions=['.xdf'])
				print(f"Session catalog: {catalog.db_path.as_posix()} (discovered/upserted {n_discovered} XDF files under {lab_recorder_output_path.as_posix()})")

		# Determine optimal number of workers
		cpu_count: int = (os.cpu_count() or 2)
		absolute_max_workers = min(absolute_max_workers, cpu_count)
		max_workers = max(1, min(len(lab_recorder_xdf_files), absolute_max_workers))
		n_files_to_process: int = len(lab_recorder_xdf_files)
		print(f"Processing {n_files_to_process} XDF files using {max_workers} parallel workers...")

		def _emit_progress(message: str, *, current: Optional[int] = None, total: Optional[int] = None, active_sessions: Optional[List[str]] = None) -> None:
			print(message)
			if progress_callback is None:
				return
			kwargs: Dict[str, Any] = {}
			if current is not None:
				kwargs["current"] = current
			if total is not None:
				kwargs["total"] = total
			if active_sessions is not None:
				kwargs["active_sessions"] = list(active_sessions)
			try:
				progress_callback(message, **kwargs)
			except TypeError:
				progress_callback(message)

		def _is_cancelled() -> bool:
			return cancel_event is not None and cancel_event.is_set()

		_emit_progress(f"Processing {n_files_to_process} XDF files using {max_workers} parallel workers...", current=0, total=n_files_to_process, active_sessions=[])

		# Initialize result containers
		_out_eeg_raw = [None] * n_files_to_process
		_out_motion_raw = [None] * n_files_to_process
		_out_xdf_stream_infos_df = [None] * n_files_to_process
		_out_results = [None] * n_files_to_process
		_active_session_names: Set[str] = set()
		_active_sessions_lock = threading.Lock()
		_completed_count = 0
		_completed_lock = threading.Lock()

		def _subfn_process_single_xdf_file(idx_file_tuple):
			"""Process a single XDF file - captures lots of things 
			"""
			nonlocal _completed_count
			an_xdf_file_idx, a_xdf_file = idx_file_tuple
			if _is_cancelled():
				with _completed_lock:
					_completed_count += 1
					done_so_far = _completed_count
				_emit_progress(f'Cancelled before start: "{a_xdf_file.name}"', current=done_so_far, total=n_files_to_process, active_sessions=sorted(_active_session_names))
				if catalog is not None:
					catalog.mark_failed(a_xdf_file, error="cancelled")
				return an_xdf_file_idx, None, None, None, None
			try:
				session_label = a_xdf_file.name
				with _active_sessions_lock:
					_active_session_names.add(session_label)
					active_snapshot = sorted(_active_session_names)
				with _completed_lock:
					done_so_far = _completed_count
				_emit_progress(f'Processing XDF file {an_xdf_file_idx+1}/{n_files_to_process}: "{session_label}"...', current=done_so_far, total=n_files_to_process, active_sessions=active_snapshot)
				if catalog is not None:
					catalog.mark_computing(a_xdf_file)

				# Load XDF file
				_obj = LabRecorderXDF.init_from_lab_recorder_xdf_file(a_xdf_file=a_xdf_file)
				stream_infos = _obj.stream_infos
				raws = _obj.datasets
				raws_dict = _obj.datasets_dict

				eeg_raws = raws_dict.get(DataModalityType.EEG.value, [])

				def _finish_skipped(reason: str):
					nonlocal _completed_count
					with _active_sessions_lock:
						_active_session_names.discard(session_label)
						active_snapshot = sorted(_active_session_names)
					with _completed_lock:
						_completed_count += 1
						done_so_far = _completed_count
					_emit_progress(f'Skipped "{session_label}": {reason}', current=done_so_far, total=n_files_to_process, active_sessions=active_snapshot)
					if catalog is not None:
						catalog.mark_failed(a_xdf_file, error=reason)
					return an_xdf_file_idx, None, None, None, None

				if len(eeg_raws) == 0:
					return _finish_skipped("no EEG streams found")

				# Merge by device so we can handle multiple EEG streams per XDF
				merged_eeg_raws, merge_meta = LabRecorderXDF.merge_eeg_streams_by_device(
					eeg_raws=eeg_raws, strict_merge=False, debug_print=False
				)
				if len(merged_eeg_raws) == 0:
					return _finish_skipped("could not produce merged EEG datasets")

				# Save post-processed data if requested (one set per merged dataset)
				exports_dict = None
				fif_export_path = None
				mat_export_path = None
				if should_write_final_merged_eeg_fif:
					_, exports_dict = LabRecorderXDF.save_post_processed_to_fif(
						raws_dict=raws_dict,
						a_xdf_file=a_xdf_file,
						labRecorder_PostProcessed_path=labRecorder_PostProcessed_path,
					)

				# For the notebook path we currently return only the first merged EEG
				# dataset for downstream plotting, but we still respect multi-stream
				# handling for disk outputs.
				eeg_raw = merged_eeg_raws[0]
				motion_raws = raws_dict.get(DataModalityType.MOTION.value, [])
				motion_raw = motion_raws[0] if len(motion_raws) > 0 else None

				# Add metadata
				stream_infos['lab_recorder_xdf_file_idx'] = an_xdf_file_idx
				stream_infos['xdf_filename'] = a_xdf_file.name
				stream_infos['eeg_device_group_idx'] = 0
				stream_infos['eeg_device_key'] = merge_meta[0].get('device_key', 'device_0')
				stream_infos['n_eeg_segments_in_group'] = merge_meta[0].get('n_segments', 1)

				if exports_dict is not None:
					for a_format, per_idx_dict in exports_dict.items():
						export_path = per_idx_dict.get(0, None)
						if export_path is not None:
							# Ensure we handle both Path and string-like values
							stream_infos[f'proccessed_{a_format}_filename'] = Path(export_path).name
							if a_format == 'fif':
								fif_export_path = Path(export_path)
							elif a_format == 'mat':
								mat_export_path = Path(export_path)
					## END for a_format, per_idx_dict in exports_dict.items()...

				# Up-convert and set montage
				eeg_raw = MNEHelpers.up_convert_raw_obj(eeg_raw)
				if motion_raw is not None:
					motion_raw = MNEHelpers.up_convert_raw_obj(motion_raw)

				EEGData.set_montage(datasets_EEG=[eeg_raw])
				eeg_raw.debug_test_annotations_timestamps()

				meas_date = eeg_raw.info.get('meas_date', None)
				try:
					duration_sec = float(eeg_raw.n_times) / float(eeg_raw.info['sfreq']) if eeg_raw.info.get('sfreq') else None
				except Exception:
					duration_sec = None

				if catalog is not None and (fif_export_path is not None or mat_export_path is not None):
					catalog.mark_exported(a_xdf_file, fif_path=fif_export_path, mat_path=mat_export_path, recording_datetime=meas_date if isinstance(meas_date, datetime) else None, duration_sec=duration_sec)

				# Do post-processing stage
				result = None
				try:
					print(f"  Processing merged EEG dataset for file {an_xdf_file_idx+1}/{len(lab_recorder_xdf_files)} (meas_date: {meas_date})")
					xdf_mtime_for_fp = a_xdf_file.stat().st_mtime
					session_fp = EEGComputationRegistryHelper.session_fingerprint_for_raw_or_path(eeg_raw, path=a_xdf_file, mtime=xdf_mtime_for_fp if use_mtime_in_cache_key else None)
					extras = {}
					if isinstance(meas_date, datetime):
						md = meas_date if meas_date.tzinfo is not None else meas_date.replace(tzinfo=timezone.utc)
						extras["session_t_start_unix"] = float(md.timestamp())
					result = EEGComputationRegistryHelper.run_eeg_graph_legacy_ordered(raw=eeg_raw, session=session_fp, global_params=run_all_params, cache=disk_node_cache, use_cache=True, parallel=False, extras=extras)
					print(f"  Completed merged EEG dataset for file {an_xdf_file_idx+1}/{len(lab_recorder_xdf_files)} (meas_date: {meas_date})")
					if catalog is not None and result is not None:
						# Prefer exported if artifacts were written; otherwise computed
						if fif_export_path is not None or mat_export_path is not None:
							catalog.mark_exported(a_xdf_file, fif_path=fif_export_path, mat_path=mat_export_path, recording_datetime=meas_date if isinstance(meas_date, datetime) else None, duration_sec=duration_sec)
						else:
							catalog.mark_computed(a_xdf_file, recording_datetime=meas_date if isinstance(meas_date, datetime) else None, duration_sec=duration_sec, params_json=run_all_params)
					elif catalog is not None and result is None:
						catalog.mark_failed(a_xdf_file, error="EEG graph returned no result")
				except Exception as e:
					print(f"  ERROR processing merged EEG dataset for file {an_xdf_file_idx+1}: {e}")
					if catalog is not None:
						catalog.mark_failed(a_xdf_file, error=str(e))

				if isinstance(meas_date, datetime):
					stream_infos["recording_datetime"] = meas_date
					stream_infos["recording_day_date"] = meas_date.replace(hour=0, minute=0, second=0, microsecond=0)
				if duration_sec is not None:
					stream_infos["eeg_duration_sec"] = float(duration_sec)

				# Drop unused XDF holder / modality lists so only returned Raw refs keep signal data alive
				try:
					del _obj, raws, raws_dict, eeg_raws, merged_eeg_raws, motion_raws
				except Exception:
					pass

				with _active_sessions_lock:
					_active_session_names.discard(session_label)
					active_snapshot = sorted(_active_session_names)
				with _completed_lock:
					_completed_count += 1
					done_so_far = _completed_count
				_emit_progress(f'Completed XDF file {an_xdf_file_idx+1}/{n_files_to_process}: "{session_label}"', current=done_so_far, total=n_files_to_process, active_sessions=active_snapshot)
				return an_xdf_file_idx, eeg_raw, motion_raw, stream_infos, result

			except (ValueError, KeyError, AssertionError, TypeError) as e:
				with _active_sessions_lock:
					_active_session_names.discard(a_xdf_file.name)
					active_snapshot = sorted(_active_session_names)
				with _completed_lock:
					_completed_count += 1
					done_so_far = _completed_count
				_emit_progress(f'ERROR in XDF file {an_xdf_file_idx+1}: {e} (skipping)', current=done_so_far, total=n_files_to_process, active_sessions=active_snapshot)
				if catalog is not None:
					catalog.mark_failed(a_xdf_file, error=str(e))
				return an_xdf_file_idx, None, None, None, None

			except Exception as e:
				with _active_sessions_lock:
					_active_session_names.discard(a_xdf_file.name)
				print(f'  EXCEPTION in XDF file {an_xdf_file_idx+1}: {e}')
				if catalog is not None:
					catalog.mark_failed(a_xdf_file, error=str(e))
				raise


		# ---------------------------------------------------------------------------- #
		#                 Parallel processing using ThreadPoolExecutor                 #
		# ---------------------------------------------------------------------------- #
		completed_sessions = 0
		was_cancelled = False
		with ThreadPoolExecutor(max_workers=max_workers) as executor:
			# Submit all tasks
			future_to_idx = {
				executor.submit(_subfn_process_single_xdf_file, (idx, xdf_file)): (idx, xdf_file)
				for idx, xdf_file in enumerate(lab_recorder_xdf_files)
			}

			# Collect results as they complete
			for future in as_completed(future_to_idx):
				if _is_cancelled() and not was_cancelled:
					was_cancelled = True
					_emit_progress("Cancel requested — finishing in-flight sessions and skipping pending...", current=_completed_count, total=n_files_to_process, active_sessions=sorted(_active_session_names))
					for pending_future, (pending_idx, pending_path) in future_to_idx.items():
						if pending_future.done():
							continue
						if pending_future.cancel():
							if catalog is not None:
								catalog.mark_failed(pending_path, error="cancelled")
							_out_eeg_raw[pending_idx] = None
							_out_motion_raw[pending_idx] = None
							_out_xdf_stream_infos_df[pending_idx] = None
							_out_results[pending_idx] = None
					## END for pending_future, (pending_idx, pending_path) in future_to_idx.items()...

				try:
					if future.cancelled():
						continue
					idx, eeg_raw, motion_raw, stream_infos, result = future.result()
					_out_xdf_stream_infos_df[idx] = stream_infos
					if session_sink is not None and eeg_raw is not None and stream_infos is not None:
						try:
							session_sink(idx, eeg_raw, motion_raw, stream_infos, result)
						except Exception as sink_err:
							print(f"  WARN: session_sink failed for file {idx+1}: {sink_err}")
					if retain_in_memory:
						_out_eeg_raw[idx] = eeg_raw
						_out_motion_raw[idx] = motion_raw
						_out_results[idx] = result
					else:
						_out_eeg_raw[idx] = None
						_out_motion_raw[idx] = None
						_out_results[idx] = None
						eeg_raw = None
						motion_raw = None
						result = None
						completed_sessions += 1
						if completed_sessions % 4 == 0:
							gc.collect()
				except Exception as e:
					idx, _xdf_path = future_to_idx[future]
					print(f"  EXCEPTION collecting result for file {idx+1}: {e}")
					_out_eeg_raw[idx] = None
					_out_motion_raw[idx] = None
					_out_xdf_stream_infos_df[idx] = None
					_out_results[idx] = None
			## END for future in as_completed(future_to_idx)...

		if was_cancelled:
			_emit_progress(f"Cancelled after {_completed_count}/{n_files_to_process} session(s).", current=_completed_count, total=n_files_to_process, active_sessions=[])
		else:
			_emit_progress(f"Finished processing {_completed_count}/{n_files_to_process} session(s).", current=_completed_count, total=n_files_to_process, active_sessions=[])

		# Filter out failed files (valid = have stream_infos; Raw required only when retaining)
		if retain_in_memory:
			valid_indices = [i for i, (raw, info) in enumerate(zip(_out_eeg_raw, _out_xdf_stream_infos_df)) if raw is not None and info is not None]
		else:
			valid_indices = [i for i, info in enumerate(_out_xdf_stream_infos_df) if info is not None]
		_out_eeg_raw = [_out_eeg_raw[i] for i in valid_indices]
		_out_motion_raw = [_out_motion_raw[i] for i in valid_indices]
		_out_xdf_stream_infos_df = [_out_xdf_stream_infos_df[i] for i in valid_indices]
		_out_results = [_out_results[i] for i in valid_indices]

		# Add xdf_dataset_idx to stream_infos
		for dataset_idx, stream_infos in enumerate(_out_xdf_stream_infos_df):
			stream_infos['xdf_dataset_idx'] = dataset_idx
		## END for dataset_idx, stream_infos in enumerate(_out_xdf_stream_infos_df)...

		if len(_out_xdf_stream_infos_df) == 0:
			print("Processed 0 sessions with EEG computations")
			empty_df = pd.DataFrame()
			return sso, np.array([], dtype=int), empty_df, [], [], []

		# Combine stream infos
		_out_xdf_stream_infos_df = pd.concat(_out_xdf_stream_infos_df)
		_out_xdf_stream_infos_df = _out_xdf_stream_infos_df.set_index('xdf_dataset_idx')

		if retain_in_memory:
			# Up-convert and sort (with results following the same order)
			_out_eeg_raw = MNEHelpers.up_convert_raw_objects(_out_eeg_raw)
			# Create sorting indices
			sort_indices = sorted(range(len(_out_eeg_raw)), key=lambda i: (_out_eeg_raw[i].raw_timerange()[0] is None, _out_eeg_raw[i].raw_timerange()[0]))
			_out_eeg_raw = [_out_eeg_raw[i] for i in sort_indices]
			_out_motion_raw = [_out_motion_raw[i] for i in sort_indices]
			_out_results = [_out_results[i] for i in sort_indices]

			# Set montage for all datasets
			EEGData.set_montage(datasets_EEG=_out_eeg_raw)  # pyright: ignore[reportUnknownMemberType]

			xdf_dataset_indicies = np.unique(deepcopy(_out_xdf_stream_infos_df).reset_index(drop=False, inplace=False)['xdf_dataset_idx'].to_numpy())
			n_unique_xdf_datasets: int = len(xdf_dataset_indicies)
			print(f'n_unique_xdf_datasets: {n_unique_xdf_datasets}')

			_out_xdf_stream_infos_df = XDFDataStreamAccessor.init_from_results(_out_xdf_stream_infos_df=_out_xdf_stream_infos_df, active_only_out_eeg_raws=_out_eeg_raw)

			active_only_out_eeg_raws = _out_eeg_raw
			active_only_out_motion_raws = _out_motion_raw
			results = _out_results
		else:
			# Streaming: no Raw/result retention; stream_infos already carry recording_datetime / eeg_duration_sec
			xdf_dataset_indicies = np.unique(deepcopy(_out_xdf_stream_infos_df).reset_index(drop=False, inplace=False)['xdf_dataset_idx'].to_numpy())
			n_unique_xdf_datasets: int = len(xdf_dataset_indicies)
			print(f'n_unique_xdf_datasets: {n_unique_xdf_datasets} (streaming; Raw/results not retained)')
			active_only_out_eeg_raws = []
			active_only_out_motion_raws = []
			results = []
			gc.collect()

		num_sessions: int = n_unique_xdf_datasets if not retain_in_memory else len(results)
		print(f'Processed {num_sessions} sessions with EEG computations')

		## OUTPUTS: _out_xdf_stream_infos_df, active_only_out_eeg_raws, active_only_out_motion_raws, results, 

		return sso, xdf_dataset_indicies, _out_xdf_stream_infos_df, active_only_out_eeg_raws, active_only_out_motion_raws, results


# Configuration
mne.viz.set_browser_backend("qt")
mne.set_config("MNE_BROWSER_BACKEND", "qt")
set_log_level("WARNING")

hv.extension('bokeh', logo=False)
hvplot.extension('bokeh')
pn.extension()

# Jupyter-lab enable printing for any line on its own (instead of just the last one in the cell)
# InteractiveShell.ast_node_interactivity = "all"

# Initialize datasets
datasets = []


# db_root_path = Path('/content/drive/MyDrive/Databases')
db_root_path = Path('E:/Dropbox (Personal)/Databases')
assert db_root_path.exists(), f"'{db_root_path.as_posix()}' does not exist!"

# eeg_recordings_file_path: Path = Path(r'E:/Dropbox (Personal)/Databases/UnparsedData/EmotivEpocX_EEGRecordings/fif')
# headset_motion_recordings_file_path: Path = Path(r'E:/Dropbox (Personal)/Databases/UnparsedData/EmotivEpocX_EEGRecordings/MOTION_RECORDINGS/fif')

# assert eeg_recordings_file_path.exists()
# assert headset_motion_recordings_file_path.exists()

eeg_recordings_file_path: Path = db_root_path.joinpath('UnparsedData/EmotivEpocX_EEGRecordings/fif')
flutter_eeg_recordings_file_path: Path = db_root_path.joinpath('UnparsedData/EmotivEEG_FlutterRecordings')
flutter_motion_recordings_file_path: Path = db_root_path.joinpath('UnparsedData/EmotivEEG_FlutterRecordings/MOTION_RECORDINGS')
flutter_GENERIC_recordings_file_path: Path = db_root_path.joinpath('UnparsedData/EmotivEEG_FlutterRecordings/GENERIC_RECORDINGS')

headset_motion_recordings_file_path: Path = db_root_path.joinpath('UnparsedData/EmotivEpocX_EEGRecordings/MOTION_RECORDINGS/fif')
WhisperVideoTranscripts_LSL_Converted = db_root_path.joinpath('UnparsedData/WhisperVideoTranscripts_LSL_Converted')
pho_log_to_LSL_recordings_path: Path = db_root_path.joinpath('UnparsedData/PhoLogToLabStreamingLayer_logs')
## These contain little LSL .fif files with names like: '20250808_062814_log.fif',

eeg_analyzed_parent_export_path = db_root_path.joinpath('AnalysisData/MNE_preprocessed')
pickled_data_path = db_root_path.joinpath('AnalysisData/MNE_preprocessed/PICKLED_COLLECTION')
pickled_data_path.mkdir(exist_ok=True)
assert pickled_data_path.exists(), f"'{pickled_data_path.as_posix()}' does not exist!"


# outputs_root_folder: Path = Path('L:/AITEMP/PhoOfflineEEGAnalysisOutputs')
outputs_root_folder: Path = Path('C:/AITEMP/PhoOfflineEEGAnalysisOutputs')
assert outputs_root_folder.exists(), f"'{outputs_root_folder.as_posix()}' does not exist!"

lab_recorder_output_path = Path("E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001")
assert lab_recorder_output_path.exists(), f"'{lab_recorder_output_path.as_posix()}' does not exist!"





# xdf_stream_infos_df: pd.DataFrame = XDFDataStreamAccessor.init_from_results(_out_xdf_stream_infos_df=_out_xdf_stream_infos_df, active_only_out_eeg_raws=active_only_out_eeg_raws)
# xdf_stream_infos_df


if __name__ == "__main__":

	# n_most_recent_sessions_to_preprocess: int = None # None means all sessions
	n_most_recent_sessions_to_preprocess: int = 55
	# n_most_recent_sessions_to_preprocess: int = 15
	# n_most_recent_sessions_to_preprocess: int = 5

	should_load_preprocessed: bool = False
	# should_load_preprocessed: bool = True

	should_write_final_merged_eeg_fif: bool = False
	# should_write_final_merged_eeg_fif: bool = True

	should_export_html_histograms: bool = True

	absolute_max_workers: int = 4

	# included_xdf_file_names = [
	# 	"E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-21T051157.400Z_eeg.xdf", ## When it started to work
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-20T215045.162Z_eeg.xdf"
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-20T164055.381Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-18T092615.398Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-17T215112.606Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-17T124127.644Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-17T214946.083Z_eeg.xdf",
	#     # # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-09-22T182649.051Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-16T220233.548Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-16T212744.771Z_eeg.xdf",
	#     # # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-16T212721.939Z_eeg.xdf",
	#     # # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-10-16T212528.076Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-09-23T141026.412Z_eeg.xdf",
	#     # "E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001/LabRecorder_Apogee_2025-09-22T213547.659Z_eeg.xdf",
	# ]

	# included_xdf_file_names = [Path(v).resolve() for v in included_xdf_file_names]
	# included_xdf_file_names = [v.name for v in included_xdf_file_names]


	included_xdf_file_names = None ## include all 
	included_xdf_file_names

	export_date_prefix = datetime.now().strftime("%Y-%m-%d_")

	# Session discovery/compute catalog (SQLite) — discover all XDFs, then update during/after processing
	session_catalog: SessionCatalog = get_or_create_catalog(pickled_data_path=pickled_data_path)
	n_discovered = session_catalog.discover_and_upsert(lab_recorder_output_path, recordings_extensions=['.xdf'])
	print(f"Session catalog DB: {session_catalog.db_path.as_posix()}")
	print(f"  discovered/upserted {n_discovered} XDF files from {lab_recorder_output_path.as_posix()}")

	# Streaming Analyze Run: export each session as it completes, then drop Raw/results (retain_in_memory=False).
	# Set retain_in_memory=True to keep the old batch-in-RAM API for notebooks / interactive use.
	summary_output_folder = outputs_root_folder.joinpath("session_summaries")
	summary_output_folder.mkdir(parents=True, exist_ok=True)
	html_output_folder = outputs_root_folder.joinpath("spectrograms_html") if should_export_html_histograms else None
	if html_output_folder is not None:
		html_output_folder.mkdir(parents=True, exist_ok=True)
	spectrograms_npz_dir = outputs_root_folder
	spectrograms_h5_path = outputs_root_folder.joinpath(f"{export_date_prefix}spectrograms_export.h5")
	spectrograms_nc_path = outputs_root_folder.joinpath(f"{export_date_prefix}spectrograms_export.nc")
	spectrograms_parquet_path = outputs_root_folder.joinpath(f"{export_date_prefix}spectrograms_export.parquet")

	_sink_lock = threading.Lock()
	_summary_rows: List[Dict[str, Any]] = []
	_extracted_comments: List[pd.DataFrame] = []
	_html_files: List[Path] = []
	_npz_paths: List[Path] = []
	_used_session_ids: Set[str] = set()
	_h5_state: Dict[str, Any] = {"session_count": 0}
	ignored_comment_descriptions = ["BAD_motion", ""]

	_h5_file = h5py.File(spectrograms_h5_path, "w")
	_h5_file.attrs["format_version"] = "1.0"
	_h5_file.attrs["freq_min"] = 1.0
	_h5_file.attrs["freq_max"] = 40.0
	_h5_sessions_grp = _h5_file.create_group("sessions")

	def _streaming_session_sink(idx, eeg_raw, motion_raw, stream_infos, result):
		xdf_filename = None
		try:
			if stream_infos is not None and "xdf_filename" in getattr(stream_infos, "columns", []):
				xdf_filename = str(stream_infos["xdf_filename"].iloc[0])
			elif isinstance(stream_infos, pd.DataFrame) and len(stream_infos) > 0 and "xdf_filename" in stream_infos.columns:
				xdf_filename = str(stream_infos["xdf_filename"].iloc[0])
		except Exception:
			xdf_filename = None

		with _sink_lock:
			# Comments / annotations (lightweight DataFrame only)
			try:
				an_annotations = eeg_raw.annotations
				if (an_annotations is not None) and (len(an_annotations) > 0):
					an_annotation_df = an_annotations.to_data_frame(time_format="datetime")
					an_annotation_df = an_annotation_df[np.logical_not(np.isin(an_annotation_df["description"], ignored_comment_descriptions))]
					if len(an_annotation_df) > 0:
						_extracted_comments.append(an_annotation_df)
			except Exception as e:
				print(f"  WARN: comment extract failed for session {idx}: {e}")

			row = SpectogramPlottingHelper.compute_one_session_summary_row(idx, eeg_raw, result, xdf_filename=xdf_filename, freq_min=1.0, freq_max=40.0)
			if row is not None:
				_summary_rows.append(row)

			if html_output_folder is not None:
				try:
					html_path = SpectogramPlottingHelper.export_one_session_spectrogram_html(idx, eeg_raw, result, html_output_folder, freq_min=1.0, freq_max=40.0, filename_prefix=export_date_prefix, n_sessions_hint=n_most_recent_sessions_to_preprocess)
					if html_path is not None:
						_html_files.append(html_path)
				except Exception as e:
					print(f"  WARN: HTML export failed for session {idx}: {e}")

			try:
				npz_path = SpectogramPlottingHelper.export_one_session_spectrogram_npz(idx, eeg_raw, result, spectrograms_npz_dir, freq_min=1.0, freq_max=40.0, filename_prefix=export_date_prefix, motion_raw=motion_raw, used_session_ids=_used_session_ids)
				if npz_path is not None:
					_npz_paths.append(npz_path)
			except Exception as e:
				print(f"  WARN: NPZ export failed for session {idx}: {e}")

			try:
				session_key = f"session_{idx:03d}"
				ok = SpectogramPlottingHelper.write_one_session_hdf5(_h5_sessions_grp, session_key, eeg_raw, result, motion_raw=motion_raw, xdf_filename=xdf_filename, freq_min=1.0, freq_max=40.0)
				if ok:
					_h5_state["session_count"] = int(_h5_state["session_count"]) + 1
			except Exception as e:
				print(f"  WARN: HDF5 export failed for session {idx}: {e}")

	print("Analyze Run mode: streaming (retain_in_memory=False) — export-then-release per session")
	try:
		sso, xdf_dataset_indicies, _out_xdf_stream_infos_df, active_only_out_eeg_raws, active_only_out_motion_raws, results = SpectogramPlottingHelper.process_XDFs_main(
			included_xdf_file_names=included_xdf_file_names,
			n_most_recent_sessions_to_preprocess=n_most_recent_sessions_to_preprocess,
			should_write_final_merged_eeg_fif=should_write_final_merged_eeg_fif,
			should_load_preprocessed=should_load_preprocessed,
			absolute_max_workers=absolute_max_workers,
			session_catalog=session_catalog,
			use_session_catalog=True,
			retain_in_memory=False,
			session_sink=_streaming_session_sink,
		)
	finally:
		# Finalize HDF5 even if processing aborts mid-batch
		try:
			_h5_file.attrs["n_sessions"] = int(_h5_state["session_count"])
			_h5_file.close()
			print(f"Exported spectrograms to HDF5: {spectrograms_h5_path.as_posix()} ({_h5_state['session_count']} sessions)")
		except Exception as e:
			print(f"  Export failed (HDF5 finalize): {e}")
			spectrograms_h5_path = None

	if _extracted_comments:
		extracted_comments_df: pd.DataFrame = pd.concat(_extracted_comments)
		extracted_comments_df = extracted_comments_df.rename(columns={"onset": "time", "description": "text"}, inplace=False)
		print(f"Extracted {len(extracted_comments_df)} comments/annotations")
	else:
		print("No comments/annotations found")

	# Write summary CSV from rows accumulated in the sink
	summary_csv_path = summary_output_folder.joinpath(f"{export_date_prefix}session_summaries.csv")
	if _summary_rows:
		pd.DataFrame.from_records(_summary_rows).to_csv(summary_csv_path, index=False)
		print(f"\nSaved per-session summary metrics to: {summary_csv_path.as_posix()}")
	else:
		summary_csv_path = summary_output_folder.joinpath(f"{export_date_prefix}session_summaries_empty.csv")
		pd.DataFrame().to_csv(summary_csv_path, index=False)
		print("No valid spectrogram-based metrics were computed; wrote empty summary CSV.")

	html_files = _html_files
	spectrograms_npz_paths = _npz_paths if _npz_paths else None
	if spectrograms_npz_paths:
		print(f"Exported {len(spectrograms_npz_paths)} spectrogram .npz file(s) for Rerun to: {spectrograms_npz_dir.as_posix()}")

	# NetCDF / Parquet from finished HDF5 (no Raw retained)
	if spectrograms_h5_path is not None and spectrograms_h5_path.exists():
		try:
			SpectogramPlottingHelper.export_netcdf_from_hdf5(spectrograms_h5_path, spectrograms_nc_path)
		except Exception as e:
			print(f"  Export failed (NetCDF from HDF5): {e}")
			spectrograms_nc_path = None
		try:
			SpectogramPlottingHelper.export_parquet_from_hdf5(spectrograms_h5_path, spectrograms_parquet_path)
		except Exception as e:
			print(f"  Export failed (Parquet from HDF5): {e}")
			spectrograms_parquet_path = None
	else:
		spectrograms_nc_path = None
		spectrograms_parquet_path = None

	n_sessions_done = int(len(xdf_dataset_indicies)) if xdf_dataset_indicies is not None else 0

	# Finalize session catalog with batch results + export artifact pointers
	export_params = {
		"summary_csv_path": str(summary_csv_path) if summary_csv_path is not None else None,
		"html_output_folder": str(html_output_folder) if html_output_folder is not None else None,
		"spectrograms_h5_path": str(spectrograms_h5_path) if spectrograms_h5_path is not None else None,
		"spectrograms_nc_path": str(spectrograms_nc_path) if spectrograms_nc_path is not None else None,
		"spectrograms_parquet_path": str(spectrograms_parquet_path) if spectrograms_parquet_path is not None else None,
	}
	n_catalog_synced = session_catalog.sync_from_processed_batch(
		_out_xdf_stream_infos_df,
		active_only_out_eeg_raws,
		recordings_dir=lab_recorder_output_path,
		status="computed",
		summary_csv_path=summary_csv_path,
		extra_params={k: v for k, v in export_params.items() if v is not None},
	)
	print(f"Session catalog synced {n_catalog_synced} processed session(s) → {session_catalog.db_path.as_posix()}")

	print(f"\n=== Processing Complete ===")
	print(f"Total sessions processed: {n_sessions_done}")
	print(f"Session catalog DB: {session_catalog.db_path.as_posix()}")
	if should_export_html_histograms:
		print(f"Individual HTML spectrograms: {html_output_folder} ({len(html_files)} files)")
	print(f"Session summary metrics CSV: {summary_csv_path}")

	if spectrograms_npz_paths:
		print(f"Spectrograms for Rerun: {spectrograms_npz_paths} {f' ({len(spectrograms_npz_paths)} .npz files)' if (len(spectrograms_npz_paths) > 1) else ''}")
		print(f'\tuv run --project rerun -- python rerun/view_spectrograms_rerun.py "{spectrograms_npz_dir}"')
	else:
		print("Spectrograms for Rerun: failed")

	print(f"Spectrograms HDF5 (interchange): {spectrograms_h5_path if spectrograms_h5_path else 'failed'}")
	print(f"Spectrograms NetCDF (interchange): {spectrograms_nc_path if spectrograms_nc_path else 'failed'}")
	print(f"Spectrograms Parquet (interchange): {spectrograms_parquet_path if spectrograms_parquet_path else 'failed'}")
