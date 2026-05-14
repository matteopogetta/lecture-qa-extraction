"""Audio normalization utilities for the consolidated src package."""

from __future__ import annotations

from lecture_analyzer.preprocessing import _audio_normalizer_impl as audio_normalizer_impl
from lecture_analyzer.preprocessing._audio_normalizer_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated audio normalizer through the src package."""

    return getattr(audio_normalizer_impl, name)


__all__ = [
    name
    for name in dir(audio_normalizer_impl)
    if not name.startswith("_")
]
