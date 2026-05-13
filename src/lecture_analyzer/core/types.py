"""Compatibility bridge for the root-pipeline shared enum types."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import types as legacy_types


def __getattr__(name: str) -> object:
    """Expose the legacy root types module through the src package."""

    return getattr(legacy_types, name)


__all__ = [
    name
    for name in dir(legacy_types)
    if not name.startswith("_")
]
