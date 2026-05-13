"""Compatibility bridge for the root audio extractor wrapper."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing import audio_extractor as legacy_audio_extractor


def __getattr__(name: str) -> object:
    """Expose the legacy audio-extractor module through the src package."""

    return getattr(legacy_audio_extractor, name)


__all__ = [
    name
    for name in dir(legacy_audio_extractor)
    if not name.startswith("_")
]
