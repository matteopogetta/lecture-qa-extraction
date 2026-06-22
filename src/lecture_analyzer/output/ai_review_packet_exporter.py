"""Src-facing export surface for AI/human review packets."""

from lecture_analyzer.output import (
    _ai_review_packet_exporter_impl as ai_review_packet_exporter_impl,
)
from lecture_analyzer.output._ai_review_packet_exporter_impl import *  # noqa: F401,F403


def __getattr__(name: str) -> object:
    """Expose the consolidated AI review packet exporter through src."""

    return getattr(ai_review_packet_exporter_impl, name)


__all__ = [
    name
    for name in dir(ai_review_packet_exporter_impl)
    if not name.startswith("_")
]
