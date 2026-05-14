"""Tests for faster-whisper backend configuration wiring."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.transcription.backend import FasterWhisperBackend


class _FakeWhisperModel:
    """Small test double that records initialization arguments."""

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.__class__.calls.append((args, kwargs))


class TranscriptionBackendTests(unittest.TestCase):
    """Verify explicit compute-type wiring for faster-whisper."""

    def setUp(self) -> None:
        """Reset recorded model initialization calls before each test."""

        _FakeWhisperModel.calls.clear()

    def test_auto_compute_type_keeps_default_whisper_model_init(self) -> None:
        """Auto compute type should preserve the previous backend behavior."""

        backend = FasterWhisperBackend(PipelineConfig(transcription_compute_type="auto"))

        with patch.dict(
            sys.modules,
            {"faster_whisper": types.SimpleNamespace(WhisperModel=_FakeWhisperModel)},
        ):
            backend._get_model()

        self.assertEqual(len(_FakeWhisperModel.calls), 1)
        args, kwargs = _FakeWhisperModel.calls[0]
        self.assertEqual(args, ("small",))
        self.assertEqual(kwargs, {})

    def test_explicit_compute_type_is_forwarded_to_whisper_model(self) -> None:
        """A configured compute type should be passed through explicitly."""

        backend = FasterWhisperBackend(
            PipelineConfig(transcription_compute_type="float32"),
        )

        with patch.dict(
            sys.modules,
            {"faster_whisper": types.SimpleNamespace(WhisperModel=_FakeWhisperModel)},
        ):
            backend._get_model()

        self.assertEqual(len(_FakeWhisperModel.calls), 1)
        args, kwargs = _FakeWhisperModel.calls[0]
        self.assertEqual(args, ("small",))
        self.assertEqual(kwargs, {"compute_type": "float32"})


if __name__ == "__main__":
    unittest.main()
