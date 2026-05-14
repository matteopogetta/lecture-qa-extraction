"""Src-facing export surface for the consolidated qa_rules module."""

from lecture_analyzer.analysis import _qa_rules_impl as qa_rules_impl
from lecture_analyzer.analysis._qa_rules_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated qa_rules implementation through src."""

    return getattr(qa_rules_impl, name)


__all__ = [
    name
    for name in dir(qa_rules_impl)
    if not name.startswith("_")
]
