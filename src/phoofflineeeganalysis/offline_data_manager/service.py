"""UI-facing service facade for Pho Offline Data Manager."""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths, default_repo_root
from phoofflineeeganalysis.offline_data_manager.media_catalog import (
    DEFAULT_CONSOLIDATE_ROOT,
    MediaCatalog,
    SourceSpec,
    load_source_specs,
    save_source_specs,
)
from phoofflineeeganalysis.offline_data_manager.media_modalities import (
    MEDIA_CATEGORIES,
     expand_modality_filters,
    media_kind_for_category,
)
from phoofflineeeganalysis.offline_data_manager.session_catalog import (
    SESSION_STATUSES,
    SessionCatalog,
    get_or_create_catalog,
)
from phoofflineeeganalysis.offline_data_manager.source_registry import list_sources as registry_list_sources
from phoofflineeeganalysis.offline_data_manager.video_ingest_helpers import DEFAULT_MIN_AGE_S


def _ensure_utc_day(day_date: datetime) -> datetime:
    if day_date.tzinfo is None:
        day_date = day_date.replace(tzinfo=timezone.utc)
    else:
        day_date = day_date.astimezone(timezone.utc)
    return day_date.replace(hour=0, minute=0, second=0, microsecond=0)


def _clean_optional_str(val: Any) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip()
    return text if text else None


