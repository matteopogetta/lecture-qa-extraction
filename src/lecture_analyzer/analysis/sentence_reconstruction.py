"""Src-facing export surface for the consolidated sentence_reconstruction module."""

from lecture_analyzer.analysis import _sentence_reconstruction_impl as sentence_reconstruction_impl
from lecture_analyzer.analysis._sentence_reconstruction_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated sentence_reconstruction implementation through src."""

    return getattr(sentence_reconstruction_impl, name)


__all__ = [
    name
    for name in dir(sentence_reconstruction_impl)
    if not name.startswith("_")
]
