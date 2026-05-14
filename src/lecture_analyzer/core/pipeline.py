"""Bootstrap orchestration plus src-based pipeline exports."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from lecture_analyzer.core import _processing_pipeline_impl as processing_pipeline_impl
from lecture_analyzer.core._processing_pipeline_impl import *  # noqa: F401,F403
from lecture_analyzer.core.config import AppConfig
from lecture_analyzer.core.models import PlaceholderResult, StageResult
from lecture_analyzer.input.validation import validate_input_path
from lecture_analyzer.output.writer import ensure_output_directory, write_result_json

LOGGER = logging.getLogger(__name__)


class LectureAnalyzerPipeline:
    """Run the current placeholder workflow end to end."""

    def __init__(self, config: AppConfig) -> None:
        """Store runtime configuration for the current execution."""

        self.config = config

    def run(self) -> Path:
        """Validate input, create the output directory, and write JSON."""

        validated_input = validate_input_path(self.config.input_path)
        output_dir = ensure_output_directory(self.config.output_dir)

        LOGGER.info("Input validated: %s", validated_input)
        LOGGER.info("Output directory ready: %s", output_dir)
        LOGGER.info("Preparing placeholder preprocessing stage")
        LOGGER.info("Preparing placeholder transcription stage")
        LOGGER.info("Preparing placeholder analysis stage")

        result = PlaceholderResult(
            run_id=str(uuid4()),
            status="placeholder-completed",
            input=str(validated_input),
            output_directory=str(output_dir),
            transcription_model=self.config.transcription_model or "not-configured",
            generated_at=datetime.now(timezone.utc).isoformat(),
            stages=[
                StageResult(
                    name="input_validation",
                    status="completed",
                    message="Input path exists and is ready for processing.",
                ),
                StageResult(
                    name="preprocessing",
                    status="pending",
                    message="Audio extraction is not implemented yet.",
                ),
                StageResult(
                    name="transcription",
                    status="pending",
                    message="Transcription backend is not implemented yet.",
                ),
                StageResult(
                    name="analysis",
                    status="pending",
                    message="Didactic segmentation is not implemented yet.",
                ),
                StageResult(
                    name="output_generation",
                    status="completed",
                    message="Placeholder JSON artifact generated successfully.",
                ),
            ],
        )
        output_path = output_dir / result.output_filename
        write_result_json(result=result, output_path=output_path)
        return output_path


def __getattr__(name: str) -> object:
    """Expose the consolidated processing pipeline through the src package."""

    return getattr(processing_pipeline_impl, name)


__all__ = [
    "LectureAnalyzerPipeline",
    *[
        name
        for name in dir(processing_pipeline_impl)
        if not name.startswith("_")
    ],
]
