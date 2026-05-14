"""Compatibility bridge for the root transcription backend module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import backend as legacy_backend


def __getattr__(name: str) -> object:
    """Expose the legacy transcription backend through the src namespace."""

    return getattr(legacy_backend, name)


__all__ = [
    name
    for name in dir(legacy_backend)
    if not name.startswith("_")
]
