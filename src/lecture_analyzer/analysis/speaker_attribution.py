"""Src-facing export surface for the consolidated speaker_attribution module."""

from lecture_analyzer.analysis import _speaker_attribution_impl as speaker_attribution_impl
from lecture_analyzer.analysis._speaker_attribution_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated speaker_attribution implementation through src."""

    return getattr(speaker_attribution_impl, name)


__all__ = [
    name
    for name in dir(speaker_attribution_impl)
    if not name.startswith("_")
]
