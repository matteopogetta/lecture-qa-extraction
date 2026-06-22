"""Src-facing export surface for local evaluation comparisons."""

from lecture_analyzer.output import (
    _evaluation_comparison_exporter_impl as evaluation_comparison_exporter_impl,
)
from lecture_analyzer.output._evaluation_comparison_exporter_impl import *  # noqa: F401,F403


def __getattr__(name: str):
    """Expose the consolidated evaluation comparison exporter through src."""

    return getattr(evaluation_comparison_exporter_impl, name)


__all__ = [
    name
    for name in dir(evaluation_comparison_exporter_impl)
    if not name.startswith("_")
]


if __name__ == "__main__":
    raise SystemExit(evaluation_comparison_exporter_impl.main())
