"""Centralized path and defaults configuration for Pho Offline Data Manager.

Layers:
    1. ``config/offline_data_manager/paths.yaml`` (committed defaults; falls back to legacy ``config/offline_analysis_paths.yaml``)
    2. ``config/offline_data_manager/paths.local.yaml`` (gitignored machine-local overrides; falls back to legacy ``config/offline_analysis_paths.local.yaml``)
    3. Environment variables (``PHO_LAB_RECORDER_DIRS``, ``PHO_PICKLED_DATA_PATH``, ``PHO_SESSION_CATALOG_PATH``,
       ``PHO_MEDIA_CATALOG_PATH`` / ``PHO_VIDEO_CATALOG_PATH``, ``PHO_MEDIA_SOURCES_YAML`` / ``PHO_VIDEO_SOURCES_YAML``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

DEFAULT_PROCESS_DEFAULTS: Dict[str, Any] = {
    "n_most_recent_sessions_to_preprocess": 5,
    "should_write_final_merged_eeg_fif": True,
    "use_computation_cache": True,
    "absolute_max_workers": 2,
}


def default_repo_root() -> Path:
    """Return the PhoOfflineEEGAnalysis repository root."""
    return Path(__file__).resolve().parents[3]


def default_paths_yaml(repo_root: Optional[Path] = None) -> Path:
    """Return the committed default paths YAML file."""
    root = Path(repo_root) if repo_root is not None else default_repo_root()
    preferred = root.joinpath("config", "offline_data_manager", "paths.yaml")
    legacy = root.joinpath("config", "offline_analysis_paths.yaml")
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def default_local_paths_yaml(base_yaml: Optional[Union[str, Path]] = None) -> Path:
    """Return the gitignored local-override YAML path adjacent to *base_yaml*."""
    base = Path(base_yaml) if base_yaml is not None else default_paths_yaml()
    return base.parent.joinpath(f"{base.stem}.local.yaml")


def default_media_sources_yaml(repo_root: Optional[Path] = None) -> Path:
    """Return the preferred media sources YAML path under *repo_root*."""
    root = Path(repo_root) if repo_root is not None else default_repo_root()
    candidates = [
        root.joinpath("config", "offline_data_manager", "media_sources.yaml"),
        root.joinpath("config", "media_sources.yaml"),
        root.joinpath("config", "video_sources.yaml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    ## END for candidate in candidates...

    return candidates[0]


def _parse_scalar(val: str) -> Any:
    text = val.strip()
    if text == "[]":
        return []
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if text.lower() in ("null", "none", "~"):
        return None
    if (len(text) >= 2) and (text[0] == text[-1]) and (text[0] in ("'", '"')):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _load_yaml_fallback(path: Path) -> Dict[str, Any]:
    """Minimal YAML loader for flat/1-level nested dicts and lists if PyYAML is absent."""
    data: Dict[str, Any] = {}
    current_section: Optional[str] = None
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                if ":" in stripped:
                    key, _, rest = stripped.partition(":")
                    key = key.strip()
                    rest = rest.strip()
                    if rest == "":
                        data[key] = {}
                        current_section = key
                    else:
                        data[key] = _parse_scalar(rest)
                        current_section = key if isinstance(data[key], list) else None
                continue
            if current_section is None:
                continue
            if stripped.startswith("- "):
                if not isinstance(data.get(current_section), list):
                    data[current_section] = []
                data[current_section].append(_parse_scalar(stripped[2:]))
            elif ":" in stripped:
                key, _, rest = stripped.partition(":")
                if not isinstance(data.get(current_section), dict):
                    data[current_section] = {}
                data[current_section][key.strip()] = _parse_scalar(rest)
        ## END for raw_line in handle...

    return data


def _read_yaml_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except ImportError:
        loaded = _load_yaml_fallback(path)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return loaded


def _format_scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "/").replace('"', '\\"')
    return f'"{escaped}"'


def _write_yaml_dict(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)
        return
    except ImportError:
        pass

    lines: List[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {_format_scalar(item)}")
                ## END for item in value...

        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_val in value.items():
                lines.append(f"  {sub_key}: {_format_scalar(sub_val)}")
            ## END for sub_key, sub_val in value.items()...

        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    ## END for key, value in data.items()...

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _resolve_optional_path(raw: Any, repo_root: Path) -> Optional[Path]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root.joinpath(candidate)
    return candidate


def _resolve_path_list(raw: Any, repo_root: Path) -> List[Path]:
    if not raw:
        return []
    items: Sequence[Any] = [raw] if isinstance(raw, (str, Path)) else list(raw)
    resolved: List[Path] = []
    for item in items:
        path = _resolve_optional_path(item, repo_root=repo_root)
        if path is not None:
            resolved.append(path)
    ## END for item in items...

    return resolved


def _merge_override_dict(merged: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if key == "process_defaults" and isinstance(value, dict):
            base_proc = dict(merged.get("process_defaults") or {})
            base_proc.update(value)
            merged["process_defaults"] = base_proc
        else:
            merged[key] = value
    ## END for key, value in override.items()...


@dataclass(init=False)
class AppPaths:
    """Resolved paths and processing defaults for offline data management workflows."""

    lab_recorder_dirs: List[Path] = field(default_factory=list)
    pickled_data_path: Optional[Path] = None
    session_catalog_path: Optional[Path] = None
    media_catalog_path: Path = field(default_factory=lambda: default_repo_root().joinpath("output", "video_catalog.sqlite"))
    media_sources_yaml: Path = field(default_factory=default_media_sources_yaml)
    process_defaults: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PROCESS_DEFAULTS))
    config_yaml_path: Path = field(default_factory=default_paths_yaml)
    local_yaml_path: Path = field(default_factory=default_local_paths_yaml)

    def __init__(self, lab_recorder_dirs: Optional[Sequence[Path]] = None, pickled_data_path: Optional[Path] = None, session_catalog_path: Optional[Path] = None,
                 media_catalog_path: Optional[Path] = None, media_sources_yaml: Optional[Path] = None,
                 video_catalog_path: Optional[Path] = None, video_sources_yaml: Optional[Path] = None,
                 process_defaults: Optional[Dict[str, Any]] = None, config_yaml_path: Optional[Path] = None, local_yaml_path: Optional[Path] = None) -> None:
        self.lab_recorder_dirs = [Path(p) for p in lab_recorder_dirs] if lab_recorder_dirs is not None else []
        self.pickled_data_path = Path(pickled_data_path) if pickled_data_path is not None else None
        self.session_catalog_path = Path(session_catalog_path) if session_catalog_path is not None else None
        cat_path = media_catalog_path if media_catalog_path is not None else video_catalog_path
        src_yaml = media_sources_yaml if media_sources_yaml is not None else video_sources_yaml
        self.media_catalog_path = Path(cat_path) if cat_path is not None else default_repo_root().joinpath("output", "video_catalog.sqlite")
        self.media_sources_yaml = Path(src_yaml) if src_yaml is not None else default_media_sources_yaml()
        merged_proc = dict(DEFAULT_PROCESS_DEFAULTS)
        if process_defaults is not None:
            merged_proc.update(process_defaults)
        self.process_defaults = merged_proc
        self.config_yaml_path = Path(config_yaml_path) if config_yaml_path is not None else default_paths_yaml()
        self.local_yaml_path = Path(local_yaml_path) if local_yaml_path is not None else default_local_paths_yaml(self.config_yaml_path)


    @property
    def video_catalog_path(self) -> Path:
        return self.media_catalog_path


    @video_catalog_path.setter
    def video_catalog_path(self, value: Path) -> None:
        self.media_catalog_path = Path(value)


    @property
    def video_sources_yaml(self) -> Path:
        return self.media_sources_yaml


    @video_sources_yaml.setter
    def video_sources_yaml(self, value: Path) -> None:
        self.media_sources_yaml = Path(value)


    @classmethod
    def load(cls, config_yaml: Optional[Union[str, Path]] = None, local_yaml: Optional[Union[str, Path]] = None, apply_env: bool = True, repo_root: Optional[Path] = None) -> "AppPaths":
        """Load paths from base YAML, optional local override YAML, and env vars."""
        root = Path(repo_root) if repo_root is not None else default_repo_root()
        base_path = Path(config_yaml) if config_yaml is not None else default_paths_yaml(repo_root=root)
        local_path = Path(local_yaml) if local_yaml is not None else default_local_paths_yaml(base_path)

        merged: Dict[str, Any] = _read_yaml_dict(base_path)
        if local_yaml is None and not local_path.exists():
            legacy_local = root.joinpath("config", "offline_analysis_paths.local.yaml")
            if legacy_local.exists() and legacy_local != local_path:
                _merge_override_dict(merged, _read_yaml_dict(legacy_local))
        _merge_override_dict(merged, _read_yaml_dict(local_path))

        raw_media_db = merged.get("media_catalog_path") or merged.get("video_catalog_path")
        raw_media_yaml = merged.get("media_sources_yaml") or merged.get("video_sources_yaml")

        if apply_env:
            env_lab_dirs = os.environ.get("PHO_LAB_RECORDER_DIRS", "").strip()
            if env_lab_dirs:
                merged["lab_recorder_dirs"] = [part.strip() for part in env_lab_dirs.split(os.pathsep) if part.strip()]
            env_pickled = os.environ.get("PHO_PICKLED_DATA_PATH", "").strip()
            if env_pickled:
                merged["pickled_data_path"] = env_pickled
            env_session_db = os.environ.get("PHO_SESSION_CATALOG_PATH", "").strip()
            if env_session_db:
                merged["session_catalog_path"] = env_session_db
            env_media_db = os.environ.get("PHO_MEDIA_CATALOG_PATH", "").strip() or os.environ.get("PHO_VIDEO_CATALOG_PATH", "").strip()
            if env_media_db:
                raw_media_db = env_media_db
            env_media_yaml = os.environ.get("PHO_MEDIA_SOURCES_YAML", "").strip() or os.environ.get("PHO_VIDEO_SOURCES_YAML", "").strip()
            if env_media_yaml:
                raw_media_yaml = env_media_yaml

        lab_dirs = _resolve_path_list(merged.get("lab_recorder_dirs"), repo_root=root)
        pickled_path = _resolve_optional_path(merged.get("pickled_data_path"), repo_root=root)
        session_db = _resolve_optional_path(merged.get("session_catalog_path"), repo_root=root)
        media_db = _resolve_optional_path(raw_media_db, repo_root=root) or root.joinpath("output", "video_catalog.sqlite")
        media_yaml = _resolve_optional_path(raw_media_yaml, repo_root=root) or default_media_sources_yaml(repo_root=root)

        proc_defaults = dict(DEFAULT_PROCESS_DEFAULTS)
        if isinstance(merged.get("process_defaults"), dict):
            proc_defaults.update(merged["process_defaults"])

        return cls(
            lab_recorder_dirs=lab_dirs,
            pickled_data_path=pickled_path,
            session_catalog_path=session_db,
            media_catalog_path=media_db,
            media_sources_yaml=media_yaml,
            process_defaults=proc_defaults,
            config_yaml_path=base_path,
            local_yaml_path=local_path,
        )


    def existing_lab_recorder_dirs(self) -> List[Path]:
        """Return configured LabRecorder directories that currently exist on disk."""
        return [path for path in self.lab_recorder_dirs if path.exists() and path.is_dir()]


    def resolved_session_catalog_path(self) -> Path:
        """Resolve the SQLite path for ``SessionCatalog``."""
        if self.session_catalog_path is not None:
            return self.session_catalog_path
        if self.pickled_data_path is not None:
            return self.pickled_data_path.joinpath("session_catalog", "sessions.sqlite")
        return default_repo_root().joinpath("output", "session_catalog", "sessions.sqlite")


    def to_dict(self) -> Dict[str, Any]:
        """Serialize paths and process defaults to a YAML-friendly dict."""
        return {
            "lab_recorder_dirs": [path.as_posix() for path in self.lab_recorder_dirs],
            "pickled_data_path": self.pickled_data_path.as_posix() if self.pickled_data_path is not None else "",
            "session_catalog_path": self.session_catalog_path.as_posix() if self.session_catalog_path is not None else "",
            "media_catalog_path": self.media_catalog_path.as_posix(),
            "media_sources_yaml": self.media_sources_yaml.as_posix(),
            "video_catalog_path": self.media_catalog_path.as_posix(),
            "video_sources_yaml": self.media_sources_yaml.as_posix(),
            "process_defaults": dict(self.process_defaults),
        }


    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Write configuration to *path* (defaults to the gitignored local override YAML)."""
        target = Path(path) if path is not None else self.local_yaml_path
        _write_yaml_dict(target, self.to_dict())
        return target


__all__ = [
    "DEFAULT_PROCESS_DEFAULTS",
    "AppPaths",
    "default_repo_root",
    "default_paths_yaml",
    "default_local_paths_yaml",
    "default_media_sources_yaml",
]
