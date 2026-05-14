"""Timing helpers for the consolidated src-based core package."""

from __future__ import annotations

from lecture_analyzer.core import _timing_impl as timing_impl
from lecture_analyzer.core._timing_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated timing helpers through the src package."""

    return getattr(timing_impl, name)


__all__ = [
    name
    for name in dir(timing_impl)
    if not name.startswith("_")
]
