"""Compatibility bridge for the root semantic-reranking module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import semantic_reranking as legacy_semantic_reranking


def __getattr__(name: str) -> object:
    """Expose the legacy semantic reranking module through src."""

    return getattr(legacy_semantic_reranking, name)


__all__ = [
    name
    for name in dir(legacy_semantic_reranking)
    if not name.startswith("_")
]
