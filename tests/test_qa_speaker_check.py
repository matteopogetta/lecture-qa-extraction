from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

import numpy as np

from lecture_analyzer.analysis.qa_speaker_check import (
    DIFFERENT_SPEAKER_LIKELY,
    SAME_SPEAKER_SUSPECTED,
    SPEAKER_CHECK_OVERLAPPING_SPANS,
    SPEAKER_CHECK_QUESTION_SPAN_EXTENDED,
    SPEAKER_PENALTY_WAIVED_RESPONSIVE,
    SPEAKER_CHECK_UNAVAILABLE,
    SPEAKER_RESCUED_CANDIDATE,
    SPEAKER_RESCUE_REJECTED_CONVERSATIONAL,
    SPEAKER_RESCUE_REJECTED_TEXT_QUALITY,
    QASpeakerCheckService,
    SpeakerCheckConfig,
    apply_qa_speaker_check,
)
from lecture_analyzer.analysis._qa_extractor_impl import QAPairExtractor
from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AudioSource,
    LectureSession,
    QAPairCandidate,
    Sentence,
    TimeRange,
    Utterance,
)
from lecture_analyzer.core.types import MediaType


class FakeSpeakerBackend:
    model_name = "fake"

    def __init__(
        self,
        *,
        same: bool = True,
        available: bool = True,
        similarity: float | None = None,
    ) -> None:
        self.same = same
        self.available = available
        self.similarity = similarity

    def is_available(self) -> bool:
        return self.available

    def embed(self, audio_path: Path) -> np.ndarray:
        if audio_path.name.endswith("_question.wav"):
            return np.array([1.0, 0.0])
        if self.similarity is not None:
            similarity = max(-1.0, min(1.0, self.similarity))
            return np.array([similarity, (1.0 - similarity**2) ** 0.5])
        if self.same:
            return np.array([1.0, 0.0])
        return np.array([0.0, 1.0])


