"""Conservative transcript normalization utilities for the src package."""

from __future__ import annotations

from lecture_analyzer.transcription import (
    _transcript_normalizer_impl as transcript_normalizer_impl,
)
from lecture_analyzer.transcription._transcript_normalizer_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated transcript normalizer through src."""

    return getattr(transcript_normalizer_impl, name)


__all__ = [
    name
    for name in dir(transcript_normalizer_impl)
    if not name.startswith("_")
]
