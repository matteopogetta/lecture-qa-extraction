"""Session-level transcript merge utilities for the src-based package."""

from __future__ import annotations

from lecture_analyzer.transcription import (
    _transcript_merger_impl as transcript_merger_impl,
)
from lecture_analyzer.transcription._transcript_merger_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated transcript merger through src."""

    return getattr(transcript_merger_impl, name)


__all__ = [
    name
    for name in dir(transcript_merger_impl)
    if not name.startswith("_")
]
