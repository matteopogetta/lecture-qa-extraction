"""Compatibility bridge for the root pyannote diarizer module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import pyannote_diarizer as legacy_pyannote_diarizer


def __getattr__(name: str) -> object:
    """Expose the legacy pyannote diarizer through the src namespace."""

    return getattr(legacy_pyannote_diarizer, name)


__all__ = [
    name
    for name in dir(legacy_pyannote_diarizer)
    if not name.startswith("_")
]
