"""Backwards-compatibility re-export shim for ``phoofflineeeganalysis.offline_data_manager.session_catalog``."""

from __future__ import annotations

from phoofflineeeganalysis.offline_data_manager.session_catalog import (
    SESSION_STATUSES,
    SessionCatalog,
    default_catalog_path,
    get_or_create_catalog,
    inspect_xdf_stream_chips,
)

__all__ = [
    "SESSION_STATUSES",
    "SessionCatalog",
    "default_catalog_path",
    "get_or_create_catalog",
    "inspect_xdf_stream_chips",
]
