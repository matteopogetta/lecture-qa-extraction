"""Backend-specific transcription helpers for the src-based package."""

from __future__ import annotations

from lecture_analyzer.transcription import _backend_impl as backend_impl
from lecture_analyzer.transcription._backend_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated transcription backend through src."""

    return getattr(backend_impl, name)


__all__ = [
    name
    for name in dir(backend_impl)
    if not name.startswith("_")
]
