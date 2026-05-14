"""Error classes for the consolidated src-based core package."""

from __future__ import annotations

from lecture_analyzer.core import _errors_impl as errors_impl
from lecture_analyzer.core._errors_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated core error classes through the src package."""

    return getattr(errors_impl, name)


__all__ = [
    name
    for name in dir(errors_impl)
    if not name.startswith("_")
]