def _clean_optional_float(val: Any) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)) or not pd.notna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class OfflineDataManagerService:
    """Single backend entry point backing Manager Home, Sources & Paths, and processing dialogs."""

    def __init__(self, paths: Optional[AppPaths] = None, *, config_yaml: Optional[Union[str, Path]] = None):
        self.paths: AppPaths = paths if paths is not None else AppPaths.load(config_yaml=config_yaml)
        self._session_catalog: Optional[SessionCatalog] = None
        self._media_catalog: Optional[MediaCatalog] = None


    @property
    def session_catalog(self) -> SessionCatalog:
        target_db = self.paths.resolved_session_catalog_path()
        if self._session_catalog is None or self._session_catalog.db_path != target_db:
            self._session_catalog = get_or_create_catalog(db_path=target_db, pickled_data_path=self.paths.pickled_data_path)
        return self._session_catalog


    @property
    def media_catalog(self) -> MediaCatalog:
        target_db = self.paths.media_catalog_path
        if self._media_catalog is None or self._media_catalog.db_path != target_db:
            self._media_catalog = MediaCatalog(target_db)
        return self._media_catalog


    def reload(self) -> None:
        """Reset cached catalog handles so subsequent queries re-read current paths."""
        self._session_catalog = None
        self._media_catalog = None


    def stats(self) -> Dict[str, Any]:
        """Compute aggregate counts across sessions (by status) and canonical media (by category & kind)."""
        sessions_df = self.session_catalog.load_sessions_dataframe()
        by_status: Dict[str, int] = {st: 0 for st in SESSION_STATUSES}
        if len(sessions_df) > 0 and "status" in sessions_df.columns:
            vc = sessions_df["status"].astype(str).value_counts().to_dict()
            for st, cnt in vc.items():
                by_status[str(st)] = int(cnt)
            ## END for st, cnt in vc.items()...

        media_df = self.media_catalog.load_media_df() if self.paths.media_catalog_path.exists() else pd.DataFrame()
        by_category: Dict[str, int] = {cat: 0 for cat in MEDIA_CATEGORIES}
        by_kind: Dict[str, int] = {"video": 0, "screenshot": 0, "audio": 0}
        total_media_all = len(media_df)
        dup_media = 0
        canonical_count = 0

        if not media_df.empty:
            if "is_duplicate" in media_df.columns:
                dup_media = int(media_df["is_duplicate"].astype(bool).sum())
                canon_df = media_df.loc[~media_df["is_duplicate"].astype(bool)]
            else:
                canon_df = media_df
            canonical_count = len(canon_df)
            if "category" in canon_df.columns:
                for cat, cnt in canon_df["category"].astype(str).value_counts().to_dict().items():
                    by_category[str(cat)] = int(cnt)
                ## END for cat, cnt in canon_df["category"].astype(str).value_counts().to_dict().items()...

            if "media_kind" in canon_df.columns:
                for knd, cnt in canon_df["media_kind"].astype(str).value_counts().to_dict().items():
                    by_kind[str(knd)] = int(cnt)
                ## END for knd, cnt in canon_df["media_kind"].astype(str).value_counts().to_dict().items()...

        return {
            "total_sessions": len(sessions_df),
            "sessions_by_status": by_status,
            "total_media": canonical_count,
            "total_media_all": total_media_all,
            "duplicate_media": dup_media,
            "media_by_category": by_category,
            "media_by_kind": by_kind,
        }


    def list_sources(self, *, probe_filesystem_counts: bool = False) -> List[Dict[str, Any]]:
        """List all LabRecorder and media source folders with online/missing health and catalog counts."""
        sc = self._session_catalog if (self._session_catalog is not None or self.paths.resolved_session_catalog_path().exists()) else None
        mc = self._media_catalog if (self._media_catalog is not None or self.paths.media_catalog_path.exists()) else None
        entries = registry_list_sources(
            self.paths,
            session_catalog=sc,
            media_catalog=mc,
            probe_filesystem_counts=probe_filesystem_counts,
        )
        return [entry.to_dict() for entry in entries]


    def load_media_source_specs(self) -> Tuple[List[SourceSpec], Path]:
        """Return ``(specs, consolidate_dest_root)`` from the configured media sources YAML."""
        if self.paths.media_sources_yaml.exists():
            return load_source_specs(self.paths.media_sources_yaml)
        return [], DEFAULT_CONSOLIDATE_ROOT


    def save_paths(self, updated_paths: Optional[AppPaths] = None, *, media_specs: Optional[Sequence[SourceSpec]] = None, consolidate_dest: Optional[Union[str, Path]] = None, save_path: Optional[Union[str, Path]] = None) -> Path:
        """Persist updated ``AppPaths`` (and media source specs when provided) and refresh catalog handles."""
        if updated_paths is not None:
            self.paths = updated_paths
        saved_yaml = self.paths.save(save_path)
        if media_specs is not None or consolidate_dest is not None:
            specs_to_write = list(media_specs) if media_specs is not None else self.load_media_source_specs()[0]
            save_source_specs(specs_to_write, dest_root=consolidate_dest, sources_yaml=self.paths.media_sources_yaml)
        self.reload()
        return saved_yaml


    def discover_sessions(self, *, dirs: Optional[Sequence[Union[str, Path]]] = None, enrich_streams: bool = False, progress: Optional[Callable[..., None]] = None) -> int:
        """Scan LabRecorder directories for ``.xdf`` sessions and upsert into ``SessionCatalog``."""
        target_dirs = [Path(d) for d in dirs] if dirs is not None else self.paths.existing_lab_recorder_dirs()
        if not target_dirs:
            return 0
        if progress is not None:
            progress(f"Scanning {len(target_dirs)} LabRecorder directory(ies) for .xdf files...")
        n_found = self.session_catalog.discover_and_upsert(target_dirs, recordings_extensions=[".xdf"], enrich_streams=enrich_streams)
        if progress is not None:
            progress(f"Upserted {n_found} session(s) into {self.session_catalog.db_path.as_posix()}")
        return n_found


    def enrich_session_streams(self, paths: Optional[Sequence[Union[str, Path]]] = None, *, progress: Optional[Callable[..., None]] = None) -> int:
        """Inspect XDF stream headers and populate stream modality chips on catalog rows."""
        if paths is None:
            df = self.session_catalog.load_sessions_dataframe()
            if len(df) == 0:
                return 0
            missing = [row["xdf_path"] for _, row in df.iterrows() if not row.get("streams")]
            target_paths: Sequence[Union[str, Path]] = missing
        else:
            target_paths = paths
        if not target_paths:
            return 0
        if progress is not None:
            progress(f"Inspecting XDF stream headers for {len(target_paths)} session(s)...")
        return self.session_catalog.enrich_streams_for_paths(target_paths)


    def ingest_media(self, *, sources: Optional[Union[Sequence[SourceSpec], Path, str]] = None, force_rebuild: bool = False, skip_incomplete: bool = True, min_age_s: float = DEFAULT_MIN_AGE_S, recompute_duplicates: bool = True, progress: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
        """Discover video, screenshot, and audio sources into ``MediaCatalog`` and recompute duplicates."""
        target_sources = sources if sources is not None else self.paths.media_sources_yaml
        if progress is not None:
            progress(f"Ingesting media metadata from {target_sources}...")
        counts = self.media_catalog.discover_and_upsert(
            target_sources,
            force_rebuild=force_rebuild,
            skip_incomplete=skip_incomplete,
            min_age_s=min_age_s,
        )
        dedupe = {"groups": 0, "duplicate_rows": 0}
        if recompute_duplicates:
            if progress is not None:
                progress("Recomputing duplicate media groups...")
            dedupe = self.media_catalog.recompute_duplicates()
        return {"counts": counts, "dedupe": dedupe}


    def duplicate_report(self) -> Dict[str, Any]:
        """Summarize canonical vs duplicate media items across the catalog."""
        df = self.media_catalog.load_media_df()
        dup_df = self.media_catalog.load_duplicates_df()
        n_total = len(df)
        n_canon = int(df["is_canonical"].sum()) if n_total and "is_canonical" in df.columns else 0
        n_dup = int(df["is_duplicate"].sum()) if n_total and "is_duplicate" in df.columns else 0
        n_groups = int(dup_df["duplicate_group_id"].nunique()) if len(dup_df) else 0
        sample_lines: List[str] = []
        if len(dup_df):
            for gid, grp in dup_df.groupby("duplicate_group_id", sort=True):
                sample_lines.append(f"  Group {gid} ({len(grp)} files):")
                for row in grp.itertuples():
                    tag = "KEEP" if getattr(row, "is_canonical", False) else "DUP "
                    sample_lines.append(f"    [{tag}] {getattr(row, 'video_path', '')}")
                ## END for row in grp.itertuples()...
            ## END for gid, grp in dup_df.groupby("duplicate_group_id", sort=True)...

        return {
            "total": n_total,
            "canonical": n_canon,
            "duplicates": n_dup,
            "groups": n_groups,
            "lines": sample_lines,
        }


    def consolidate_media(self, dest_root: Optional[Union[str, Path]] = None, *, dry_run: bool = True, only_canonical: bool = True, progress: Optional[Callable[..., None]] = None) -> Dict[str, int]:
        """Copy canonical media files into ``{dest_root}/{category}/``."""
        resolved_dest: Optional[Path] = Path(dest_root) if dest_root is not None else None
        if resolved_dest is None and self.paths.media_sources_yaml.exists():
            _specs, resolved_dest = load_source_specs(self.paths.media_sources_yaml)
        if progress is not None:
            progress(f"Consolidating canonical media -> {resolved_dest} (dry_run={dry_run})...")
        return self.media_catalog.consolidate(dest_root=resolved_dest, dry_run=dry_run, only_canonical=only_canonical)


    def get_compute_options(self) -> Dict[str, Any]:
        """Return a copy of the active computation options."""
        return dict(self.paths.process_defaults)


    def update_compute_options(self, options: Dict[str, Any], *, persist: bool = True) -> Dict[str, Any]:
        """Update current computation options on ``self.paths.process_defaults`` (and persist to local YAML when requested)."""
        allowed_keys = {
            "n_most_recent_sessions_to_preprocess",
            "should_write_final_merged_eeg_fif",
            "use_computation_cache",
            "use_mtime_in_cache_key",
            "should_load_preprocessed",
            "absolute_max_workers",
        }
        for key, val in options.items():
            if key in allowed_keys:
                self.paths.process_defaults[key] = val
        ## END for key, val in options.items()...

        if persist:
            try:
                self.paths.save()
            except Exception:
                pass
        return dict(self.paths.process_defaults)


    def compute_session(self, record_or_path: Union[Dict[str, Any], str, Path], *, progress: Optional[Callable[..., None]] = None, cancel_event: Optional[threading.Event] = None, **option_overrides: Any) -> Any:
        """Compute a single selected ``.xdf`` session using the current computation options (plus any *option_overrides*)."""
        if isinstance(record_or_path, dict):
            raw_path = record_or_path.get("xdf_path") or record_or_path.get("path")
        else:
            raw_path = record_or_path
        if not raw_path:
            raise ValueError("Selected session has no .xdf file path")
        xdf_path = Path(str(raw_path))
        call_kwargs: Dict[str, Any] = dict(option_overrides)
        call_kwargs["n_most_recent_sessions_to_preprocess"] = None
        call_kwargs["included_xdf_paths"] = [xdf_path]
        call_kwargs["included_xdf_file_names"] = [xdf_path.name]
        return self.process_sessions(progress=progress, cancel_event=cancel_event, **call_kwargs)


    def process_sessions(self, *, progress: Optional[Callable[..., None]] = None, cancel_event: Optional[threading.Event] = None, **process_kwargs: Any) -> Any:
        """Run ``SpectogramPlottingHelper.process_XDFs_main`` wired to ``self.session_catalog``."""
        merged = dict(self.paths.process_defaults)
        merged.update(process_kwargs)
        repo_root = str(default_repo_root())
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        if progress is not None:
            progress("Importing main_analyze_run.SpectogramPlottingHelper...")
        from main_analyze_run import SpectogramPlottingHelper

        raw_paths = merged.get("included_xdf_paths")
        resolved_paths = [Path(p) for p in raw_paths] if raw_paths is not None else None

        if progress is not None:
            target_desc = f"target={[p.name for p in resolved_paths]}" if resolved_paths else f"n_recent={merged.get('n_most_recent_sessions_to_preprocess')}"
            progress(
                f"Running process_XDFs_main ({target_desc}, cache={bool(merged.get('use_computation_cache', True))}, fif={bool(merged.get('should_write_final_merged_eeg_fif', True))}, workers={merged.get('absolute_max_workers')})...",
                current=0,
                total=0,
                active_sessions=[],
            )
        return SpectogramPlottingHelper.process_XDFs_main(
            n_most_recent_sessions_to_preprocess=merged.get("n_most_recent_sessions_to_preprocess"),
            should_write_final_merged_eeg_fif=bool(merged.get("should_write_final_merged_eeg_fif", True)),
            included_xdf_file_names=merged.get("included_xdf_file_names"),
            included_xdf_paths=resolved_paths,
            should_load_preprocessed=bool(merged.get("should_load_preprocessed", False)),
            use_computation_cache=bool(merged.get("use_computation_cache", True)),
            use_mtime_in_cache_key=bool(merged.get("use_mtime_in_cache_key", False)),
            absolute_max_workers=int(merged.get("absolute_max_workers") or 2),
            session_catalog=self.session_catalog,
            use_session_catalog=True,
            retain_in_memory=False,
            progress_callback=progress,
            cancel_event=cancel_event,
        )


    def calendar_payload(self, *, modality_filters: Optional[Iterable[str]] = None, include_duplicates: bool = False, build_datasource: bool = True) -> Dict[str, Any]:
        """Build filtered session + media DataFrames (and optional ``CalendarDatasource``) for month calendar rendering."""
        expanded = expand_modality_filters(modality_filters)
        include_eeg = expanded is None or ("EEG" in expanded)
        if include_eeg:
            sessions_df = self.session_catalog.load_calendar_dataframe()
        else:
            sessions_df = pd.DataFrame(
                columns=[
                    "xdf_path",
                    "xdf_filename",
                    "name",
                    "source_root",
                    "recording_datetime",
                    "recording_day_date",
                    "duration_sec",
                    "first_timestamp_rel",
                    "duration_rel",
                    "status",
                    "streams",
                    "fif_path",
                    "mat_path",
                    "error",
                ]
            )

        if self.paths.media_catalog_path.exists() or self._media_catalog is not None:
            if expanded is None:
                media_df = self.media_catalog.load_calendar_dataframe(include_duplicates=include_duplicates)
            else:
                media_cats = [tok for tok in expanded if tok != "EEG"]
                media_df = self.media_catalog.load_calendar_dataframe(include_duplicates=include_duplicates, categories=media_cats)
        else:
            media_df = pd.DataFrame(
                columns=[
                    "video_path",
                    "filename",
                    "category",
                    "media_kind",
                    "source_root",
                    "recording_datetime",
                    "recording_day_date",
                    "duration_sec",
                    "first_timestamp_rel",
                    "duration_rel",
                    "is_duplicate",
                    "is_canonical",
                    "consolidated_path",
                ]
            )

        payload: Dict[str, Any] = {
            "sessions_df": sessions_df,
            "media_df": media_df,
        }
        if build_datasource:
            try:
                from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import CalendarDatasource

                payload["datasource"] = CalendarDatasource(xdf_stream_infos_df=sessions_df, video_infos_df=media_df)
            except Exception:
                payload["datasource"] = None
        return payload


    def day_records(self, day: datetime, *, modality_filters: Optional[Iterable[str]] = None, include_duplicates: bool = False) -> List[Dict[str, Any]]:
        """Return chronologically sorted normalized session + media dicts for *day* (UTC calendar date)."""
        target_day = _ensure_utc_day(day)
        cal = self.calendar_payload(modality_filters=modality_filters, include_duplicates=include_duplicates, build_datasource=False)
        sessions_df: pd.DataFrame = cal["sessions_df"]
        media_df: pd.DataFrame = cal["media_df"]

        records: List[Dict[str, Any]] = []
        if len(sessions_df) > 0 and "recording_day_date" in sessions_df.columns:
            s_days = pd.to_datetime(sessions_df["recording_day_date"], utc=True, errors="coerce")
            s_mask = s_days.map(lambda v: _ensure_utc_day(v.to_pydatetime()) == target_day if pd.notna(v) else False)
            for row in sessions_df.loc[s_mask].itertuples():
                rec_dt = getattr(row, "recording_datetime", None)
                if isinstance(rec_dt, pd.Timestamp):
                    rec_dt = rec_dt.to_pydatetime()
                streams_val = getattr(row, "streams", None)
                streams_list = list(streams_val) if isinstance(streams_val, (list, tuple)) else []
                xdf_p = _clean_optional_str(getattr(row, "xdf_path", None))
                fname = _clean_optional_str(getattr(row, "xdf_filename", None)) or (Path(xdf_p).name if xdf_p else "session.xdf")
                records.append(
                    {
                        "kind": "session",
                        "category": "EEG",
                        "status": _clean_optional_str(getattr(row, "status", None)) or "discovered",
                        "path": xdf_p,
                        "xdf_path": xdf_p,
                        "filename": fname,
                        "streams": streams_list,
                        "duration_sec": _clean_optional_float(getattr(row, "duration_sec", None)),
                        "recording_datetime": rec_dt if isinstance(rec_dt, datetime) else None,
                        "fif_path": _clean_optional_str(getattr(row, "fif_path", None)),
                        "mat_path": _clean_optional_str(getattr(row, "mat_path", None)),
                        "source_root": _clean_optional_str(getattr(row, "source_root", None)),
                        "error": _clean_optional_str(getattr(row, "error", None)),
                    }
                )
            ## END for row in sessions_df.loc[s_mask].itertuples()...

        if len(media_df) > 0 and "recording_day_date" in media_df.columns:
            m_days = pd.to_datetime(media_df["recording_day_date"], utc=True, errors="coerce")
            m_mask = m_days.map(lambda v: _ensure_utc_day(v.to_pydatetime()) == target_day if pd.notna(v) else False)
            for row in media_df.loc[m_mask].itertuples():
                rec_dt = getattr(row, "recording_datetime", None)
                if isinstance(rec_dt, pd.Timestamp):
                    rec_dt = rec_dt.to_pydatetime()
                cat = _clean_optional_str(getattr(row, "category", None)) or "Video"
                v_path = _clean_optional_str(getattr(row, "video_path", None))
                fname = _clean_optional_str(getattr(row, "filename", None)) or (Path(v_path).name if v_path else "media")
                mkind = _clean_optional_str(getattr(row, "media_kind", None)) or media_kind_for_category(cat, Path(fname).suffix)
                records.append(
                    {
                        "kind": mkind,
                        "category": cat,
                        "path": v_path,
                        "video_path": v_path,
                        "filename": fname,
                        "duration_sec": _clean_optional_float(getattr(row, "duration_sec", None)),
                        "recording_datetime": rec_dt if isinstance(rec_dt, datetime) else None,
                        "source_root": _clean_optional_str(getattr(row, "source_root", None)),
                        "consolidated_path": _clean_optional_str(getattr(row, "consolidated_path", None)),
                    }
                )
            ## END for row in media_df.loc[m_mask].itertuples()...

        def _sort_key(item: Dict[str, Any]) -> Tuple[int, float, int, str]:
            dt = item.get("recording_datetime")
            if isinstance(dt, datetime):
                ts = dt.timestamp() if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc).timestamp()
                has_missing = 0
            else:
                ts = 0.0
                has_missing = 1
            kind_rank = 0 if item.get("kind") == "session" else 1
            return (has_missing, ts, kind_rank, str(item.get("filename") or ""))

        records.sort(key=_sort_key)
        return records


    def open_sessions_in_timeline(self, records_or_paths: Sequence[Union[Dict[str, Any], str, Path]], *, mode: str = "append", **kwargs: Any) -> str:
        """Open, merge, or replace one or more EEG ``.xdf`` sessions in ``pyPhoTimeline``."""
        from phoofflineeeganalysis.analysis.timeline_handoff import open_sessions_in_timeline

        return open_sessions_in_timeline(records_or_paths, mode=mode, **kwargs)


    def open_in_timeline(self, record_or_path: Union[Dict[str, Any], str, Path, Sequence[Union[Dict[str, Any], str, Path]]], *, mode: str = "append", **kwargs: Any) -> str:
        """Open or focus one (or a list of) EEG ``.xdf`` session(s) in ``pyPhoTimeline``."""
        from phoofflineeeganalysis.analysis.timeline_handoff import open_session_in_timeline, open_sessions_in_timeline

        if isinstance(record_or_path, (list, tuple)):
            return open_sessions_in_timeline(record_or_path, mode=mode, **kwargs)
        if isinstance(record_or_path, dict):
            xdf_path = record_or_path.get("xdf_path") or record_or_path.get("path")
            if not xdf_path:
                raise ValueError("Session record has no xdf_path")
            return open_session_in_timeline(
                xdf_path,
                start=kwargs.pop("start", record_or_path.get("recording_datetime")),
                duration_sec=kwargs.pop("duration_sec", record_or_path.get("duration_sec")),
                status=kwargs.pop("status", record_or_path.get("status")),
                mode=mode,
                **kwargs,
            )
        return open_session_in_timeline(record_or_path, mode=mode, **kwargs)


__all__ = ["OfflineDataManagerService"]
