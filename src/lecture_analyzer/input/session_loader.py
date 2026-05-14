"""Session loading utilities for the consolidated src-based input package."""

from __future__ import annotations

from lecture_analyzer.input import _session_loader_impl as session_loader_impl
from lecture_analyzer.input._session_loader_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated session loader through the src package."""

    return getattr(session_loader_impl, name)


__all__ = [
    name
    for name in dir(session_loader_impl)
    if not name.startswith("_")
]
