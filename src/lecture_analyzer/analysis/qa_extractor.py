"""Src-facing export surface for the consolidated qa_extractor module."""

from lecture_analyzer.analysis import _qa_extractor_impl as qa_extractor_impl
from lecture_analyzer.analysis._qa_extractor_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated qa_extractor implementation through src."""

    return getattr(qa_extractor_impl, name)


__all__ = [
    name
    for name in dir(qa_extractor_impl)
    if not name.startswith("_")
]
