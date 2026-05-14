"""Src-facing export surface for the consolidated json_exporter module."""

from lecture_analyzer.output import _json_exporter_impl as json_exporter_impl
from lecture_analyzer.output._json_exporter_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated json_exporter implementation through src."""

    return getattr(json_exporter_impl, name)


__all__ = [
    name
    for name in dir(json_exporter_impl)
    if not name.startswith("_")
]
