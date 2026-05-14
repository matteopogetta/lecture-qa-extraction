"""Compatibility bridge for the root transcript-merger module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import transcript_merger as legacy_transcript_merger


def __getattr__(name: str) -> object:
    """Expose the legacy transcript merger through the src namespace."""

    return getattr(legacy_transcript_merger, name)


__all__ = [
    name
    for name in dir(legacy_transcript_merger)
    if not name.startswith("_")
]