class QASpeakerCheckTest(unittest.TestCase):
    def test_disabled_check_keeps_pre_speaker_confidence(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root))

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=False),
            )

        candidate = session.qa_candidates[0]
        self.assertFalse(metrics.enabled)
        self.assertEqual(candidate.confidence, 0.8)
        self.assertEqual(candidate.confidence_score, 0.8)
        self.assertNotIn("speaker_check", candidate.metadata)

    def test_missing_model_marks_candidates_unavailable(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root))

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True),
            )

        candidate = session.qa_candidates[0]
        self.assertFalse(metrics.model_available)
        self.assertEqual(metrics.unavailable_candidate_count, 1)
        self.assertIn("model_not_configured_or_missing", metrics.notes)
        self.assertIn(SPEAKER_CHECK_UNAVAILABLE, candidate.review_flags)
        self.assertEqual(candidate.confidence, 0.8)

    def test_short_span_is_unavailable_without_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root), question_end=1.0)
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True),
                backend=FakeSpeakerBackend(),
            )

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertEqual(metrics.unavailable_candidate_count, 1)
        self.assertEqual(
            candidate.metadata["speaker_check"]["note"],
            "span_too_short_or_missing",
        )
        self.assertEqual(candidate.confidence, 0.8)

    def test_overlapping_question_answer_spans_are_skipped(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(
                Path(temp_root),
                question_sentence_ids=["sentence_0001"],
                answer_sentence_ids=["sentence_0001"],
            )
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True),
                backend=FakeSpeakerBackend(),
            )

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertEqual(metrics.skipped_candidate_count, 1)
        self.assertEqual(metrics.checked_candidate_count, 0)
        self.assertIn(SPEAKER_CHECK_OVERLAPPING_SPANS, candidate.review_flags)
        self.assertIn(SPEAKER_CHECK_OVERLAPPING_SPANS, candidate.reason_codes)
        self.assertEqual(candidate.metadata["speaker_check"]["status"], "skipped")
        self.assertEqual(candidate.confidence, 0.8)

    def test_short_question_span_extends_to_utterance_audio(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(
                Path(temp_root),
                question_end=1.0,
                include_question_utterance=True,
            )
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True, min_span_seconds=1.5),
                backend=FakeSpeakerBackend(),
            )

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True, min_span_seconds=1.5),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertEqual(metrics.checked_candidate_count, 1)
        self.assertEqual(metrics.unavailable_candidate_count, 0)
        self.assertIn(SPEAKER_CHECK_QUESTION_SPAN_EXTENDED, candidate.review_flags)
        self.assertEqual(
            candidate.metadata["speaker_check"]["note"],
            "question_audio_span_extended_to_utterance",
        )
        self.assertEqual(
            candidate.metadata["speaker_check"]["original_question_duration_seconds"],
            1.0,
        )
        self.assertEqual(
            candidate.metadata["speaker_check"]["question_duration_seconds"],
            2.0,
        )

    def test_same_speaker_suspected_adds_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root))
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                backend=FakeSpeakerBackend(same=True),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertEqual(candidate.metadata["speaker_check"]["penalty_applied"], 0.3)
        self.assertFalse(
            any("legacy" in key for key in candidate.metadata["speaker_check"]),
        )
        self.assertNotIn("raw_confidence", candidate.metadata["speaker_check"])
        self.assertEqual(candidate.metadata["speaker_check"]["original_confidence"], 0.8)
        self.assertEqual(candidate.confidence, 0.5)

    def test_intra_sentence_marker_alone_does_not_exempt_same_speaker_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(
                Path(temp_root),
                reason_codes=["intra_sentence_qa"],
            )
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                backend=FakeSpeakerBackend(same=True),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertFalse(
            candidate.metadata["speaker_check"]["exempted_by_socratic_pattern"],
        )
        self.assertEqual(candidate.metadata["speaker_check"]["penalty_applied"], 0.3)
        self.assertEqual(candidate.confidence, 0.5)

    def test_socratic_candidate_is_exempt_from_same_speaker_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(
                Path(temp_root),
                reason_codes=["same_unit_local_answer"],
            )
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                backend=FakeSpeakerBackend(same=True),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertTrue(
            candidate.metadata["speaker_check"]["exempted_by_socratic_pattern"],
        )
        self.assertEqual(candidate.confidence, 0.8)

    def test_responsive_definition_candidate_waives_same_speaker_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(
                Path(temp_root),
                question_text="Cosa vuol dire marker alpha?",
                answer_text="Significa che marker alpha definisce response beta.",
                reason_codes=[
                    "answer_responsiveness_strong",
                    "answer_responsiveness_anchor",
                    "answer_it_cioe",
                ],
            )
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                backend=FakeSpeakerBackend(same=True),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True, same_speaker_penalty=0.3),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertIn(SPEAKER_PENALTY_WAIVED_RESPONSIVE, candidate.reason_codes)
        self.assertTrue(
            candidate.metadata["speaker_check"]["waived_by_responsive_pattern"],
        )
        self.assertEqual(candidate.metadata["speaker_check"]["penalty_applied"], 0.0)
        self.assertEqual(candidate.confidence, 0.8)

    def test_same_speaker_penalty_is_graduated_by_similarity(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root))
            service = QASpeakerCheckService(
                SpeakerCheckConfig(
                    enabled=True,
                    same_speaker_threshold=0.72,
                    same_speaker_full_penalty_threshold=0.85,
                    same_speaker_penalty=0.26,
                ),
                backend=FakeSpeakerBackend(similarity=0.785),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(
                    enabled=True,
                    same_speaker_threshold=0.72,
                    same_speaker_full_penalty_threshold=0.85,
                    same_speaker_penalty=0.26,
                ),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertEqual(candidate.metadata["speaker_check"]["penalty_applied"], 0.13)
        self.assertFalse(
            any("legacy" in key for key in candidate.metadata["speaker_check"]),
        )
        self.assertEqual(candidate.metadata["speaker_check"]["original_confidence"], 0.8)
        self.assertEqual(candidate.confidence, 0.67)

    def test_intra_sentence_marker_changes_only_by_graduated_penalty(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root), reason_codes=["intra_sentence_qa"])
            service = QASpeakerCheckService(
                SpeakerCheckConfig(
                    enabled=True,
                    same_speaker_threshold=0.72,
                    same_speaker_full_penalty_threshold=0.85,
                    same_speaker_penalty=0.26,
                ),
                backend=FakeSpeakerBackend(similarity=0.785),
            )

            apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(
                    enabled=True,
                    same_speaker_threshold=0.72,
                    same_speaker_full_penalty_threshold=0.85,
                    same_speaker_penalty=0.26,
                ),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertIn(SAME_SPEAKER_SUSPECTED, candidate.review_flags)
        self.assertEqual(candidate.metadata["speaker_check"]["penalty_applied"], 0.13)
        self.assertFalse(
            any("legacy" in key for key in candidate.metadata["speaker_check"]),
        )
        self.assertEqual(candidate.metadata["speaker_check"]["original_confidence"], 0.8)
        self.assertEqual(candidate.confidence, 0.67)

    def test_different_speaker_likely_flag(self) -> None:
        with TemporaryDirectory() as temp_root:
            session = _session(Path(temp_root))
            service = QASpeakerCheckService(
                SpeakerCheckConfig(enabled=True),
                backend=FakeSpeakerBackend(same=False),
            )

            metrics = apply_qa_speaker_check(
                session,
                SpeakerCheckConfig(enabled=True),
                service=service,
            )

        candidate = session.qa_candidates[0]
        self.assertEqual(metrics.checked_candidate_count, 1)
        self.assertIn(DIFFERENT_SPEAKER_LIKELY, candidate.review_flags)
        self.assertEqual(
            candidate.metadata["speaker_check"]["speaker_similarity_score"],
            0.0,
        )
        self.assertEqual(candidate.metadata["speaker_check"]["bonus_applied"], 0.04)
        self.assertEqual(candidate.confidence, 0.84)

    def test_pipeline_config_builds_speaker_check_config(self) -> None:
        with TemporaryDirectory() as temp_root:
            model_path = Path(temp_root) / "model"
            config = PipelineConfig(
                qa_speaker_check_enabled=True,
                qa_speaker_check_model_path=model_path,
                qa_speaker_check_same_speaker_penalty=0.4,
                qa_speaker_check_same_full_penalty_threshold=0.9,
                qa_speaker_check_different_speaker_bonus=0.06,
                qa_speaker_rescue_max_checks_per_run=12,
                qa_speaker_rescue_max_candidates_per_run=3,
                qa_speaker_rescue_min_confidence_margin=0.05,
                qa_speaker_rescue_min_text_quality_score=0.57,
            )

            speaker_config = config.speaker_check_config()

        self.assertTrue(speaker_config.enabled)
        self.assertEqual(speaker_config.model_path, model_path.resolve())
        self.assertEqual(speaker_config.same_speaker_penalty, 0.4)
        self.assertEqual(speaker_config.same_speaker_full_penalty_threshold, 0.9)
        self.assertEqual(speaker_config.different_speaker_bonus, 0.06)
        self.assertEqual(speaker_config.rescue_max_checks_per_run, 12)
        self.assertEqual(speaker_config.rescue_max_candidates_per_run, 3)
        self.assertEqual(speaker_config.rescue_min_confidence_margin, 0.05)
        self.assertEqual(speaker_config.rescue_min_text_quality_score, 0.57)

    def test_speaker_rescue_recovers_soft_gate_with_different_voice(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertEqual(emitted, [candidate])
        self.assertEqual(suppressed_counts, {})
        self.assertIn(SPEAKER_RESCUED_CANDIDATE, candidate.reason_codes)
        self.assertIn(SPEAKER_RESCUED_CANDIDATE, candidate.review_flags)
        self.assertIn(DIFFERENT_SPEAKER_LIKELY, candidate.review_flags)
        self.assertTrue(candidate.metadata["speaker_check_precomputed"])
        self.assertEqual(
            stats["rescued_by_gate_reasons"],
            {"surface_answer_cue_risk": 1},
        )

    def test_speaker_rescue_rejects_same_uncertain_and_unavailable_voice(self) -> None:
        cases = [
            {"similarity": 0.95, "available": True},
            {"similarity": 0.60, "available": True},
            {"similarity": 0.1, "available": False},
        ]
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temp_root:
                extractor, session, candidate = _speaker_rescue_fixture(
                    Path(temp_root),
                    similarity=case["similarity"],
                    available=case["available"],
                )
                emitted: list[QAPairCandidate] = []
                suppressed_counts = {"weak_answer_responsiveness": 1}

                stats = extractor._apply_speaker_assisted_rescue(
                    session=session,
                    suppressed_candidates=[(candidate, "weak_answer_responsiveness")],
                    emitted_candidates=emitted,
                    suppressed_by_gate_reason_counts=suppressed_counts,
                )

            self.assertEqual(stats["rescued_candidate_count"], 0)
            self.assertEqual(emitted, [])
            self.assertEqual(suppressed_counts, {"weak_answer_responsiveness": 1})
            self.assertNotIn(SPEAKER_RESCUED_CANDIDATE, candidate.review_flags)

    def test_speaker_rescue_rejects_hard_gate_candidate(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                risk_reasons=["surface_answer_cue_risk", "question_span_integrity"],
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["attempted_candidate_count"], 0)
        self.assertEqual(stats["rescued_candidate_count"], 0)
        self.assertEqual(emitted, [])
        self.assertEqual(suppressed_counts, {"surface_answer_cue_risk": 1})

    def test_speaker_rescue_rejects_low_text_quality_candidate(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                question_quality=0.44,
                answer_quality=0.82,
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 0)
        self.assertEqual(stats["rejected_candidate_count"], 1)
        self.assertEqual(
            stats["rejected_reason_counts"],
            {SPEAKER_RESCUE_REJECTED_TEXT_QUALITY: 1},
        )
        self.assertEqual(emitted, [])
        self.assertIn(SPEAKER_RESCUE_REJECTED_TEXT_QUALITY, candidate.reason_codes)

    def test_speaker_rescue_rejects_conversational_answer(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                answer_text=(
                    "Thanks for that question and thanks to everyone for the "
                    "overview."
                ),
                question_quality=0.8,
                answer_quality=0.8,
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 0)
        self.assertEqual(stats["rejected_candidate_count"], 1)
        self.assertEqual(
            stats["rejected_reason_counts"],
            {SPEAKER_RESCUE_REJECTED_CONVERSATIONAL: 1},
        )
        self.assertEqual(emitted, [])
        self.assertIn(SPEAKER_RESCUE_REJECTED_CONVERSATIONAL, candidate.reason_codes)

    def test_speaker_rescue_trims_question_focus_and_completes_answer(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                question_text="Setup words before the real focus how does lift work",
                answer_text="Lift starts with pressure that",
                normalized_question_text=(
                    "setup words before the real focus how does lift work"
                ),
                answer_sentence_ids=["answer_sentence_1"],
                answer_boundary_status="truncated",
                question_quality=0.75,
                answer_quality=0.72,
            )
            session.sentences = [
                Sentence(
                    sentence_id="answer_sentence_1",
                    audio_source_id="audio_1",
                    text="Lift starts with pressure that",
                    start_seconds=2.0,
                    end_seconds=3.0,
                ),
                Sentence(
                    sentence_id="answer_sentence_2",
                    audio_source_id="audio_1",
                    text="keeps the wing supported",
                    start_seconds=3.0,
                    end_seconds=4.0,
                ),
            ]
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertEqual(candidate.question_text, "How does lift work")
        self.assertEqual(
            candidate.answer_text,
            "Lift starts with pressure that keeps the wing supported",
        )
        self.assertEqual(
            candidate.metadata["speaker_rescue_trim"]["answer_completion_extended_by"],
            "answer_sentence_2",
        )

    def test_speaker_rescue_extends_one_more_sentence_on_suspended_answer(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                answer_text="The answer starts",
                answer_sentence_ids=["answer_sentence_1"],
                answer_boundary_status="truncated",
                question_quality=0.75,
                answer_quality=0.72,
            )
            session.sentences = [
                Sentence(
                    sentence_id="answer_sentence_1",
                    audio_source_id="audio_1",
                    text="The answer starts",
                    start_seconds=2.0,
                    end_seconds=3.0,
                ),
                *[
                    Sentence(
                        sentence_id=f"answer_sentence_{index}",
                        audio_source_id="audio_1",
                        text=f"with clause {index} and",
                        start_seconds=float(index + 1),
                        end_seconds=float(index + 2),
                    )
                    for index in range(2, 10)
                ],
                Sentence(
                    sentence_id="answer_sentence_10",
                    audio_source_id="audio_1",
                    text="the complete idea lands",
                    start_seconds=11.0,
                    end_seconds=12.0,
                ),
            ]
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertTrue(candidate.answer_text.endswith("the complete idea lands"))
        self.assertEqual(
            candidate.metadata["speaker_rescue_trim"][
                "answer_completion_extended_by"
            ],
            "answer_sentence_10",
        )

    def test_speaker_rescue_truncates_still_suspended_answer_to_integral_period(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                answer_text="A concise complete answer. Then it trails",
                answer_sentence_ids=["answer_sentence_1"],
                answer_boundary_status="truncated",
                question_quality=0.75,
                answer_quality=0.72,
            )
            session.sentences = [
                Sentence(
                    sentence_id="answer_sentence_1",
                    audio_source_id="audio_1",
                    text="A concise complete answer. Then it trails",
                    start_seconds=2.0,
                    end_seconds=3.0,
                ),
                Sentence(
                    sentence_id="answer_sentence_2",
                    audio_source_id="audio_1",
                    text="with",
                    start_seconds=3.0,
                    end_seconds=4.0,
                ),
            ]
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertEqual(candidate.answer_text, "A concise complete answer.")
        self.assertTrue(
            candidate.metadata["speaker_rescue_trim"][
                "answer_completion_truncated_to_integral_boundary"
            ],
        )

    def test_speaker_rescue_question_focus_does_not_end_inside_phrase(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                question_text="Setup words che cosa vuol dire studiare a un",
                normalized_question_text=(
                    "setup words che cosa vuol dire studiare a un"
                ),
                question_quality=0.75,
                answer_quality=0.72,
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertEqual(candidate.question_text, "Che cosa vuol dire studiare")

    def test_speaker_rescue_question_focus_falls_back_to_full_sentence(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                question_text="Setup how to",
                normalized_question_text="setup how to",
                question_quality=0.75,
                answer_quality=0.72,
            )
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": 1}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[(candidate, "surface_answer_cue_risk")],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertEqual(candidate.question_text, "Setup how to")

    def test_speaker_rescue_respects_check_and_rescue_caps(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, first_candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                max_checks=2,
                max_rescues=1,
            )
            extra_candidates = [
                _candidate_for_rescue(
                    qa_candidate_id=f"qa_000{index}",
                    question_text=f"What is topic {index}?",
                    answer_text=f"Topic {index} has a grounded answer.",
                )
                for index in (2, 3)
            ]
            candidates = [first_candidate, *extra_candidates]
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": len(candidates)}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[
                    (candidate, "surface_answer_cue_risk")
                    for candidate in candidates
                ],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["attempted_candidate_count"], 1)
        self.assertEqual(stats["rescued_candidate_count"], 1)
        self.assertTrue(stats["candidate_cap_reached"])
        self.assertFalse(stats["check_cap_reached"])
        self.assertEqual(len(emitted), 1)
        self.assertEqual(suppressed_counts, {"surface_answer_cue_risk": 2})

    def test_speaker_rescue_respects_check_cap(self) -> None:
        with TemporaryDirectory() as temp_root:
            extractor, session, first_candidate = _speaker_rescue_fixture(
                Path(temp_root),
                similarity=0.1,
                max_checks=2,
                max_rescues=10,
            )
            extra_candidates = [
                _candidate_for_rescue(
                    qa_candidate_id=f"qa_001{index}",
                    question_text=f"How does item {index} work?",
                    answer_text=f"Item {index} works through a grounded response.",
                )
                for index in (2, 3)
            ]
            candidates = [first_candidate, *extra_candidates]
            emitted: list[QAPairCandidate] = []
            suppressed_counts = {"surface_answer_cue_risk": len(candidates)}

            stats = extractor._apply_speaker_assisted_rescue(
                session=session,
                suppressed_candidates=[
                    (candidate, "surface_answer_cue_risk")
                    for candidate in candidates
                ],
                emitted_candidates=emitted,
                suppressed_by_gate_reason_counts=suppressed_counts,
            )

        self.assertEqual(stats["attempted_candidate_count"], 2)
        self.assertEqual(stats["rescued_candidate_count"], 2)
        self.assertTrue(stats["check_cap_reached"])
        self.assertEqual(len(emitted), 2)
        self.assertEqual(suppressed_counts, {"surface_answer_cue_risk": 1})


