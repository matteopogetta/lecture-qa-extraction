"""Src-facing export surface for the consolidated speaker_role module."""

from lecture_analyzer.analysis import _speaker_role_impl as speaker_role_impl
from lecture_analyzer.analysis._speaker_role_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated speaker_role implementation through src."""

    return getattr(speaker_role_impl, name)


__all__ = [
    name
    for name in dir(speaker_role_impl)
    if not name.startswith("_")
]
