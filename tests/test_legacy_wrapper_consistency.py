"""Consistency checks between legacy wrappers and src-owned modules."""

from __future__ import annotations

import unittest

from analysis.segmenter import TranscriptSegmenter as RootTranscriptSegmenter
from core.config import PipelineConfig as RootPipelineConfig
from core.models import LectureSession as RootLectureSession
from input.session_loader import SessionLoader as RootSessionLoader
from output.json_exporter import JsonExporter as RootJsonExporter
from preprocessing.audio_normalizer import AudioNormalizer as RootAudioNormalizer
from transcription.transcriber import Transcriber as RootTranscriber

from lecture_analyzer.analysis.segmenter import (
    TranscriptSegmenter as SrcTranscriptSegmenter,
)
from lecture_analyzer.core.config import PipelineConfig as SrcPipelineConfig
from lecture_analyzer.core.models import LectureSession as SrcLectureSession
from lecture_analyzer.input.session_loader import SessionLoader as SrcSessionLoader
from lecture_analyzer.output.json_exporter import JsonExporter as SrcJsonExporter
from lecture_analyzer.preprocessing.audio_normalizer import (
    AudioNormalizer as SrcAudioNormalizer,
)
from lecture_analyzer.transcription.transcriber import Transcriber as SrcTranscriber


class LegacyWrapperConsistencyTests(unittest.TestCase):
    """Ensure legacy wrappers still expose the consolidated src symbols."""

    def test_core_symbols_match_src_exports(self) -> None:
        """Root core imports should resolve to the src-owned symbols."""

        self.assertIs(RootPipelineConfig, SrcPipelineConfig)
        self.assertIs(RootLectureSession, SrcLectureSession)

    def test_input_and_preprocessing_symbols_match_src_exports(self) -> None:
        """Root input and preprocessing imports should resolve through src."""

        self.assertIs(RootSessionLoader, SrcSessionLoader)
        self.assertIs(RootAudioNormalizer, SrcAudioNormalizer)

    def test_transcription_and_analysis_symbols_match_src_exports(self) -> None:
        """Root transcription and analysis imports should resolve through src."""

        self.assertIs(RootTranscriber, SrcTranscriber)
        self.assertIs(RootTranscriptSegmenter, SrcTranscriptSegmenter)

    def test_output_symbols_match_src_exports(self) -> None:
        """Root output imports should resolve to the src-owned exporter."""

        self.assertIs(RootJsonExporter, SrcJsonExporter)


if __name__ == "__main__":
    unittest.main()
