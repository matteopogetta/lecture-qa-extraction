"""Normalized-audio metadata persistence helpers for the src package."""

from __future__ import annotations

from lecture_analyzer.preprocessing import (
    _normalized_audio_metadata_store_impl as normalized_audio_metadata_store_impl,
)
from lecture_analyzer.preprocessing._normalized_audio_metadata_store_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated metadata store through the src package."""

    return getattr(normalized_audio_metadata_store_impl, name)


__all__ = [
    name
    for name in dir(normalized_audio_metadata_store_impl)
    if not name.startswith("_")
]