def _session(
    temp_root: Path,
    *,
    question_text: str = "What is the point?",
    answer_text: str = "The point is local.",
    question_end: float = 2.0,
    reason_codes: list[str] | None = None,
    question_sentence_ids: list[str] | None = None,
    answer_sentence_ids: list[str] | None = None,
    include_question_utterance: bool = False,
) -> LectureSession:
    audio_path = temp_root / "source.wav"
    _write_wav(audio_path)
    candidate = QAPairCandidate(
        qa_candidate_id="qa_0001",
        question_text=question_text,
        answer_text=answer_text,
        question_timing=TimeRange(
            start_seconds=0.0,
            end_seconds=question_end,
            audio_source_id="audio_1",
        ),
        answer_timing=TimeRange(
            start_seconds=2.0,
            end_seconds=4.0,
            audio_source_id="audio_1",
        ),
        confidence=0.8,
        confidence_score=0.8,
        reason_codes=reason_codes or [],
        question_sentence_ids=question_sentence_ids or ["sentence_0001"],
        answer_sentence_ids=answer_sentence_ids or ["sentence_0002"],
        question_source_utterance_ids=(
            ["utterance_0001"] if include_question_utterance else []
        ),
    )
    session = LectureSession(
        session_id="test_session",
        language_codes=["en"],
        qa_candidates=[candidate],
    )
    session.audio_sources = [
        AudioSource(
            audio_source_id="audio_1",
            input_source_id="input_1",
            audio_path=audio_path,
            audio_format="wav",
            duration_seconds=5.0,
        ),
    ]
    if include_question_utterance:
        session.utterances = [
            Utterance(
                utterance_id="utterance_0001",
                audio_source_id="audio_1",
                text="What is the point?",
                start_seconds=0.0,
                end_seconds=2.0,
                aligned_segment_id="aligned_1",
                aligned_segment_index=0,
            ),
        ]
    session.metadata["media_type"] = MediaType.AUDIO.value
    return session


