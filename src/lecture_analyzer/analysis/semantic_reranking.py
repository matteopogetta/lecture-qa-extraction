"""Src-facing export surface for the consolidated semantic_reranking module."""

from lecture_analyzer.analysis import _semantic_reranking_impl as semantic_reranking_impl
from lecture_analyzer.analysis._semantic_reranking_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated semantic_reranking implementation through src."""

    return getattr(semantic_reranking_impl, name)


__all__ = [
    name
    for name in dir(semantic_reranking_impl)
    if not name.startswith("_")
]
