"""Compatibility bridge for the root speaker-role module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import speaker_role as legacy_speaker_role


def __getattr__(name: str) -> object:
    """Expose the legacy speaker-role module through the src namespace."""

    return getattr(legacy_speaker_role, name)


__all__ = [
    name
    for name in dir(legacy_speaker_role)
    if not name.startswith("_")
]
