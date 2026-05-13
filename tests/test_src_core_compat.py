"""Compatibility checks for the src-based core migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.core.config import AppConfig, PipelineConfig
from lecture_analyzer.core.errors import IngestionError
from lecture_analyzer.core.models import LectureSession, PlaceholderResult, StageResult
from lecture_analyzer.core.pipeline import (
    LectureAnalyzerPipeline,
    LectureProcessingPipeline,
)
from lecture_analyzer.core.timing import PipelineTimer
from lecture_analyzer.core.types import MediaType


class SrcCoreCompatibilityTests(unittest.TestCase):
    """Ensure both placeholder and root-pipeline symbols stay importable."""

    def test_placeholder_exports_remain_available(self) -> None:
        """The smoke-mode placeholder API should remain stable."""

        self.assertEqual(AppConfig.__name__, "AppConfig")
        self.assertEqual(LectureAnalyzerPipeline.__name__, "LectureAnalyzerPipeline")
        self.assertEqual(PlaceholderResult.__name__, "PlaceholderResult")
        self.assertEqual(StageResult.__name__, "StageResult")

    def test_root_pipeline_exports_are_available_via_src_namespace(self) -> None:
        """The root pipeline symbols should resolve through lecture_analyzer.core."""

        self.assertEqual(PipelineConfig.__name__, "PipelineConfig")
        self.assertEqual(
            LectureProcessingPipeline.__name__,
            "LectureProcessingPipeline",
        )
        self.assertEqual(LectureSession.__name__, "LectureSession")
        self.assertEqual(PipelineTimer.__name__, "PipelineTimer")
        self.assertEqual(MediaType.VIDEO.value, "video")
        self.assertTrue(issubclass(IngestionError, Exception))


if __name__ == "__main__":
    unittest.main()
