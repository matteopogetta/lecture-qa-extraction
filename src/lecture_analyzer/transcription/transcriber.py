"""Compatibility bridge for the root transcriber module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import transcriber as legacy_transcriber


def __getattr__(name: str) -> object:
    """Expose the legacy transcriber module through the src namespace."""

    return getattr(legacy_transcriber, name)


__all__ = [
    name
    for name in dir(legacy_transcriber)
    if not name.startswith("_")
]
