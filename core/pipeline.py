"""Legacy wrapper for the consolidated src-based core pipeline module."""

from __future__ import annotations

from typing import Any

from lecture_analyzer.core import _processing_pipeline_impl as processing_pipeline_impl
from lecture_analyzer.core.pipeline import *  # noqa: F401,F403

export_run_to_excel = processing_pipeline_impl.export_run_to_excel


class LectureProcessingPipeline(processing_pipeline_impl.LectureProcessingPipeline):
    """Legacy proxy that preserves root-module patch points during tests."""

    def process(self, *args: Any, **kwargs: Any):
        """Delegate to the consolidated pipeline while honoring root patches."""

        original_exporter = processing_pipeline_impl.export_run_to_excel
        processing_pipeline_impl.export_run_to_excel = export_run_to_excel
        try:
            return super().process(*args, **kwargs)
        finally:
            processing_pipeline_impl.export_run_to_excel = original_exporter
