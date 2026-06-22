"""Tests for Markdown QA/C review packet export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.core.models import (
    LectureSession,
    PipelineStageTiming,
    PipelineTiming,
    QAPairCandidate,
    TimeRange,
)
from lecture_analyzer.output.ai_review_packet_exporter import (
    build_ai_review_packet,
    export_ai_review_packet,
)


class AIReviewPacketExporterTests(unittest.TestCase):
    """Exercise the human/chatbot review packet surface."""

    def test_build_packet_includes_instructions_transcript_and_qac(self) -> None:
        """The packet should be self-contained for semantic QA/C review."""

        session = self._build_session()

        packet = build_ai_review_packet(
            session,
            source_json_path="/tmp/session.json",
        )

        self.assertIn("# QA/C Review Packet", packet)
        self.assertIn("question_quality", packet)
        self.assertIn("answer_quality", packet)
        self.assertIn("context_quality", packet)
        self.assertIn("What is a matrix?", packet)
        self.assertIn("A matrix is a rectangular array of numbers.", packet)
        self.assertIn("We are introducing linear algebra objects.", packet)
        self.assertIn("qa_001", packet)
        self.assertIn("local_rule_based", packet)
        self.assertIn("pipeline_profile: `light`", packet)
        self.assertIn("## Timing Stage Details", packet)
        self.assertIn("| transcription | reused_from_cache | 0 | 0.0% | yes", packet)
        self.assertIn("zero_or_near_zero_reused_stages: `transcription`", packet)
        self.assertIn("warm/reuse run", packet)

    def test_export_packet_writes_markdown_file(self) -> None:
        """The exporter should create a Markdown artifact on disk."""

        session = self._build_session()

        with tempfile.TemporaryDirectory() as temp_directory:
            target_path = Path(temp_directory) / "review.md"

            exported_path = export_ai_review_packet(session, target_path)

            self.assertEqual(exported_path, target_path.resolve())
            self.assertTrue(exported_path.is_file())
            self.assertIn("QA/C Candidates", exported_path.read_text())

    @staticmethod
    def _build_session() -> LectureSession:
        """Return a compact session with one QA/C candidate."""

        return LectureSession(
            session_id="session_001",
            metadata={
                "pipeline_profile": "light",
                "pipeline_execution_mode": "normal",
                "segmentation_mode": "structural",
            },
            transcript_text=(
                "We are introducing linear algebra objects. "
                "What is a matrix? A matrix is a rectangular array of numbers."
            ),
            qa_candidates=[
                QAPairCandidate(
                    qa_candidate_id="qa_001",
                    question_text="What is a matrix?",
                    answer_text="A matrix is a rectangular array of numbers.",
                    context_text="We are introducing linear algebra objects.",
                    question_sentence_ids=["sentence_0002"],
                    answer_sentence_ids=["sentence_0003"],
                    context_sentence_ids=["sentence_0001"],
                    question_timing=TimeRange(start_seconds=4.0, end_seconds=5.0),
                    answer_timing=TimeRange(start_seconds=5.1, end_seconds=8.0),
                    confidence=0.82,
                    confidence_label="high",
                    question_type="definition",
                    context_strategy="local_topic_window",
                    context_confidence="medium",
                    reason_codes=["question_mark", "answer_in_next_sentence"],
                    metadata={
                        "pairing_debug": {
                            "search_strategy": "local_rule_based",
                            "ranking_strategy": "rule_based",
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
                        metadata={"backend": "fake"},
                    ),
                    PipelineStageTiming(
                        stage_name="qa_extraction",
                        status="executed",
                        duration_seconds=0.25,
                    ),
                    PipelineStageTiming(
                        stage_name="total_pipeline_execution",
                        status="completed",
                        duration_seconds=2.0,
                        metadata={
                            "pipeline_execution_mode": "normal",
                            "full_recompute_requested": False,
                        },
                    ),
                ],
            ),
        )


if __name__ == "__main__":
    unittest.main()
