"""Compatibility bridge for the root debug Excel exporter module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from output import debug_excel_exporter as legacy_debug_excel_exporter


def __getattr__(name: str) -> object:
    """Expose the legacy debug Excel exporter through the src namespace."""

    return getattr(legacy_debug_excel_exporter, name)


__all__ = [
    name
    for name in dir(legacy_debug_excel_exporter)
    if not name.startswith("_")
]
