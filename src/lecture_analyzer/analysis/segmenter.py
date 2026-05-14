"""Src-facing export surface for the consolidated segmenter module."""

from lecture_analyzer.analysis import _segmenter_impl as segmenter_impl
from lecture_analyzer.analysis._segmenter_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated segmenter implementation through src."""

    return getattr(segmenter_impl, name)


__all__ = [
    name
    for name in dir(segmenter_impl)
    if not name.startswith("_")
]
