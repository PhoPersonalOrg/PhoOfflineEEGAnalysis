"""Backwards-compatibility re-export shim for ``phoofflineeeganalysis.offline_data_manager.app_paths``."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.app_paths import (
    DEFAULT_PROCESS_DEFAULTS,
    AppPaths,
    default_local_paths_yaml,
    default_media_sources_yaml,
    default_paths_yaml,
    default_repo_root,
)

__all__ = [
    "DEFAULT_PROCESS_DEFAULTS",
    "AppPaths",
    "default_repo_root",
    "default_paths_yaml",
    "default_local_paths_yaml",
    "default_media_sources_yaml",
]
