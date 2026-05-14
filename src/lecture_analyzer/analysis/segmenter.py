"""Compatibility bridge for the root segmenter module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import segmenter as legacy_segmenter


def __getattr__(name: str) -> object:
    """Expose the legacy segmenter through the src namespace."""

    return getattr(legacy_segmenter, name)


__all__ = [
    name
    for name in dir(legacy_segmenter)
    if not name.startswith("_")
]
