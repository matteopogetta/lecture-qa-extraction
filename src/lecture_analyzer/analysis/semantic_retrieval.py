"""Src-facing export surface for the consolidated semantic_retrieval module."""

from lecture_analyzer.analysis import _semantic_retrieval_impl as semantic_retrieval_impl
from lecture_analyzer.analysis._semantic_retrieval_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated semantic_retrieval implementation through src."""

    return getattr(semantic_retrieval_impl, name)


__all__ = [
    name
    for name in dir(semantic_retrieval_impl)
    if not name.startswith("_")
]
