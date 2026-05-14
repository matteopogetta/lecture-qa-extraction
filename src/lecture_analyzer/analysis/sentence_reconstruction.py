"""Compatibility bridge for the root sentence-reconstruction module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import sentence_reconstruction as legacy_sentence_reconstruction


def __getattr__(name: str) -> object:
    """Expose the legacy sentence reconstruction module through src."""

    return getattr(legacy_sentence_reconstruction, name)


__all__ = [
    name
    for name in dir(legacy_sentence_reconstruction)
    if not name.startswith("_")
]
