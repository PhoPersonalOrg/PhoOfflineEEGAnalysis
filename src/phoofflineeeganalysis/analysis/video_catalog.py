"""Backwards-compatibility re-export shim for ``phoofflineeeganalysis.offline_data_manager.media_catalog``."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.media_catalog import (
    DEFAULT_CONSOLIDATE_ROOT,
    DEFAULT_DURATION_TOL_SEC,
    VIDEO_EXTENSIONS,
    MediaCatalog,
    SourceSpec,
    VideoCatalog,
    default_media_catalog_path,
    default_sources_yaml_path,
    default_video_catalog_path,
    load_source_specs,
    save_source_specs,
)

__all__ = [
    "SourceSpec",
    "MediaCatalog",
    "VideoCatalog",
    "VIDEO_EXTENSIONS",
    "DEFAULT_CONSOLIDATE_ROOT",
    "DEFAULT_DURATION_TOL_SEC",
    "default_media_catalog_path",
    "default_video_catalog_path",
    "default_sources_yaml_path",
    "load_source_specs",
    "save_source_specs",
]
