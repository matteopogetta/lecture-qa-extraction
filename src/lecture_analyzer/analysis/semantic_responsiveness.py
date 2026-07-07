"""Src-facing export surface for semantic responsiveness scoring."""

from lecture_analyzer.analysis import (
    _semantic_responsiveness_impl as semantic_responsiveness_impl,
)
from lecture_analyzer.analysis._semantic_responsiveness_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated semantic responsiveness implementation."""

    return getattr(semantic_responsiveness_impl, name)


__all__ = [
    name
    for name in dir(semantic_responsiveness_impl)
    if not name.startswith("_")
]