def _speaker_rescue_fixture(
    temp_root: Path,
    *,
    similarity: float,
    available: bool = True,
    question_text: str = "What is the abstract mechanism?",
    answer_text: str = "The mechanism has a grounded answer.",
    normalized_question_text: str | None = None,
    answer_sentence_ids: list[str] | None = None,
    answer_boundary_status: str = "terminal",
    question_quality: float = 0.8,
    answer_quality: float = 0.8,
    risk_reasons: list[str] | None = None,
    max_checks: int = 40,
    max_rescues: int = 8,
) -> tuple[QAPairExtractor, LectureSession, QAPairCandidate]:
    session = _session(temp_root)
    candidate = _candidate_for_rescue(
        question_text=question_text,
        answer_text=answer_text,
        normalized_question_text=normalized_question_text,
        answer_sentence_ids=answer_sentence_ids,
        answer_boundary_status=answer_boundary_status,
        question_quality=question_quality,
        answer_quality=answer_quality,
        risk_reasons=risk_reasons,
    )
    session.qa_candidates = []
    config = PipelineConfig(
        pipeline_profile="quality_local",
        qa_speaker_check_enabled=True,
        qa_speaker_rescue_max_checks_per_run=max_checks,
        qa_speaker_rescue_max_candidates_per_run=max_rescues,
    )
    service = QASpeakerCheckService(
        config.speaker_check_config(),
        backend=FakeSpeakerBackend(similarity=similarity, available=available),
    )
    extractor = QAPairExtractor(
        config=config,
        qa_speaker_check_service=service,
    )
    return extractor, session, candidate


