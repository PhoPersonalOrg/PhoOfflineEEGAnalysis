"""Multi-root source folder registry: online/missing health checks, catalog counts, and YAML CRUD."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths
from phoofflineeeganalysis.offline_data_manager.media_catalog import (
    DEFAULT_CONSOLIDATE_ROOT,
    MediaCatalog,
    SourceSpec,
    load_source_specs,
    save_source_specs,
)
from phoofflineeeganalysis.offline_data_manager.media_modalities import extensions_for_category, media_kind_for_category
from phoofflineeeganalysis.offline_data_manager.session_catalog import SessionCatalog


def _norm_fs_str(path: Union[str, Path]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


@dataclass
class SourceEntry:
    """Normalized health + configuration record for one LabRecorder or media source root."""

    kind: str
    modality: str
    path: Path
    recursive: bool = False
    exclude: List[Path] = field(default_factory=list)
    exists: bool = False
    file_count_estimate: Optional[int] = None
    last_scanned_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "modality": self.modality,
            "path": self.path.as_posix(),
            "recursive": bool(self.recursive),
            "exclude": [ex.as_posix() for ex in self.exclude],
            "exists": bool(self.exists),
            "file_count_estimate": self.file_count_estimate,
            "last_scanned_at": self.last_scanned_at,
        }


def _shallow_count_files(root: Path, extensions: Sequence[str], *, recursive: bool, max_probe: int = 5000) -> int:
    ext_set = {e.lower() for e in extensions}
    count = 0
    if not recursive:
        try:
            for item in root.iterdir():
                if item.is_file() and item.suffix.lower() in ext_set:
                    count += 1
                    if count >= max_probe:
                        break
            ## END for item in root.iterdir()...
        except OSError:
            return 0
        return count

    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "_DUP"]
        for fname in filenames:
            if Path(fname).suffix.lower() in ext_set:
                count += 1
                if count >= max_probe:
                    return count
        ## END for fname in filenames...
    ## END for _dirpath, dirnames, filenames in os.walk(root)...

    return count


def list_sources(app_paths: AppPaths, *, session_catalog: Optional[SessionCatalog] = None, media_catalog: Optional[MediaCatalog] = None, probe_filesystem_counts: bool = False) -> List[SourceEntry]:
    """Return all configured LabRecorder and media source roots with online/missing health and catalog counts."""
    session_summary: Dict[str, Dict[str, Any]] = {}
    if session_catalog is not None:
        session_summary = session_catalog.source_summary_by_root(candidate_roots=app_paths.lab_recorder_dirs)
    elif app_paths.resolved_session_catalog_path().exists():
        session_summary = SessionCatalog(app_paths.resolved_session_catalog_path()).source_summary_by_root(candidate_roots=app_paths.lab_recorder_dirs)

    media_summary: Dict[str, Dict[str, Any]] = {}
    if media_catalog is not None:
        media_summary = media_catalog.source_summary_by_root()
    elif app_paths.media_catalog_path.exists():
        media_summary = MediaCatalog(app_paths.media_catalog_path).source_summary_by_root()

    entries: List[SourceEntry] = []
    for lab_dir in app_paths.lab_recorder_dirs:
        p = Path(lab_dir)
        exists = p.exists() and p.is_dir()
        norm = _norm_fs_str(p)
        stat = session_summary.get(norm)
        est = int(stat["count"]) if stat is not None else (
            _shallow_count_files(p, [".xdf"], recursive=True) if (probe_filesystem_counts and exists) else 0
        )
        entries.append(
            SourceEntry(
                kind="lab_recorder",
                modality="EEG",
                path=p,
                recursive=True,
                exclude=[],
                exists=exists,
                file_count_estimate=est,
                last_scanned_at=stat["last_scanned_at"] if stat is not None else None,
            )
        )
    ## END for lab_dir in app_paths.lab_recorder_dirs...

    specs: List[SourceSpec] = []
    if app_paths.media_sources_yaml.exists():
        try:
            specs, _dest = load_source_specs(app_paths.media_sources_yaml)
        except Exception:
            specs = []

    for spec in specs:
        p = Path(spec.path)
        exists = p.exists() and p.is_dir()
        norm = _norm_fs_str(p)
        stat = media_summary.get(norm)
        mkind = media_kind_for_category(spec.category)
        est = int(stat["count"]) if stat is not None else (
            _shallow_count_files(p, extensions_for_category(spec.category), recursive=spec.recursive) if (probe_filesystem_counts and exists) else 0
        )
        entries.append(
            SourceEntry(
                kind=str(spec.category),
                modality=mkind,
                path=p,
                recursive=bool(spec.recursive),
                exclude=list(spec.exclude),
                exists=exists,
                file_count_estimate=est,
                last_scanned_at=stat["last_scanned_at"] if stat is not None else None,
            )
        )
    ## END for spec in specs...

    return entries


def add_lab_dir(app_paths: AppPaths, folder: Union[str, Path], *, save_path: Optional[Union[str, Path]] = None) -> AppPaths:
    """Append *folder* to ``app_paths.lab_recorder_dirs`` if not already present and save overrides."""
    candidate = Path(folder)
    existing_norms = {_norm_fs_str(p) for p in app_paths.lab_recorder_dirs}
    if _norm_fs_str(candidate) not in existing_norms:
        app_paths.lab_recorder_dirs.append(candidate)
    app_paths.save(save_path)
    return app_paths


def remove_lab_dir(app_paths: AppPaths, folder: Union[str, Path], *, save_path: Optional[Union[str, Path]] = None) -> AppPaths:
    """Remove *folder* from ``app_paths.lab_recorder_dirs`` and save overrides."""
    target_norm = _norm_fs_str(folder)
    app_paths.lab_recorder_dirs = [p for p in app_paths.lab_recorder_dirs if _norm_fs_str(p) != target_norm]
    app_paths.save(save_path)
    return app_paths


def _load_or_empty_specs(sources_yaml: Path) -> tuple[List[SourceSpec], Path]:
    if sources_yaml.exists():
        return load_source_specs(sources_yaml)
    return [], DEFAULT_CONSOLIDATE_ROOT


def add_media_source(sources_yaml: Union[str, Path], category: str, path: Union[str, Path], *, recursive: bool = False, exclude: Optional[Sequence[Union[str, Path]]] = None) -> List[SourceSpec]:
    """Add or replace a media source root under *category* and rewrite *sources_yaml*."""
    yaml_path = Path(sources_yaml)
    specs, dest_root = _load_or_empty_specs(yaml_path)
    target_norm = _norm_fs_str(path)
    ex_paths = [Path(ex) for ex in (exclude or [])]
    updated = False
    for idx, spec in enumerate(specs):
        if spec.category == category and _norm_fs_str(spec.path) == target_norm:
            specs[idx] = SourceSpec(category=category, path=Path(path), recursive=bool(recursive), exclude=ex_paths)
            updated = True
            break
    ## END for idx, spec in enumerate(specs)...

    if not updated:
        specs.append(SourceSpec(category=category, path=Path(path), recursive=bool(recursive), exclude=ex_paths))
    save_source_specs(specs, dest_root=dest_root, sources_yaml=yaml_path)
    return specs


def remove_media_source(sources_yaml: Union[str, Path], category: str, path: Union[str, Path]) -> List[SourceSpec]:
    """Remove a media source root matching (*category*, *path*) and rewrite *sources_yaml*."""
    yaml_path = Path(sources_yaml)
    specs, dest_root = _load_or_empty_specs(yaml_path)
    target_norm = _norm_fs_str(path)
    kept = [s for s in specs if not (s.category == category and _norm_fs_str(s.path) == target_norm)]
    save_source_specs(kept, dest_root=dest_root, sources_yaml=yaml_path)
    return kept


def update_media_source(sources_yaml: Union[str, Path], category: str, path: Union[str, Path], *, recursive: Optional[bool] = None, exclude: Optional[Sequence[Union[str, Path]]] = None, new_category: Optional[str] = None, new_path: Optional[Union[str, Path]] = None) -> List[SourceSpec]:
    """Update fields on an existing media source entry in *sources_yaml*."""
    yaml_path = Path(sources_yaml)
    specs, dest_root = _load_or_empty_specs(yaml_path)
    target_norm = _norm_fs_str(path)
    for idx, spec in enumerate(specs):
        if spec.category == category and _norm_fs_str(spec.path) == target_norm:
            specs[idx] = SourceSpec(
                category=str(new_category) if new_category is not None else spec.category,
                path=Path(new_path) if new_path is not None else spec.path,
                recursive=bool(recursive) if recursive is not None else spec.recursive,
                exclude=[Path(ex) for ex in exclude] if exclude is not None else list(spec.exclude),
            )
            break
    ## END for idx, spec in enumerate(specs)...

    save_source_specs(specs, dest_root=dest_root, sources_yaml=yaml_path)
    return specs


__all__ = [
    "SourceEntry",
    "list_sources",
    "add_lab_dir",
    "remove_lab_dir",
    "add_media_source",
    "remove_media_source",
    "update_media_source",
]
