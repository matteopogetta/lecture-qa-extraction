"""Src-facing export surface for the consolidated sentence_provenance_validation module."""

from lecture_analyzer.output import _sentence_provenance_validation_impl as sentence_provenance_validation_impl
from lecture_analyzer.output._sentence_provenance_validation_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated sentence_provenance_validation implementation through src."""

    return getattr(sentence_provenance_validation_impl, name)


__all__ = [
    name
    for name in dir(sentence_provenance_validation_impl)
    if not name.startswith("_")
]
