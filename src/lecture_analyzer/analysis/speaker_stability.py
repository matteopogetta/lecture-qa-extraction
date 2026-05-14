"""Compatibility bridge for the root speaker-stability module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import speaker_stability as legacy_speaker_stability


def __getattr__(name: str) -> object:
    """Expose the legacy speaker-stability module through src."""

    return getattr(legacy_speaker_stability, name)


__all__ = [
    name
    for name in dir(legacy_speaker_stability)
    if not name.startswith("_")
]
