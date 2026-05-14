"""Compatibility bridge for the root sentence-provenance validation module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from output import sentence_provenance_validation as legacy_provenance_validation


def __getattr__(name: str) -> object:
    """Expose the legacy provenance validation module through src."""

    return getattr(legacy_provenance_validation, name)


__all__ = [
    name
    for name in dir(legacy_provenance_validation)
    if not name.startswith("_")
]
