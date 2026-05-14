"""Compatibility bridge for the root speaker-attribution module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import speaker_attribution as legacy_speaker_attribution


def __getattr__(name: str) -> object:
    """Expose the legacy speaker attribution module through src."""

    return getattr(legacy_speaker_attribution, name)


__all__ = [
    name
    for name in dir(legacy_speaker_attribution)
    if not name.startswith("_")
]
