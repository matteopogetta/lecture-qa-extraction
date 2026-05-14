"""Transcription orchestration for the src-based package."""

from __future__ import annotations

from lecture_analyzer.transcription import _transcriber_impl as transcriber_impl
from lecture_analyzer.transcription._transcriber_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated transcriber through the src namespace."""

    return getattr(transcriber_impl, name)


__all__ = [
    name
    for name in dir(transcriber_impl)
    if not name.startswith("_")
]
