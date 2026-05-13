"""Tests for sentence-aware deterministic QA extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analysis.qa_extractor import QAPairExtractor
from analysis.semantic_reranking import (
    SemanticRerankScore,
    SemanticRerankingUnavailableError,
)
from analysis.semantic_retrieval import (
    SemanticRetrievalUnavailableError,
    SemanticSearchHit,
)
from core.config import PipelineConfig
from core.models import (
    LectureSession,
    MergedTranscript,
    MergedTranscriptUnit,
    Segment,
    Sentence,
    Utterance,
)
from core.pipeline import LectureProcessingPipeline
from core.types import SpeakerRole


class QAPairExtractorTests(unittest.TestCase):
    """Exercise sentence-aware question detection and local answer pairing."""

    class _FakeSemanticBackend:
        """Deterministic semantic backend used by unit tests."""

        backend_name = "fake_semantic_backend"
        model_name = "intfloat/multilingual-e5-base"

        def __init__(self, scores_by_text: dict[str, float]) -> None:
            self._scores_by_text = scores_by_text

        def retrieve(
            self,
            *,
            query_text: str,
            passage_texts: list[str],
            top_k: int,
            min_similarity: float | None = None,
        ) -> list[SemanticSearchHit]:
            del query_text
            hits: list[SemanticSearchHit] = []
            for passage_index, passage_text in enumerate(passage_texts):
                score = float(self._scores_by_text.get(passage_text, 0.0))
                if min_similarity is not None and score < min_similarity:
                    continue
                hits.append(
                    SemanticSearchHit(
                        passage_index=passage_index,
                        score=score,
                        text=passage_text,
                    ),
                )
            hits.sort(key=lambda hit: hit.score, reverse=True)
            return hits[:top_k]

    class _UnavailableSemanticBackend:
        """Semantic backend stub that forces fallback behavior."""

        backend_name = "fake_unavailable_backend"
        model_name = "intfloat/multilingual-e5-base"

        def retrieve(
            self,
            *,
            query_text: str,
            passage_texts: list[str],
            top_k: int,
            min_similarity: float | None = None,
        ) -> list[SemanticSearchHit]:
            del query_text, passage_texts, top_k, min_similarity
            raise SemanticRetrievalUnavailableError(
                "semantic backend unavailable for test",
            )

    class _FakeSemanticRerankerBackend:
        """Deterministic semantic reranker backend used by unit tests."""

        backend_name = "fake_semantic_reranker"
        model_name = "BAAI/bge-reranker-v2-m3"

        def __init__(self, scores_by_text: dict[str, float]) -> None:
            self._scores_by_text = scores_by_text

        def score_pairs(
            self,
            *,
            query_text: str,
            passage_texts: list[str],
            normalize_scores: bool = True,
        ) -> list[SemanticRerankScore]:
            del query_text, normalize_scores
            return [
                SemanticRerankScore(
                    candidate_index=index,
                    score=float(self._scores_by_text.get(passage_text, 0.0)),
                )
                for index, passage_text in enumerate(passage_texts)
            ]

    class _UnavailableSemanticRerankerBackend:
        """Semantic reranker backend stub that forces fallback behavior."""

        backend_name = "fake_unavailable_reranker"
        model_name = "BAAI/bge-reranker-v2-m3"

        def score_pairs(
            self,
            *,
            query_text: str,
            passage_texts: list[str],
            normalize_scores: bool = True,
        ) -> list[SemanticRerankScore]:
            del query_text, passage_texts, normalize_scores
            raise SemanticRerankingUnavailableError(
                "semantic reranker unavailable for test",
            )

    def test_extracts_explicit_question_with_next_sentence_answer(self) -> None:
        """A sentence question should pair with the immediately following answer."""

        session = self._build_session(
            texts=[
                "What is a graph?",
                "A graph is a set of nodes and edges.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.question_text, "What is a graph?")
        self.assertIn("nodes and edges", candidate.answer_text or "")
        self.assertEqual(candidate.question_sentence_ids, ["sentence_0001"])
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0002"])
        self.assertEqual(
            candidate.question_source_utterance_ids,
            ["utterance_0001"],
        )
        self.assertEqual(
            candidate.answer_source_utterance_ids,
            ["utterance_0002"],
        )
        self.assertEqual(candidate.question_segment_id, "segment_0001")
        self.assertEqual(candidate.answer_segment_id, "segment_0001")
        self.assertEqual(candidate.metadata["input_layer"], "sentences")
        self.assertEqual(
            candidate.metadata["grounding_debug"]["question_timing_source"],
            "utterances",
        )
        self.assertGreaterEqual(candidate.confidence, 0.6)
        self.assertIn("question_mark", candidate.reason_codes)
        self.assertIn("answer_in_next_sentence", candidate.reason_codes)

    def test_detects_question_without_question_mark_from_sentence_cue(self) -> None:
        """Strong lexical cues should still create a sentence-level question."""

        session = self._build_session(
            texts=[
                "Perché questo accade",
                "Accade perché il denominatore cambia segno.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.question_type, "why")
        self.assertIn("denominatore", candidate.answer_text or "")
        self.assertIn("cue_it_perche", candidate.reason_codes)

    def test_pairs_didactic_question_with_same_sentence_answer(self) -> None:
        """A didactic prompt followed by an explanation should pair locally."""

        session = self._build_session(
            texts=[
                (
                    "The question is: why does the limit exist? "
                    "Because the numerator vanishes faster."
                ),
            ],
            segment_text_indexes=[[0]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.question_segment_id, "segment_0001")
        self.assertEqual(candidate.answer_segment_id, "segment_0001")
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0001"])
        self.assertEqual(candidate.question_type, "didactic_prompt")
        self.assertIn("same_sentence_answer", candidate.reason_codes)

    def test_expands_contextual_question_with_previous_sentence(self) -> None:
        """Short contextual questions should absorb nearby sentence context."""

        session = self._build_session(
            texts=[
                (
                    "In America, instead of calling it the imperial system, "
                    "you guys call it the English system."
                ),
                "Is that true?",
                "No, not for me, but maybe other Americans do that.",
            ],
            starts=[0.0, 3.2, 4.8],
            ends=[3.0, 3.5, 7.0],
            segment_text_indexes=[[0, 1, 2]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(session)[0]

        self.assertIn("English system", candidate.question_text)
        self.assertTrue(candidate.question_text.endswith("Is that true?"))
        self.assertEqual(
            candidate.question_sentence_ids,
            ["sentence_0001", "sentence_0002"],
        )
        self.assertIn("question_context_expanded", candidate.reason_codes)
        self.assertEqual(candidate.context_strategy, "previous_sentence_context")
        self.assertEqual(candidate.context_confidence, "high")
        self.assertEqual(candidate.context_sentence_ids, ["sentence_0001"])
        self.assertEqual(
            candidate.context_text,
            (
                "The speaker is asking whether Americans call the imperial "
                "system the English system."
            ),
        )

    def test_supports_intra_sentence_question_answer_split(self) -> None:
        """A question followed by a declarative continuation should split in-place."""

        session = self._build_session(
            texts=[
                "What did you say? One inch is 2.54 centimeters.",
            ],
            segment_text_indexes=[[0]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(session)[0]

        self.assertEqual(candidate.question_text, "What did you say?")
        self.assertEqual(
            candidate.answer_text,
            "One inch is 2.54 centimeters.",
        )
        self.assertIn("intra_sentence_qa", candidate.reason_codes)
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0001"])

    def test_prefers_latest_question_when_multiple_questions_are_close(self) -> None:
        """A competing nearby question should stop the earlier answer search."""

        session = self._build_session(
            texts=[
                "What is the derivative?",
                "How do we compute it?",
                "We compute it with the limit definition.",
            ],
            segment_text_indexes=[[0, 1, 2]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.question_text, "How do we compute it?")
        self.assertIn("limit definition", candidate.answer_text or "")

    def test_discards_answer_that_is_too_far_in_time(self) -> None:
        """A distant answer candidate should be rejected by the temporal window."""

        session = self._build_session(
            texts=[
                "Why is the function continuous?",
                "It is continuous on the interval.",
            ],
            starts=[0.0, 25.0],
            ends=[2.0, 30.0],
            segment_text_indexes=[[0], [1]],
        )

        extractor = self._build_extractor(max_answer_duration_seconds=5.0)
        candidates = extractor.extract(session)

        self.assertEqual(candidates, [])

    def test_supports_cross_segment_pairing_when_sentences_are_consecutive(self) -> None:
        """Imperfect segmentation should not block QA extraction by itself."""

        session = self._build_session(
            texts=[
                "Che cos'è un vettore?",
                "Un vettore è una quantità con modulo e direzione.",
            ],
            segment_text_indexes=[[0], [1]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.question_segment_id, "segment_0001")
        self.assertEqual(candidate.answer_segment_id, "segment_0002")
        self.assertIn("next_segment_support", candidate.reason_codes)

    def test_uses_speaker_turn_support_when_sentence_speakers_are_reliable(self) -> None:
        """Reliable speaker turns should help ranking without becoming mandatory."""

        dialogue_session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            speaker_ids=["speaker_a", "speaker_b"],
            speaker_resolution_statuses=["stable", "stable"],
            speaker_stability_labels=["stable", "stable"],
            speaker_assignment_methods=[
                "direct_stable_majority",
                "direct_stable_majority",
            ],
            sentence_dominance_margins=[0.9, 0.8],
            segment_text_indexes=[[0, 1]],
        )
        monologue_session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            speaker_ids=["speaker_a", "speaker_a"],
            speaker_resolution_statuses=["stable", "stable"],
            speaker_stability_labels=["stable", "stable"],
            speaker_assignment_methods=[
                "direct_stable_majority",
                "direct_stable_majority",
            ],
            sentence_dominance_margins=[0.9, 0.8],
            segment_text_indexes=[[0, 1]],
        )

        dialogue_candidate = self._build_extractor().extract(dialogue_session)[0]
        monologue_candidate = self._build_extractor().extract(monologue_session)[0]

        self.assertIn("speaker_turn_support", dialogue_candidate.reason_codes)
        self.assertGreater(dialogue_candidate.confidence, monologue_candidate.confidence)

    def test_exposes_search_and_ranking_debug_for_answer_selection(self) -> None:
        """Answer search and ranking should remain visible in exported debug."""

        session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidate = self._build_extractor().extract(session)[0]

        self.assertEqual(
            candidate.metadata["pairing_debug"]["search_strategy"],
            "local_rule_based",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["ranking_strategy"],
            "rule_based",
        )
        self.assertEqual(
            candidate.metadata["answer_debug"]["ranking_debug"]["search_strategy"],
            "local_rule_based",
        )
        self.assertIn(
            "distance",
            candidate.metadata["answer_debug"]["partial_scores"],
        )

    def test_semantic_retrieval_finds_answer_beyond_local_window(self) -> None:
        """Semantic answer search should surface distant sentence candidates."""

        session = self._build_session(
            texts=[
                "What is an integral?",
                "Well.",
                "Let us pause.",
                "In general.",
                "An integral is the area under the curve.",
            ],
            segment_text_indexes=[[0, 1, 2, 3, 4]],
            starts=[0.0, 1.0, 2.0, 3.0, 4.0],
            ends=[0.8, 1.4, 2.5, 3.5, 5.5],
        )
        backend = self._FakeSemanticBackend(
            {
                "An integral is the area under the curve.": 0.93,
                "Well.": 0.05,
                "Let us pause.": 0.09,
                "In general.": 0.08,
            },
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="semantic_retrieval",
            qa_semantic_retrieval_enabled=True,
            qa_semantic_retrieval_window_units=6,
            qa_semantic_retrieval_top_k=1,
            qa_semantic_retrieval_min_similarity=0.2,
            answer_search_window_units=1,
            semantic_retriever_backend=backend,
        ).extract(session)[0]

        self.assertEqual(
            candidate.metadata["pairing_debug"]["search_strategy"],
            "semantic_retrieval",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["effective_search_strategy"],
            "semantic_retrieval",
        )
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0005"])
        self.assertEqual(
            candidate.metadata["answer_debug"]["search_signals"]["candidate_channel"],
            "semantic_retrieval",
        )
        self.assertAlmostEqual(
            candidate.metadata["answer_debug"]["search_signals"]["semantic_similarity"],
            0.93,
            places=2,
        )
        self.assertIn("semantic_retrieval_candidate", candidate.reason_codes)

    def test_deferred_search_finds_delayed_answer_after_irrelevant_local_reply(self) -> None:
        """A weak local answer should not block a stronger deferred answer."""

        session = self._build_session(
            texts=[
                "Am I also going to learn a mnemonic during this, but how many feet are in a mile?",
                "Because I can feel one coming.",
                "Let us move on for a moment.",
                "Another aside entirely.",
                "Five tomatoes, five two eighty.",
                "That's how many feet are in a mile.",
            ],
            starts=[0.0, 1.0, 2.0, 3.0, 60.0, 63.0],
            ends=[0.8, 1.8, 2.8, 3.8, 62.0, 64.0],
            segment_text_indexes=[[0, 1, 2, 3], [4, 5]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
            qa_answer_ranking_strategy="rule_based",
            answer_search_window_units=2,
            deferred_answer_search_window_units=8,
        ).extract(session)[0]

        self.assertEqual(candidate.question_text, "How many feet are in a mile?")
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0006"])
        self.assertIn("deferred_answer_search", candidate.reason_codes)
        self.assertTrue(
            candidate.metadata["pairing_debug"]["deferred_answer_search_used"],
        )
        self.assertNotIn("Because I can feel one coming", candidate.answer_text or "")
        self.assertEqual(candidate.context_strategy, "deferred_answer_context")
        self.assertIn("five tomatoes", candidate.context_text or "")
        self.assertIn("sentence_0005", candidate.context_sentence_ids)

    def test_merges_contextual_followup_question_and_finds_deferred_numeric_answer(self) -> None:
        """A question with a follow-up tag should stay a question and find later answers."""

        session = self._build_session(
            texts=[
                (
                    "Where is there a point where an integer number of centimeters "
                    "lines up exactly with an integer number of inches? "
                    "It must happen eventually, right?"
                ),
                "What did you say? One inch is 2.54 centimeters.",
                "Yeah, but it actually happens sooner than you would think.",
                "Yeah, it happens at 50 inches.",
                "Does it?",
                "50 inches is exactly 127 centimeters.",
            ],
            starts=[0.0, 2.0, 4.0, 6.0, 7.5, 8.5],
            ends=[1.5, 3.5, 5.5, 6.8, 8.0, 9.5],
            segment_text_indexes=[[0, 1, 2], [3, 4, 5]],
            semantic_quality_labels=[
                "fragment",
                "fragment",
                "good",
                "borderline",
                "borderline",
                "good",
            ],
            merge_safety_labels=["risky", "risky", "safe", "safe", "safe", "safe"],
            review_priorities=["high", "high", "low", "low", "low", "low"],
            sentence_review_flags=[
                ["merge_risky"],
                ["merge_risky"],
                [],
                [],
                [],
                [],
            ],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
            qa_answer_ranking_strategy="rule_based",
            answer_search_window_units=1,
            deferred_answer_search_window_units=8,
        ).extract(session)[0]

        self.assertIn("It must happen eventually, right?", candidate.question_text)
        self.assertIn(
            candidate.answer_sentence_ids[0],
            {"sentence_0004", "sentence_0006"},
        )
        self.assertGreaterEqual(candidate.didactic_question_score or 0.0, 0.7)

    def test_builds_local_topic_context_for_unit_alignment_question(self) -> None:
        """Nearby topical setup should become reviewer-facing QA context."""

        session = self._build_session(
            texts=[
                "On this ruler the inches and centimeters are not lining up yet.",
                "I want to find the tick marks where they line up exactly.",
                (
                    "Where is there a point where an integer number of centimeters "
                    "lines up exactly with an integer number of inches?"
                ),
                "Yeah, it happens at 50 inches.",
                "50 inches is exactly 127 centimeters.",
            ],
            segment_text_indexes=[[0, 1, 2], [3, 4]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
            qa_answer_ranking_strategy="rule_based",
            answer_search_window_units=1,
            deferred_answer_search_window_units=6,
        ).extract(session)[0]

        self.assertEqual(candidate.context_strategy, "local_topic_window")
        self.assertEqual(candidate.context_confidence, "medium")
        self.assertEqual(candidate.context_sentence_ids, ["sentence_0001", "sentence_0002"])
        self.assertEqual(
            candidate.context_text,
            (
                "The speakers are comparing inches and centimeters on a ruler "
                "and looking for a point where both unit systems align exactly."
            ),
        )

    def test_builds_context_for_where_is_that_from_followup(self) -> None:
        """Compressed follow-up questions should still carry nearby phrase context."""

        session = self._build_session(
            texts=[
                "Give them an inch and they'll take an L.",
                "Where's that from?",
                "It's from a joke about taking a mile.",
            ],
            segment_text_indexes=[[0, 1, 2]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(session)[0]

        self.assertEqual(candidate.question_text, "Where's that from?")
        self.assertEqual(candidate.context_strategy, "local_topic_window")
        self.assertEqual(candidate.context_sentence_ids, ["sentence_0001"])
        self.assertIn("take an L", candidate.context_text or "")

    def test_uses_leading_answer_before_competing_followup_question(self) -> None:
        """A sentence can answer one question before asking a new follow-up."""

        session = self._build_session(
            texts=[
                (
                    "In America, instead of calling it the imperial system, "
                    "you guys call it the English system."
                ),
                "Is that true?",
                "No, not for me, but maybe other Americans do that. What do people call it?",
            ],
            starts=[0.0, 3.2, 4.8],
            ends=[3.0, 3.5, 7.0],
            segment_text_indexes=[[0, 1, 2]],
        )

        candidates = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(session)

        contextual_candidate = next(
            candidate
            for candidate in candidates
            if candidate.question_text.endswith("Is that true?")
        )

        self.assertEqual(
            contextual_candidate.answer_text,
            "No, not for me, but maybe other Americans do that.",
        )
        self.assertEqual(
            contextual_candidate.answer_sentence_ids,
            ["sentence_0003"],
        )
        self.assertIn(
            "answer_before_competing_question",
            contextual_candidate.reason_codes,
        )

    def test_filters_false_positive_declarative_what_clause(self) -> None:
        """Declarative 'that's what ...' clauses must not be treated as questions."""

        session = self._build_session(
            texts=[
                "That's what a black hole says to light, some light.",
                "Now that means something else.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidates = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(session)

        self.assertEqual(candidates, [])

    def test_semantic_retrieval_falls_back_to_local_when_backend_unavailable(self) -> None:
        """Semantic search should fall back cleanly to the local baseline."""

        session = self._build_session(
            texts=[
                "What is a graph?",
                "A graph is a set of nodes and edges.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="semantic_retrieval",
            qa_semantic_retrieval_enabled=True,
            semantic_retriever_backend=self._UnavailableSemanticBackend(),
        ).extract(session)[0]

        self.assertEqual(
            candidate.metadata["pairing_debug"]["requested_search_strategy"],
            "semantic_retrieval",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["search_strategy"],
            "local_rule_based",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["search_fallback_reason"],
            "semantic_backend_unavailable",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["semantic_backend_status"],
            "fallback",
        )
        self.assertIn(
            "semantic backend unavailable",
            candidate.metadata["pairing_debug"]["search_backend_error"],
        )
        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0002"])

    def test_semantic_reranker_can_override_local_rule_based_ordering(self) -> None:
        """Semantic reranking should be able to reorder local candidates."""

        session = self._build_session(
            texts=[
                "What is an integral?",
                "The answer is that we use it in calculus.",
                "An integral is the area under the curve.",
            ],
            segment_text_indexes=[[0, 1, 2]],
        )
        reranker_backend = self._FakeSemanticRerankerBackend(
            {
                "The answer is that we use it in calculus.": 0.18,
                "The answer is that we use it in calculus. "
                "An integral is the area under the curve.": 0.97,
            },
        )

        candidate = self._build_extractor(
            qa_answer_ranking_strategy="semantic_reranker",
            qa_semantic_reranking_enabled=True,
            qa_semantic_reranking_weight=0.7,
            max_answer_units=2,
            answer_search_window_units=2,
            semantic_reranker_backend=reranker_backend,
        ).extract(session)[0]

        self.assertEqual(candidate.answer_sentence_ids, ["sentence_0002", "sentence_0003"])
        self.assertEqual(
            candidate.metadata["pairing_debug"]["ranking_strategy"],
            "semantic_reranker",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["effective_ranking_strategy"],
            "semantic_reranker",
        )
        self.assertAlmostEqual(
            candidate.metadata["answer_debug"]["ranking_debug"]["semantic_relevance_score"],
            0.97,
            places=2,
        )
        self.assertEqual(
            candidate.metadata["answer_debug"]["ranking_debug"]["ranking_strategy"],
            "semantic_reranker",
        )
        self.assertIn("semantic_reranking_applied", candidate.reason_codes)

    def test_semantic_reranker_falls_back_to_rule_based_when_backend_unavailable(self) -> None:
        """Semantic reranking should fall back cleanly to the rule-based ranker."""

        session = self._build_session(
            texts=[
                "What is a graph?",
                "A graph is a set of nodes and edges.",
            ],
            segment_text_indexes=[[0, 1]],
        )

        candidate = self._build_extractor(
            qa_answer_ranking_strategy="semantic_reranker",
            qa_semantic_reranking_enabled=True,
            semantic_reranker_backend=self._UnavailableSemanticRerankerBackend(),
        ).extract(session)[0]

        self.assertEqual(
            candidate.metadata["pairing_debug"]["requested_ranking_strategy"],
            "semantic_reranker",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["ranking_strategy"],
            "rule_based",
        )
        self.assertEqual(
            candidate.metadata["pairing_debug"]["ranking_fallback_reason"],
            "semantic_backend_unavailable",
        )
        self.assertEqual(
            candidate.metadata["answer_debug"]["ranking_debug"]["semantic_reranking_status"],
            "fallback",
        )

    def test_keeps_uncertain_speaker_cases_extractable(self) -> None:
        """Uncertain speaker attribution should not block a plausible QA pair."""

        session = self._build_session(
            texts=[
                "Che differenza c'è tra speed e velocity?",
                "The difference is that velocity includes direction.",
            ],
            speaker_ids=["speaker_a", "speaker_b"],
            speaker_resolution_statuses=["uncertain", "uncertain"],
            speaker_stability_labels=["uncertain", "uncertain"],
            speaker_assignment_methods=[
                "direct_uncertain_majority",
                "direct_uncertain_majority",
            ],
            semantic_quality_labels=["good", "good"],
            merge_safety_labels=["safe", "safe"],
            segment_text_indexes=[[0, 1]],
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].question_type, "difference")
        self.assertNotIn("speaker_conflict_penalty", candidates[0].reason_codes)

    def test_softens_same_speaker_penalty_when_speaker_is_uncertain_or_fragile(self) -> None:
        """Same-speaker penalties should be softer when speaker evidence is weak."""

        stable_session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            speaker_ids=["speaker_a", "speaker_a"],
            speaker_resolution_statuses=["stable", "stable"],
            speaker_stability_labels=["stable", "stable"],
            speaker_confidence_labels=["high", "high"],
            speaker_assignment_methods=[
                "direct_stable_majority",
                "direct_stable_majority",
            ],
            segment_text_indexes=[[0, 1]],
        )
        weak_session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            speaker_ids=["speaker_a", "speaker_a"],
            speaker_resolution_statuses=["mostly_stable", "mostly_stable"],
            speaker_stability_labels=["uncertain", "uncertain"],
            speaker_confidence_labels=["medium", "medium"],
            speaker_assignment_methods=[
                "direct_uncertain_majority",
                "direct_uncertain_majority",
            ],
            semantic_quality_labels=["fragment", "fragment"],
            merge_safety_labels=["risky", "risky"],
            sentence_review_flags=[["multi_utterance"], ["multi_utterance"]],
            segment_text_indexes=[[0, 1]],
        )

        stable_candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(stable_session)[0]
        weak_candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
        ).extract(weak_session)[0]

        self.assertIn("same_speaker_pairing_penalty", stable_candidate.reason_codes)
        self.assertIn("same_speaker_penalty_softened", weak_candidate.reason_codes)
        self.assertLess(
            weak_candidate.metadata["answer_debug"]["partial_scores"]["speaker_pairing"],
            0.0,
        )
        self.assertGreater(
            weak_candidate.metadata["answer_debug"]["partial_scores"]["speaker_pairing"],
            stable_candidate.metadata["answer_debug"]["partial_scores"][
                "speaker_pairing"
            ],
        )

    def test_marks_answer_that_is_itself_a_question(self) -> None:
        """Question-like answers should be flagged for review and confidence reduction."""

        session = self._build_session(
            texts=[
                "But what are you going to do then? How are you going to get it back?",
            ],
            segment_text_indexes=[[0]],
        )

        candidate = self._build_extractor(
            qa_answer_search_strategy="local_rule_based",
            qa_answer_ranking_strategy="rule_based",
            min_qa_confidence=0.0,
        ).extract(session)[0]

        self.assertTrue(candidate.answer_is_question)
        self.assertIn("answer_is_question", candidate.reason_codes)
        self.assertIn("answer_is_question", candidate.review_flags)
        self.assertLess(candidate.confidence, 0.7)

    def test_falls_back_to_merged_transcript_when_sentences_are_missing(self) -> None:
        """Merged transcript should remain an explicit fallback path."""

        session = self._build_session(
            texts=[
                "What is a graph?",
                "A graph is a set of nodes and edges.",
            ],
            segment_text_indexes=[[0, 1]],
            include_sentences=False,
        )

        candidates = self._build_extractor().extract(session)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.metadata["input_layer"], "merged_transcript_fallback")
        self.assertEqual(candidate.question_unit_ids, ["unit_0001"])
        self.assertEqual(candidate.answer_unit_ids, ["unit_0002"])
        self.assertEqual(candidate.question_sentence_ids, [])
        self.assertIn("merged_transcript_fallback", candidate.reason_codes)

    def test_pipeline_step_populates_session_candidates(self) -> None:
        """The pipeline QA step should store candidates back into the session."""

        session = self._build_session(
            texts=[
                "What is a matrix?",
                "A matrix is a rectangular array of numbers.",
            ],
            segment_text_indexes=[[0, 1]],
        )
        config = self._build_config()
        pipeline = LectureProcessingPipeline(config)

        pipeline.extract_qa_candidates(session)

        self.assertEqual(len(session.qa_candidates), 1)
        self.assertEqual(session.metadata["qa_candidate_count"], 1)
        self.assertTrue(session.metadata["qa_extraction_enabled"])

    def _build_extractor(
        self,
        *,
        semantic_retriever_backend: object | None = None,
        semantic_reranker_backend: object | None = None,
        **config_overrides: object,
    ) -> QAPairExtractor:
        """Create an extractor with deterministic QA-oriented defaults."""

        return QAPairExtractor(
            self._build_config(**config_overrides),
            semantic_retriever_backend=semantic_retriever_backend,
            semantic_reranker_backend=semantic_reranker_backend,
        )

    @staticmethod
    def _build_config(**config_overrides: object) -> PipelineConfig:
        """Create a test configuration for extractor and pipeline checks."""

        with tempfile.TemporaryDirectory() as temp_directory:
            return PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                **config_overrides,
            )

    @staticmethod
    def _build_session(
        texts: list[str],
        segment_text_indexes: list[list[int]],
        starts: list[float] | None = None,
        ends: list[float] | None = None,
        speaker_ids: list[str | None] | None = None,
        speaker_resolution_statuses: list[str | None] | None = None,
        speaker_confidence_labels: list[str | None] | None = None,
        speaker_stability_labels: list[str | None] | None = None,
        speaker_assignment_methods: list[str | None] | None = None,
        semantic_quality_labels: list[str | None] | None = None,
        merge_safety_labels: list[str | None] | None = None,
        review_priorities: list[str | None] | None = None,
        sentence_review_flags: list[list[str]] | None = None,
        sentence_dominance_margins: list[float | None] | None = None,
        include_sentences: bool = True,
    ) -> LectureSession:
        """Create a lightweight lecture session for extractor tests."""

        starts = starts or [float(index * 4) for index in range(len(texts))]
        ends = ends or [start + 4.0 for start in starts]
        speaker_ids = speaker_ids or [None] * len(texts)
        speaker_resolution_statuses = speaker_resolution_statuses or [None] * len(texts)
        speaker_confidence_labels = speaker_confidence_labels or [None] * len(texts)
        speaker_stability_labels = speaker_stability_labels or [None] * len(texts)
        speaker_assignment_methods = speaker_assignment_methods or [None] * len(texts)
        semantic_quality_labels = semantic_quality_labels or ["good"] * len(texts)
        merge_safety_labels = merge_safety_labels or ["safe"] * len(texts)
        review_priorities = review_priorities or ["low"] * len(texts)
        sentence_review_flags = sentence_review_flags or [[] for _ in texts]
        sentence_dominance_margins = sentence_dominance_margins or [0.7] * len(texts)

        units = [
            QAPairExtractorTests._build_unit(
                index=index + 1,
                text=text,
                start_seconds=starts[index],
                end_seconds=ends[index],
            )
            for index, text in enumerate(texts)
        ]
        utterances = [
            QAPairExtractorTests._build_utterance(
                index=index + 1,
                text=text,
                start_seconds=starts[index],
                end_seconds=ends[index],
                speaker_id=speaker_ids[index],
            )
            for index, text in enumerate(texts)
        ]
        sentences = []
        if include_sentences:
            sentences = [
                QAPairExtractorTests._build_sentence(
                    index=index + 1,
                    text=text,
                    start_seconds=starts[index],
                    end_seconds=ends[index],
                    source_utterance_ids=[utterances[index].utterance_id],
                    speaker_id=speaker_ids[index],
                    speaker_resolution_status=speaker_resolution_statuses[index],
                    speaker_confidence_label=speaker_confidence_labels[index],
                    speaker_stability_label=speaker_stability_labels[index],
                    speaker_assignment_method=speaker_assignment_methods[index],
                    semantic_quality_label=semantic_quality_labels[index],
                    merge_safety_label=merge_safety_labels[index],
                    review_priority=review_priorities[index],
                    sentence_review_flags=sentence_review_flags[index],
                    dominance_margin=sentence_dominance_margins[index],
                )
                for index, text in enumerate(texts)
            ]

        merged_transcript = MergedTranscript(
            session_id="session_001",
            units=units,
            full_text=" ".join(texts),
        )
        segments = [
            QAPairExtractorTests._build_segment(
                index=index + 1,
                text_indexes=text_indexes,
                texts=texts,
                units=units,
                sentences=sentences,
                utterances=utterances,
            )
            for index, text_indexes in enumerate(segment_text_indexes)
        ]
        return LectureSession(
            session_id="session_001",
            merged_transcript=merged_transcript,
            transcript_text=merged_transcript.full_text,
            utterances=utterances,
            sentences=sentences,
            segments=segments,
        )

    @staticmethod
    def _build_unit(
        index: int,
        text: str,
        start_seconds: float,
        end_seconds: float,
    ) -> MergedTranscriptUnit:
        """Build one deterministic merged transcript unit."""

        return MergedTranscriptUnit(
            unit_id=f"unit_{index:04d}",
            chunk_id=f"chunk_{index:04d}",
            chunk_occurrence=index,
            audio_source_id="audio_source_001",
            source_order_index=1,
            input_source_id="input_source_001",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            session_start_seconds=start_seconds,
            session_end_seconds=end_seconds,
            text=text,
            detected_language="it",
        )

    @staticmethod
    def _build_utterance(
        index: int,
        text: str,
        start_seconds: float,
        end_seconds: float,
        speaker_id: str | None,
    ) -> Utterance:
        """Build one deterministic utterance."""

        return Utterance(
            utterance_id=f"utterance_{index:04d}",
            audio_source_id="audio_source_001",
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            aligned_segment_id=f"aligned_segment_{index:04d}",
            aligned_segment_index=index - 1,
            session_start_seconds=start_seconds,
            session_end_seconds=end_seconds,
            speaker_id=speaker_id,
        )

    @staticmethod
    def _build_sentence(
        index: int,
        text: str,
        start_seconds: float,
        end_seconds: float,
        source_utterance_ids: list[str],
        speaker_id: str | None,
        speaker_resolution_status: str | None,
        speaker_confidence_label: str | None,
        speaker_stability_label: str | None,
        speaker_assignment_method: str | None,
        semantic_quality_label: str | None,
        merge_safety_label: str | None,
        review_priority: str | None,
        sentence_review_flags: list[str],
        dominance_margin: float | None,
    ) -> Sentence:
        """Build one deterministic sentence."""

        metadata = {}
        if dominance_margin is not None:
            metadata["dominance_margin"] = dominance_margin

        return Sentence(
            sentence_id=f"sentence_{index:04d}",
            audio_source_id="audio_source_001",
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            source_utterance_ids=source_utterance_ids,
            detected_language="it",
            speaker_id=speaker_id,
            speaker_resolution_status=speaker_resolution_status,
            speaker_confidence_label=speaker_confidence_label,
            speaker_stability_label=speaker_stability_label,
            speaker_assignment_method=speaker_assignment_method,
            speaker_evidence_summary="synthetic_test_evidence",
            merge_safety_label=merge_safety_label,
            semantic_quality_label=semantic_quality_label,
            review_priority=review_priority,
            sentence_review_flags=list(sentence_review_flags),
            session_start_seconds=start_seconds,
            session_end_seconds=end_seconds,
            metadata=metadata,
        )

    @staticmethod
    def _build_segment(
        index: int,
        text_indexes: list[int],
        texts: list[str],
        units: list[MergedTranscriptUnit],
        sentences: list[Sentence],
        utterances: list[Utterance],
    ) -> Segment:
        """Build one deterministic segment covering selected items."""

        return Segment(
            segment_id=f"segment_{index:04d}",
            start_seconds=units[text_indexes[0]].session_start_seconds
            or units[text_indexes[0]].start_seconds,
            end_seconds=units[text_indexes[-1]].session_end_seconds
            or units[text_indexes[-1]].end_seconds,
            text=" ".join(texts[item_index] for item_index in text_indexes),
            transcript_chunk_ids=[units[item_index].chunk_id for item_index in text_indexes],
            merged_transcript_unit_ids=[
                units[item_index].unit_id for item_index in text_indexes
            ],
            sentence_ids=[
                sentences[item_index].sentence_id
                for item_index in text_indexes
                if item_index < len(sentences)
            ],
            source_utterance_ids=[
                utterances[item_index].utterance_id for item_index in text_indexes
            ],
            audio_source_ids=["audio_source_001"],
            observed_languages=["it", "en"],
            estimated_speaker_roles=[SpeakerRole.TEACHER],
        )


if __name__ == "__main__":
    unittest.main()
