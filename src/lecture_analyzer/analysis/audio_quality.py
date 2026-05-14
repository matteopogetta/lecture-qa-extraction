"""Src-facing export surface for the consolidated audio_quality module."""

from lecture_analyzer.analysis import _audio_quality_impl as audio_quality_impl
from lecture_analyzer.analysis._audio_quality_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated audio_quality implementation through src."""

    return getattr(audio_quality_impl, name)


__all__ = [
    name
    for name in dir(audio_quality_impl)
    if not name.startswith("_")
]
