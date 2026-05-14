"""WhisperX-based forced alignment for the src-based package."""

from __future__ import annotations

from lecture_analyzer.transcription import (
    _whisperx_aligner_impl as whisperx_aligner_impl,
)
from lecture_analyzer.transcription._whisperx_aligner_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated WhisperX aligner through src."""

    return getattr(whisperx_aligner_impl, name)


__all__ = [
    name
    for name in dir(whisperx_aligner_impl)
    if not name.startswith("_")
]
