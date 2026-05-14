"""Compatibility bridge for the root transcription cache-store module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import cache_store as legacy_cache_store


def __getattr__(name: str) -> object:
    """Expose the legacy transcription cache store through the src package."""

    return getattr(legacy_cache_store, name)


__all__ = [
    name
    for name in dir(legacy_cache_store)
    if not name.startswith("_")
]
