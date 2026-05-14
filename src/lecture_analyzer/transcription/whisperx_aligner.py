"""Compatibility bridge for the root WhisperX aligner module."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transcription import whisperx_aligner as legacy_whisperx_aligner


def __getattr__(name: str) -> object:
    """Expose the legacy WhisperX aligner through the src namespace."""

    return getattr(legacy_whisperx_aligner, name)


__all__ = [
    name
    for name in dir(legacy_whisperx_aligner)
    if not name.startswith("_")
]
