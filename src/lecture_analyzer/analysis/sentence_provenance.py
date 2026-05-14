"""Src-facing export surface for the consolidated sentence_provenance module."""

from lecture_analyzer.analysis import _sentence_provenance_impl as sentence_provenance_impl
from lecture_analyzer.analysis._sentence_provenance_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated sentence_provenance implementation through src."""

    return getattr(sentence_provenance_impl, name)


__all__ = [
    name
    for name in dir(sentence_provenance_impl)
    if not name.startswith("_")
]
