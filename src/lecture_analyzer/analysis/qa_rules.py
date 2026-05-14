"""Compatibility bridge for the root QA rules module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis import qa_rules as legacy_qa_rules


def __getattr__(name: str) -> object:
    """Expose the legacy QA rules module through the src namespace."""

    return getattr(legacy_qa_rules, name)


__all__ = [
    name
    for name in dir(legacy_qa_rules)
    if not name.startswith("_")
]
