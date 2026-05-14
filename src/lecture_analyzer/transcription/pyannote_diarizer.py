"""Pyannote-based speaker diarization for the src-based package."""

from __future__ import annotations

from lecture_analyzer.transcription import (
    _pyannote_diarizer_impl as pyannote_diarizer_impl,
)
from lecture_analyzer.transcription._pyannote_diarizer_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated pyannote diarizer through src."""

    return getattr(pyannote_diarizer_impl, name)


__all__ = [
    name
    for name in dir(pyannote_diarizer_impl)
    if not name.startswith("_")
]
