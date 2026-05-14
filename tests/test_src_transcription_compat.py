"""Compatibility checks for the src-based transcription migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.transcription.backend import (
    BackendSegment,
    FasterWhisperBackend,
)
from lecture_analyzer.transcription.cache_store import TranscriptionCacheStore
from lecture_analyzer.transcription.pyannote_diarizer import PyannoteDiarizer
from lecture_analyzer.transcription.transcriber import Transcriber
from lecture_analyzer.transcription.transcript_merger import TranscriptMerger
from lecture_analyzer.transcription.transcript_normalizer import (
    TranscriptNormalizer,
)
from lecture_analyzer.transcription.whisperx_aligner import WhisperXAligner


class SrcTranscriptionCompatibilityTests(unittest.TestCase):
    """Ensure the src transcription namespace exposes root implementations."""

    def test_backend_symbols_are_available(self) -> None:
        """The backend module should resolve through the src namespace."""

        self.assertEqual(BackendSegment.__name__, "BackendSegment")
        self.assertEqual(FasterWhisperBackend.__name__, "FasterWhisperBackend")

    def test_transcriber_symbols_are_available(self) -> None:
        """The transcriber module should resolve through the src namespace."""

        self.assertEqual(Transcriber.__name__, "Transcriber")
        self.assertEqual(
            TranscriptionCacheStore.__name__,
            "TranscriptionCacheStore",
        )

    def test_alignment_and_diarization_symbols_are_available(self) -> None:
        """Alignment and diarization modules should resolve through src."""

        self.assertEqual(WhisperXAligner.__name__, "WhisperXAligner")
        self.assertEqual(PyannoteDiarizer.__name__, "PyannoteDiarizer")

    def test_transcript_postprocessing_symbols_are_available(self) -> None:
        """Transcript merger and normalizer should resolve through src."""

        self.assertEqual(TranscriptMerger.__name__, "TranscriptMerger")
        self.assertEqual(TranscriptNormalizer.__name__, "TranscriptNormalizer")


if __name__ == "__main__":
    unittest.main()
