"""Tests for the temporary smoke lecture analyzer workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lecture_analyzer.core.config import AppConfig
from lecture_analyzer.core.exceptions import InputValidationError
from lecture_analyzer.core.pipeline import LectureAnalyzerPipeline
from lecture_analyzer.input.validation import validate_input_path


def test_validate_input_path_rejects_missing_file(tmp_path: Path) -> None:
    """A missing input file should raise a validation error."""

    missing_path = tmp_path / "missing.mp4"

    with pytest.raises(InputValidationError):
        validate_input_path(missing_path)


def test_pipeline_creates_output_directory(tmp_path: Path) -> None:
    """Running the pipeline should create the target output directory."""

    input_file = tmp_path / "lesson.mp4"
    input_file.write_text("placeholder media", encoding="utf-8")
    output_dir = tmp_path / "generated-output"

    pipeline = LectureAnalyzerPipeline(
        config=AppConfig(
            input_path=input_file,
            output_dir=output_dir,
        ),
    )

    pipeline.run()

    assert output_dir.is_dir()


def test_pipeline_generates_placeholder_json(tmp_path: Path) -> None:
    """Running the pipeline should save a minimal placeholder JSON artifact."""

    input_file = tmp_path / "lesson.mp4"
    input_file.write_text("placeholder media", encoding="utf-8")
    output_dir = tmp_path / "output"

    pipeline = LectureAnalyzerPipeline(
        config=AppConfig(
            input_path=input_file,
            output_dir=output_dir,
            transcription_model="mock-model",
        ),
    )

    result_path = pipeline.run()
    payload = json.loads(result_path.read_text(encoding="utf-8"))

    assert result_path.exists()
    assert payload["status"] == "placeholder-completed"
    assert payload["transcription_model"] == "mock-model"
    assert payload["stages"][0]["name"] == "input_validation"
