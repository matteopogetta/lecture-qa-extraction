"""Src-facing export surface for the consolidated debug_excel_exporter module."""

from lecture_analyzer.output import _debug_excel_exporter_impl as debug_excel_exporter_impl
from lecture_analyzer.output._debug_excel_exporter_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated debug_excel_exporter implementation through src."""

    return getattr(debug_excel_exporter_impl, name)


__all__ = [
    name
    for name in dir(debug_excel_exporter_impl)
    if not name.startswith("_")
]
