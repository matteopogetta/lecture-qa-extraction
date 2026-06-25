"""Tests for persistent local evaluation run export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    InputSource,
    LectureSession,
    PipelineStageTiming,
    PipelineTiming,
    QAPairCandidate,
)
from lecture_analyzer.core.types import MediaType
from lecture_analyzer.output.evaluation_run_exporter import export_evaluation_run


class EvaluationRunExporterTests(unittest.TestCase):
    """Exercise local evaluation history folder creation."""

    def test_export_evaluation_run_writes_expected_local_artifacts(self) -> None:
        """One run should contain JSON, review packet, AI placeholder, and metrics."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            source_json_path = temp_path / "source_session.json"
            source_json_path.write_text('{"source": true}', encoding="utf-8")
            evaluation_root = temp_path / "evaluations"
            session = self._build_session(temp_path / "ICWROS.mp3")

            run_directory = export_evaluation_run(
                session,
                source_json_path=source_json_path,
                evaluation_root=evaluation_root,
                input_label="ICWROS",
                run_label="2026-06-21_light",
                pipeline_config=PipelineConfig(
                    pipeline_profile="light",
                    diarization_auth_token="hf_secret",
                ),
            )

            self.assertEqual(
                run_directory,
                (evaluation_root / "icwros" / "runs" / "2026-06-21_light").resolve(),
            )
            self.assertTrue((run_directory / "session.json").is_file())
            self.assertTrue((run_directory / "review_packet.md").is_file())
            self.assertTrue((run_directory / "ai_review.json").is_file())
            self.assertTrue((run_directory / "metrics.json").is_file())

            metrics = json.loads((run_directory / "metrics.json").read_text())
            self.assertEqual(metrics["run_identity"]["run_id"], "icwros/2026-06-21_light")
            self.assertEqual(metrics["run_identity"]["pipeline_profile"], "light")
            self.assertEqual(metrics["objective_metrics"]["qa_candidate_count"], 1)
            self.assertEqual(metrics["qa_quality_metrics"]["candidate_count"], 1)
            self.assertEqual(
                metrics["qa_quality_metrics"]["available_candidate_count"],
                1,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["final_quality_score"]["median"],
                0.82,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["answer_responsiveness_score"][
                    "median"
                ],
                0.77,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["quality_band_counts"]["high"],
                1,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["risk_band_counts"]["low"],
                1,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["top_risk_reasons"],
                [{"reason": "medium_confidence", "count": 1}],
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["run_quality_signal"]["band"],
                "medium",
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["run_quality_signal"]["score"],
                0.5848,
            )
            self.assertEqual(
                metrics["qa_quality_metrics"]["run_quality_signal"][
                    "useful_yield_score"
                ],
                0.1,
            )
            self.assertNotIn("candidates", metrics["qa_quality_metrics"])
            self.assertEqual(
                metrics["runtime_metrics"]["zero_or_near_zero_reused_stages"],
                ["transcription"],
            )
            self.assertFalse(metrics["privacy"]["external_upload_performed_by_pipeline"])
            self.assertIn("code_snapshot", metrics)
            self.assertEqual(
                metrics["pipeline_configuration"]["pipeline_profile"],
                "light",
            )
            self.assertEqual(
                metrics["pipeline_configuration"]["diarization_auth_token"],
                "<redacted>",
            )

            ai_review = json.loads((run_directory / "ai_review.json").read_text())
            self.assertEqual(ai_review["review_status"], "pending_manual_review")
            self.assertEqual(ai_review["run_id"], "icwros/2026-06-21_light")

    def test_export_evaluation_run_derives_unique_label_when_omitted(self) -> None:
        """Callers should not need to choose a run label for every run."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            source_json_path = temp_path / "source_session.json"
            source_json_path.write_text("{}", encoding="utf-8")
            session = self._build_session(temp_path / "ICWROS.mp3")

            run_directory = export_evaluation_run(
                session,
                source_json_path=source_json_path,
                evaluation_root=temp_path / "evaluations",
                input_label="ICWROS",
            )

            self.assertRegex(
                run_directory.name,
                r"^\d{4}-\d{2}-\d{2}_\d{6}_light_structural$",
            )

    def test_export_evaluation_run_avoids_overwriting_existing_runs(self) -> None:
        """A repeated requested run label should receive a numeric suffix."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            source_json_path = temp_path / "source_session.json"
            source_json_path.write_text("{}", encoding="utf-8")
            session = self._build_session(temp_path / "ICWROS.mp3")

            first_directory = export_evaluation_run(
                session,
                source_json_path=source_json_path,
                evaluation_root=temp_path / "evaluations",
                input_label="ICWROS",
                run_label="2026-06-21_light",
            )
            second_directory = export_evaluation_run(
                session,
                source_json_path=source_json_path,
                evaluation_root=temp_path / "evaluations",
                input_label="ICWROS",
                run_label="2026-06-21_light",
            )

            self.assertNotEqual(first_directory, second_directory)
            self.assertEqual(second_directory.name, "2026-06-21_light_2")

    @staticmethod
    def _build_session(input_path: Path) -> LectureSession:
        """Return a compact session for evaluation export tests."""

        return LectureSession(
            session_id="session_001",
            input_sources=[
                InputSource(
                    source_id="source_001",
                    original_path=input_path,
                    media_type=MediaType.AUDIO,
                    original_filename=input_path.name,
                ),
            ],
            metadata={
                "pipeline_profile": "light",
                "pipeline_execution_mode": "normal",
                "segmentation_mode": "structural",
            },
            transcript_text="What is a matrix? A matrix is an array.",
            qa_candidates=[
                QAPairCandidate(
                    qa_candidate_id="qa_001",
                    question_text="What is a matrix?",
                    answer_text="A matrix is an array.",
                    context_text="Linear algebra introduction.",
                    metadata={
                        "quality_features": {
                            "schema_version": "1.0",
                            "question_quality_score": 0.86,
                            "answer_quality_score": 0.84,
                            "answer_responsiveness_score": 0.77,
                            "context_quality_score": 0.75,
                            "grounding_quality_score": 0.90,
                            "risk_score": 0.12,
                            "final_quality_score": 0.82,
                            "quality_band": "high",
                            "risk_band": "low",
                            "risk_reasons": ["medium_confidence"],
                        },
                    },
                ),
            ],
            pipeline_timing=PipelineTiming(
                stages=[
                    PipelineStageTiming(
                        stage_name="transcription",
                        status="reused_from_cache",
                        duration_seconds=0.0,
                        used_cache=True,
                    ),
                    PipelineStageTiming(
                        stage_name="qa_extraction",
                        status="executed",
                        duration_seconds=0.2,
                    ),
                ],
            ),
        )


if __name__ == "__main__":
    unittest.main()
