"""Compatibility bridge for the root utterance-builder module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import utterance_builder as legacy_utterance_builder


def __getattr__(name: str) -> object:
    """Expose the legacy utterance builder module through src."""

    return getattr(legacy_utterance_builder, name)


__all__ = [
    name
    for name in dir(legacy_utterance_builder)
    if not name.startswith("_")
]
