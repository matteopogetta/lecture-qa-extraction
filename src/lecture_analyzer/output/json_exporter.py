"""Compatibility bridge for the root JSON exporter module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from output import json_exporter as legacy_json_exporter


def __getattr__(name: str) -> object:
    """Expose the legacy JSON exporter through the src namespace."""

    return getattr(legacy_json_exporter, name)


__all__ = [
    name
    for name in dir(legacy_json_exporter)
    if not name.startswith("_")
]
