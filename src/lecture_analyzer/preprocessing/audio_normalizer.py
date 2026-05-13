"""Compatibility bridge for the root audio normalizer module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing import audio_normalizer as legacy_audio_normalizer


def __getattr__(name: str) -> object:
    """Expose the legacy audio-normalizer module through the src package."""

    return getattr(legacy_audio_normalizer, name)


__all__ = [
    name
    for name in dir(legacy_audio_normalizer)
    if not name.startswith("_")
]
