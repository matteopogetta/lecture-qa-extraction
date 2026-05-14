"""Reusable transcription and alignment cache helpers for the src package."""

from __future__ import annotations

from lecture_analyzer.transcription import _cache_store_impl as cache_store_impl
from lecture_analyzer.transcription._cache_store_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated transcription cache store through src."""

    return getattr(cache_store_impl, name)


__all__ = [
    name
    for name in dir(cache_store_impl)
    if not name.startswith("_")
]
