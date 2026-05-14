"""Src-facing export surface for the consolidated utterance_builder module."""

from lecture_analyzer.analysis import _utterance_builder_impl as utterance_builder_impl
from lecture_analyzer.analysis._utterance_builder_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated utterance_builder implementation through src."""

    return getattr(utterance_builder_impl, name)


__all__ = [
    name
    for name in dir(utterance_builder_impl)
    if not name.startswith("_")
]
