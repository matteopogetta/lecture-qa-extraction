"""Compatibility bridge for normalized-audio metadata persistence helpers."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing import (
    normalized_audio_metadata_store as legacy_normalized_audio_metadata_store,
)


def __getattr__(name: str) -> object:
    """Expose the legacy metadata-store module through the src package."""

    return getattr(legacy_normalized_audio_metadata_store, name)


__all__ = [
    name
    for name in dir(legacy_normalized_audio_metadata_store)
    if not name.startswith("_")
]
