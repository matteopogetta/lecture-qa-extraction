"""Compatibility bridge for the root-pipeline session loader."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from input import session_loader as legacy_session_loader


def __getattr__(name: str) -> object:
    """Expose the legacy root session-loader module through the src package."""

    return getattr(legacy_session_loader, name)


__all__ = [
    name
    for name in dir(legacy_session_loader)
    if not name.startswith("_")
]
