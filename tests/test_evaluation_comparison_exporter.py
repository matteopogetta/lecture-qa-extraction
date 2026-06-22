"""Tests for local evaluation run comparison export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.output.evaluation_comparison_exporter import (
    build_evaluation_comparison,
    export_evaluation_comparison,
)


class EvaluationComparisonExporterTests(unittest.TestCase):
    """Exercise comparison summaries across warm and cold runs."""

    def test_build_evaluation_comparison_estimates_warm_run_from_cold_reference(self) -> None:
        """A reused stage should borrow a cold duration from another run."""

        with tempfile.TemporaryDirectory() as temp_directory:
            input_dir = Path(temp_directory) / "evaluations" / "icwros"
            self._write_run(
                input_dir,
                "cold_full",
                profile="full",
                mode="structural",
                total_duration=12.0,
                stages=[
                    self._stage("transcription", "executed", 8.0),
                    self._stage("qa_extraction", "executed", 4.0),
                ],
                quality_score=3,
                runtime_value_score=2,
            )
            self._write_run(
                input_dir,
                "warm_full",
                profile="full",
                mode="structural",
                total_duration=4.1,
                stages=[
                    self._stage(
                        "transcription",
                        "reused_from_cache",
                        0.1,
                        used_cache=True,
                    ),
                    self._stage("qa_extraction", "executed", 4.0),
                ],
                quality_score=3,
                runtime_value_score=3,
            )

            comparison = build_evaluation_comparison(input_dir)
            by_id = {
                run["run_id"]: run
                for run in comparison["run_summaries"]
            }
            warm = by_id["icwros/warm_full"]

            self.assertEqual(
                warm["warm_cache_classification"]["classification"],
                "warm_or_cached",
            )
            self.assertTrue(warm["runtime"]["cold_equivalent_estimation_complete"])
            self.assertEqual(
                warm["runtime"]["cold_equivalent_runtime_seconds"],
                12.0,
            )
            self.assertEqual(
                warm["runtime"]["estimated_recomputed_reused_stage_seconds"],
                8.0,
            )
            self.assertEqual(
                comparison["rankings"]["complete_cold_runtime_asc"],
                ["icwros/cold_full", "icwros/warm_full"],
            )

    def test_build_evaluation_comparison_marks_missing_cold_references(self) -> None:
        """Warm runs should not pretend to know cold cost without references."""

        with tempfile.TemporaryDirectory() as temp_directory:
            input_dir = Path(temp_directory) / "evaluations" / "icwros"
            self._write_run(
                input_dir,
                "warm_light",
                profile="light",
                mode="structural",
                total_duration=0.2,
                stages=[
                    self._stage(
                        "transcription",
                        "reused_from_cache",
                        0.01,
                        used_cache=True,
                    ),
                    self._stage("qa_extraction", "executed", 0.19),
                ],
                quality_score=2,
                runtime_value_score=3,
            )

            comparison = build_evaluation_comparison(input_dir)
            run = comparison["run_summaries"][0]

            self.assertFalse(run["runtime"]["cold_equivalent_estimation_complete"])
            self.assertIsNone(run["runtime"]["cold_equivalent_runtime_seconds"])
            self.assertEqual(
                run["runtime"]["cold_equivalent_missing_reused_stages"],
                ["transcription"],
            )
            self.assertEqual(
                run["runtime"]["cold_equivalent_known_seconds"],
                0.19,
            )

    def test_export_evaluation_comparison_writes_json_and_markdown(self) -> None:
        """The exporter should persist comparison.json and comparison.md."""

        with tempfile.TemporaryDirectory() as temp_directory:
            input_dir = Path(temp_directory) / "evaluations" / "icwros"
            self._write_run(
                input_dir,
                "cold_light",
                profile="light",
                mode="structural",
                total_duration=1.0,
                stages=[self._stage("qa_extraction", "executed", 1.0)],
                quality_score=2,
                runtime_value_score=3,
            )

            comparison = export_evaluation_comparison(input_dir)

            self.assertEqual(comparison["input_identity"]["run_count"], 1)
            self.assertTrue((input_dir / "comparison.json").is_file())
            self.assertTrue((input_dir / "comparison.md").is_file())
            markdown = (input_dir / "comparison.md").read_text(encoding="utf-8")
            self.assertIn("Evaluation Comparison: icwros", markdown)

    @staticmethod
    def _write_run(
        input_dir: Path,
        run_label: str,
        *,
        profile: str,
        mode: str,
        total_duration: float,
        stages: list[dict[str, object]],
        quality_score: int,
        runtime_value_score: int,
    ) -> None:
        run_dir = input_dir / "runs" / run_label
        run_dir.mkdir(parents=True)
        metrics = {
            "run_identity": {
                "run_id": f"{input_dir.name}/{run_label}",
                "input_label": input_dir.name,
                "run_label": run_label,
                "pipeline_profile": profile,
                "segmentation_mode": mode,
            },
            "code_snapshot": {
                "git_commit_short": "abc123",
                "git_branch": "test",
                "git_dirty": False,
            },
            "objective_metrics": {
                "qa_candidate_count": 2,
                "qa_candidates_with_answer_count": 2,
                "qa_candidates_with_context_count": 1,
                "qa_candidates_with_review_flags_count": 0,
                "transcript_word_count": 100,
                "segment_count": 3,
                "sentence_count": 8,
            },
            "timing_summary": {
                "total_duration_seconds": total_duration,
                "any_cache_hit": any(stage.get("used_cache") for stage in stages),
                "any_artifact_reuse": any(
                    stage.get("used_existing_artifact") for stage in stages
                ),
                "reused_cache_stage_count": sum(
                    1 for stage in stages if stage.get("used_cache")
                ),
                "reused_artifact_stage_count": sum(
                    1 for stage in stages if stage.get("used_existing_artifact")
                ),
            },
            "runtime_metrics": {
                "total_duration_seconds": total_duration,
                "any_cache_hit": any(stage.get("used_cache") for stage in stages),
                "any_artifact_reuse": any(
                    stage.get("used_existing_artifact") for stage in stages
                ),
                "reused_cache_stage_count": sum(
                    1 for stage in stages if stage.get("used_cache")
                ),
                "reused_artifact_stage_count": sum(
                    1 for stage in stages if stage.get("used_existing_artifact")
                ),
            },
            "timing_stages": stages,
        }
        ai_review = {
            "review_status": "completed",
            "overall": {
                "quality_score": quality_score,
                "runtime_value_score": runtime_value_score,
                "summary": "ok",
            },
            "candidates": [
                {
                    "keep_decision": "keep",
                    "question_quality": 3,
                    "answer_quality": 3,
                    "context_quality": 2,
                    "grounding_quality": 3,
                }
            ],
            "failure_modes": [],
            "recommendations": [],
        }
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2),
            encoding="utf-8",
        )
        (run_dir / "ai_review.json").write_text(
            json.dumps(ai_review, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _stage(
        stage_name: str,
        status: str,
        duration_seconds: float,
        *,
        used_cache: bool = False,
        used_existing_artifact: bool = False,
    ) -> dict[str, object]:
        return {
            "stage_name": stage_name,
            "status": status,
            "duration_seconds": duration_seconds,
            "used_cache": used_cache,
            "used_existing_artifact": used_existing_artifact,
        }


if __name__ == "__main__":
    unittest.main()
