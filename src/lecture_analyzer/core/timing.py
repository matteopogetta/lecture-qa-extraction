"""Compatibility bridge for the root-pipeline timing helpers."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import timing as legacy_timing


def __getattr__(name: str) -> object:
    """Expose the legacy root timing module through the src package."""

    return getattr(legacy_timing, name)


__all__ = [
    name
    for name in dir(legacy_timing)
    if not name.startswith("_")
]