def _candidate_for_rescue(
    *,
    qa_candidate_id: str = "qa_0001",
    question_text: str = "What is the abstract mechanism?",
    answer_text: str = "The mechanism has a grounded answer.",
    normalized_question_text: str | None = None,
    answer_sentence_ids: list[str] | None = None,
    answer_boundary_status: str = "terminal",
    question_quality: float = 0.8,
    answer_quality: float = 0.8,
    risk_reasons: list[str] | None = None,
) -> QAPairCandidate:
    resolved_answer_sentence_ids = answer_sentence_ids or [f"{qa_candidate_id}_answer"]
    return QAPairCandidate(
        qa_candidate_id=qa_candidate_id,
        question_text=question_text,
        answer_text=answer_text,
        question_timing=TimeRange(
            start_seconds=0.0,
            end_seconds=2.0,
            audio_source_id="audio_1",
        ),
        answer_timing=TimeRange(
            start_seconds=2.0,
            end_seconds=4.0,
            audio_source_id="audio_1",
        ),
        confidence=0.5,
        confidence_score=0.5,
        reason_codes=["answer_in_next_sentence"],
        review_flags=[],
        question_sentence_ids=[f"{qa_candidate_id}_question"],
        answer_sentence_ids=resolved_answer_sentence_ids,
        metadata={
            "quality_features": {
                "risk_reasons": risk_reasons or ["surface_answer_cue_risk"],
                "question_quality_score": question_quality,
                "answer_quality_score": answer_quality,
                "answer_responsiveness_score": 0.38,
            },
            "question_debug": {
                "normalized_question_text": (
                    normalized_question_text or question_text
                ),
            },
            "answer_debug": {
                "search_signals": {
                    "answer_boundary_status": answer_boundary_status,
                },
                "partial_scores": {
                    "answer_cues": 0.0,
                    "keyword_overlap": 0.05,
                    "answer_context": 0.0,
                },
            },
        },
    )


def _write_wav(path: Path) -> None:
    frame_rate = 8000
    frames = b"\0\0" * frame_rate * 5
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(frames)


if __name__ == "__main__":
    unittest.main()
