"""Src-facing export surface for persistent local evaluation runs."""

from lecture_analyzer.output import (
    _evaluation_run_exporter_impl as evaluation_run_exporter_impl,
)
from lecture_analyzer.output._evaluation_run_exporter_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated evaluation-run exporter through src."""

    return getattr(evaluation_run_exporter_impl, name)


__all__ = [
    name
    for name in dir(evaluation_run_exporter_impl)
    if not name.startswith("_")
]
