"""Compatibility bridge for the root audio-quality analysis module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import audio_quality as legacy_audio_quality


def __getattr__(name: str) -> object:
    """Expose the legacy audio-quality module through the src namespace."""

    return getattr(legacy_audio_quality, name)


__all__ = [
    name
    for name in dir(legacy_audio_quality)
    if not name.startswith("_")
]
