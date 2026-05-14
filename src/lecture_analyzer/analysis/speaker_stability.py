"""Src-facing export surface for the consolidated speaker_stability module."""

from lecture_analyzer.analysis import _speaker_stability_impl as speaker_stability_impl
from lecture_analyzer.analysis._speaker_stability_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated speaker_stability implementation through src."""

    return getattr(speaker_stability_impl, name)


__all__ = [
    name
    for name in dir(speaker_stability_impl)
    if not name.startswith("_")
]
