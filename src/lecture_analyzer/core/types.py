"""Shared enum types for the consolidated src-based core package."""

from __future__ import annotations

from lecture_analyzer.core import _types_impl as types_impl
from lecture_analyzer.core._types_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated core types through the src package."""

    return getattr(types_impl, name)


__all__ = [
    name
    for name in dir(types_impl)
    if not name.startswith("_")
]
