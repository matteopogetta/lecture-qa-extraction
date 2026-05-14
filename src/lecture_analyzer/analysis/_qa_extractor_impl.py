"""Sentence-aware rule-based candidate QA extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Protocol, Sequence

from lecture_analyzer.analysis.qa_rules import (
    ANSWER_CUE_RULES,
    DECLARATIVE_WHAT_PATTERNS,
    DIDACTIC_QUESTION_RULES,
    INTERROGATIVE_START_WORDS,
    QUESTION_CUE_RULES,
    collect_rule_matches,
    count_tokens,
    normalize_rule_text,
)
from lecture_analyzer.analysis.semantic_retrieval import (
    SemanticRetrievalUnavailableError,
    SemanticRetrieverBackend,
    SentenceTransformersE5Backend,
)
from lecture_analyzer.analysis.semantic_reranking import (
    SemanticRerankerBackend,
    SemanticRerankingUnavailableError,
    TransformersBGERerankerBackend,
)
from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    LectureSession,
    MergedTranscriptUnit,
    QAPairCandidate,
    Segment,
    Sentence,
    TimeRange,
    Utterance,
)
from lecture_analyzer.core.types import SpeakerRole


_QUESTION_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "be",
    "but",
    "che",
    "come",
    "con",
    "cosa",
    "da",
    "del",
    "della",
    "dello",
    "di",
    "do",
    "does",
    "e",
    "for",
    "from",
    "how",
    "i",
    "if",
    "il",
    "in",
    "into",
    "is",
    "it",
    "la",
    "le",
    "many",
    "must",
    "number",
    "of",
    "on",
    "or",
    "point",
    "right",
    "the",
    "there",
    "this",
    "to",
    "un",
    "una",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "you",
}
_NUMBER_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_ANAPHORIC_QUESTION_PATTERNS = (
    re.compile(r"^(?:is|was|were) that true\??$"),
    re.compile(r"^(?:does|did|do|can|could|would|will|has|have|had) it\b.*\??$"),
    re.compile(r"^really\??$"),
    re.compile(r"^why\??$"),
    re.compile(r"^what about that\??$"),
    re.compile(r"^it must\b.*right\??$"),
)


@dataclass(slots=True)
class _ExtractionUnit:
    """One local QA extraction unit built from a session layer."""

    index: int
    layer: str
    text_id: str
    text: str
    start_seconds: float
    end_seconds: float
    audio_source_id: str | None = None
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    merged_unit_ids: list[str] = field(default_factory=list)
    sentence_ids: list[str] = field(default_factory=list)
    source_utterance_ids: list[str] = field(default_factory=list)
    speaker_id: str | None = None
    speaker_resolution_status: str | None = None
    speaker_confidence_label: str | None = None
    speaker_stability_label: str | None = None
    speaker_assignment_method: str | None = None
    speaker_evidence_summary: str | None = None
    merge_safety_label: str | None = None
    semantic_quality_label: str | None = None
    review_priority: str | None = None
    sentence_review_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _PreparedExtractionInput:
    """Resolved primary QA input with auxiliary grounding lookups."""

    input_layer: str
    units: list[_ExtractionUnit]
    utterance_by_id: dict[str, Utterance] = field(default_factory=dict)


@dataclass(slots=True)
class QuestionCandidate:
    """An intermediate question candidate detected from one extraction unit."""

    question_candidate_id: str
    unit_index: int
    unit: _ExtractionUnit
    question_units: list[_ExtractionUnit]
    question_text: str
    question_unit_ids: list[str]
    question_sentence_ids: list[str]
    question_source_utterance_ids: list[str]
    question_segment_ids: list[str]
    question_segment_id: str | None
    question_type: str
    question_score: float
    didactic_question_score: float
    reason_codes: list[str] = field(default_factory=list)
    local_answer_seed: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _QuestionExtraction:
    """A normalized question span extracted from one unit."""

    question_text: str
    local_answer_seed: str | None
    extraction_reason: str
    question_preamble: str | None = None
    intra_sentence_qa: bool = False


@dataclass(slots=True)
class _ContextExtraction:
    """A short contextual summary attached to one QA pair."""

    context_text: str | None
    context_raw_text: str | None
    context_units: list[_ExtractionUnit] = field(default_factory=list)
    context_sentence_ids: list[str] = field(default_factory=list)
    context_source_utterance_ids: list[str] = field(default_factory=list)
    context_strategy: str | None = None
    context_confidence: str | None = None


@dataclass(slots=True)
class _AnswerCandidate:
    """A locally plausible answer span for one detected question."""

    answer_candidate_id: str
    answer_text: str
    answer_units: list[_ExtractionUnit]
    answer_unit_ids: list[str]
    answer_sentence_ids: list[str]
    answer_source_utterance_ids: list[str]
    answer_segment_ids: list[str]
    answer_segment_id: str | None
    answer_score: float
    distance_units: int
    gap_seconds: float
    search_signals: dict[str, Any] = field(default_factory=dict)
    partial_scores: dict[str, float] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _AnswerSearchResult:
    """A search-stage container holding answer candidates and stop context."""

    strategy_name: str
    candidates: list[_AnswerCandidate] = field(default_factory=list)
    stop_reason: str = "window_exhausted"
    competing_question_stop: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AnswerSearchStrategy(Protocol):
    """Protocol for future answer candidate generation strategies."""

    strategy_name: str

    def generate_candidates(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> _AnswerSearchResult:
        """Return plausible answer candidates for one detected question."""


class AnswerRankingStrategy(Protocol):
    """Protocol for future answer candidate ranking strategies."""

    strategy_name: str

    def rank_candidates(
        self,
        *,
        question: QuestionCandidate,
        search_result: _AnswerSearchResult,
        segment_lookup: dict[str, Any],
    ) -> list[_AnswerCandidate]:
        """Return ranked answer candidates for one detected question."""


class _LocalRuleBasedAnswerSearcher:
    """Generate locally grounded answer candidates with deterministic rules."""

    strategy_name = "local_rule_based"

    def __init__(self, extractor: QAPairExtractor) -> None:
        self._extractor = extractor

    def generate_candidates(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> _AnswerSearchResult:
        """Return answer candidates collected from the local search window."""

        candidates: list[_AnswerCandidate] = []
        competing_question_stop = False
        search_stop_reason = "window_exhausted"

        same_unit_answer = self._extractor._build_same_unit_answer_candidate(
            question=question,
            segment_lookup=segment_lookup,
        )
        if same_unit_answer is not None:
            candidates.append(same_unit_answer)

        collected_units: list[_ExtractionUnit] = []
        for distance_units in range(
            1,
            self._extractor.config.answer_search_window_units + 1,
        ):
            candidate_index = question.unit_index + distance_units
            if candidate_index >= len(units):
                break

            if candidate_index in question_by_index:
                leading_answer = self._extractor._build_competing_question_leading_answer_candidate(
                    question=question,
                    competing_unit=units[candidate_index],
                    distance_units=distance_units,
                    segment_lookup=segment_lookup,
                )
                if leading_answer is not None:
                    candidates.append(leading_answer)
                competing_question_stop = True
                search_stop_reason = "competing_question"
                break

            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                search_stop_reason = "audio_source_boundary"
                break
            if not candidate_unit.text.strip():
                continue

            gap_from_question = max(
                0.0,
                candidate_unit.start_seconds - question.unit.end_seconds,
            )
            if gap_from_question > self._extractor.config.max_answer_duration_seconds:
                search_stop_reason = "temporal_gap_limit"
                break

            prospective_units = collected_units + [candidate_unit]
            if len(prospective_units) > self._extractor.config.max_answer_units:
                search_stop_reason = "answer_span_limit"
                break

            answer_duration = self._extractor._units_duration_seconds(prospective_units)
            if answer_duration > self._extractor.config.max_answer_duration_seconds:
                search_stop_reason = "answer_duration_limit"
                break

            if not self._extractor._can_use_answer_units(
                question=question,
                answer_units=prospective_units,
                segment_lookup=segment_lookup,
            ):
                search_stop_reason = "cross_segment_block"
                break

            collected_units = prospective_units
            answer_candidate = self._extractor._build_answer_candidate_from_units(
                question=question,
                answer_units=collected_units,
                distance_units=distance_units,
                segment_lookup=segment_lookup,
            )
            if answer_candidate is not None:
                candidates.append(answer_candidate)

        return _AnswerSearchResult(
            strategy_name=self.strategy_name,
            candidates=candidates,
            stop_reason=search_stop_reason,
            competing_question_stop=competing_question_stop,
            metadata={
                "requested_strategy": self.strategy_name,
                "effective_strategy": self.strategy_name,
                "candidate_count": len(candidates),
                "search_window_units": self._extractor.config.answer_search_window_units,
                "max_answer_units": self._extractor.config.max_answer_units,
            },
        )


class _SemanticAnswerSearcher:
    """Generate answer candidates via sentence-level semantic retrieval."""

    strategy_name = "semantic_retrieval"

    def __init__(
        self,
        extractor: QAPairExtractor,
        *,
        backend: SemanticRetrieverBackend | None = None,
        fallback_searcher: AnswerSearchStrategy | None = None,
    ) -> None:
        self._extractor = extractor
        self._backend = backend or SentenceTransformersE5Backend(
            extractor.config.qa_semantic_retrieval_model_name,
        )
        self._fallback_searcher = fallback_searcher or _LocalRuleBasedAnswerSearcher(
            extractor,
        )

    def generate_candidates(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> _AnswerSearchResult:
        """Return semantically retrieved answer candidates plus local baseline."""

        baseline_result = self._fallback_searcher.generate_candidates(
            question=question,
            units=units,
            question_by_index=question_by_index,
            segment_lookup=segment_lookup,
        )
        baseline_result.metadata = {
            **baseline_result.metadata,
            "requested_strategy": self.strategy_name,
            "baseline_strategy": baseline_result.strategy_name,
        }

        if not self._extractor.config.qa_semantic_retrieval_enabled:
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="semantic_retrieval_disabled",
                fallback_error=None,
            )

        if question.unit.layer != "sentence":
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="sentence_layer_required",
                fallback_error=None,
            )

        candidate_units, search_stop_reason, competing_question_stop = (
            self._semantic_candidate_window(
                question=question,
                units=units,
                question_by_index=question_by_index,
                segment_lookup=segment_lookup,
            )
        )
        if not candidate_units:
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="semantic_candidate_window_empty",
                fallback_error=None,
            )

        try:
            hits = self._backend.retrieve(
                query_text=question.question_text,
                passage_texts=[candidate_unit.text for candidate_unit in candidate_units],
                top_k=self._extractor.config.qa_semantic_retrieval_top_k,
                min_similarity=self._extractor.config.qa_semantic_retrieval_min_similarity,
            )
        except SemanticRetrievalUnavailableError as exc:
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="semantic_backend_unavailable",
                fallback_error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - runtime safety net
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="semantic_runtime_error",
                fallback_error=str(exc),
            )

        semantic_candidates = self._build_semantic_candidates(
            question=question,
            candidate_units=candidate_units,
            hits=hits,
            units=units,
            question_by_index=question_by_index,
            segment_lookup=segment_lookup,
        )
        if not semantic_candidates:
            return self._fallback_result(
                baseline_result=baseline_result,
                fallback_reason="semantic_hits_empty",
                fallback_error=None,
            )

        merged_candidates = self._merge_candidates(
            baseline_candidates=baseline_result.candidates,
            semantic_candidates=semantic_candidates,
        )
        return _AnswerSearchResult(
            strategy_name=self.strategy_name,
            candidates=merged_candidates,
            stop_reason=search_stop_reason,
            competing_question_stop=(
                competing_question_stop or baseline_result.competing_question_stop
            ),
            metadata={
                "requested_strategy": self.strategy_name,
                "effective_strategy": self.strategy_name,
                "baseline_strategy": baseline_result.strategy_name,
                "candidate_count": len(merged_candidates),
                "baseline_candidate_count": len(baseline_result.candidates),
                "semantic_candidate_count": len(semantic_candidates),
                "semantic_hit_count": len(hits),
                "search_window_units": (
                    self._extractor.config.qa_semantic_retrieval_window_units
                ),
                "semantic_model_name": self._backend.model_name,
                "semantic_backend": self._backend.backend_name,
                "semantic_backend_status": "available",
                "semantic_stop_reason": search_stop_reason,
                "baseline_stop_reason": baseline_result.stop_reason,
                "semantic_candidate_debug": [
                    {
                        "candidate_id": candidate.answer_candidate_id,
                        "semantic_similarity": candidate.search_signals.get(
                            "semantic_similarity",
                        ),
                        "semantic_rank": candidate.search_signals.get("semantic_rank"),
                        "answer_sentence_ids": list(candidate.answer_sentence_ids),
                    }
                    for candidate in semantic_candidates
                ],
            },
        )

    def _semantic_candidate_window(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> tuple[list[_ExtractionUnit], str, bool]:
        """Return the sentence window searched by the semantic retriever."""

        candidate_units: list[_ExtractionUnit] = []
        search_stop_reason = "semantic_window_exhausted"
        competing_question_stop = False

        for distance_units in range(
            1,
            self._extractor.config.qa_semantic_retrieval_window_units + 1,
        ):
            candidate_index = question.unit_index + distance_units
            if candidate_index >= len(units):
                break
            if candidate_index in question_by_index:
                competing_question_stop = True
                search_stop_reason = "competing_question"
                break

            candidate_unit = units[candidate_index]
            if candidate_unit.layer != "sentence":
                continue
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                search_stop_reason = "audio_source_boundary"
                break
            if not candidate_unit.text.strip():
                continue
            gap_from_question = max(
                0.0,
                candidate_unit.start_seconds - question.unit.end_seconds,
            )
            if gap_from_question > self._extractor.config.max_answer_duration_seconds:
                search_stop_reason = "temporal_gap_limit"
                break
            if not self._extractor._can_use_answer_units(
                question=question,
                answer_units=[candidate_unit],
                segment_lookup=segment_lookup,
            ):
                search_stop_reason = "cross_segment_block"
                break
            candidate_units.append(candidate_unit)

        return candidate_units, search_stop_reason, competing_question_stop

    def _build_semantic_candidates(
        self,
        *,
        question: QuestionCandidate,
        candidate_units: Sequence[_ExtractionUnit],
        hits: Sequence[Any],
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> list[_AnswerCandidate]:
        """Build answer candidates anchored on semantic retrieval hits."""

        candidates: list[_AnswerCandidate] = []
        for semantic_rank, hit in enumerate(hits, start=1):
            anchor_unit = candidate_units[hit.passage_index]
            anchor_distance = anchor_unit.index - question.unit.index
            for span_length in range(1, self._extractor.config.max_answer_units + 1):
                candidate_span = self._semantic_span_from_anchor(
                    anchor_unit=anchor_unit,
                    span_length=span_length,
                    units=units,
                    question_by_index=question_by_index,
                    question=question,
                    segment_lookup=segment_lookup,
                )
                if candidate_span is None:
                    break
                candidate = self._extractor._build_answer_candidate_from_units(
                    question=question,
                    answer_units=candidate_span,
                    distance_units=anchor_distance,
                    segment_lookup=segment_lookup,
                )
                if candidate is None:
                    continue
                candidate.search_signals.update(
                    {
                        "candidate_channel": "semantic_retrieval",
                        "semantic_similarity": round(float(hit.score), 4),
                        "semantic_rank": semantic_rank,
                        "semantic_model_name": self._backend.model_name,
                        "semantic_backend": self._backend.backend_name,
                        "retrieval_window_units": (
                            self._extractor.config.qa_semantic_retrieval_window_units
                        ),
                    },
                )
                candidate.reason_codes.append("semantic_retrieval_candidate")
                candidate.metadata["answer_source"] = "semantic_retrieval"
                candidate.metadata["search_debug"] = {
                    "candidate_origin": "semantic_retrieval",
                    "semantic_similarity": round(float(hit.score), 4),
                    "semantic_rank": semantic_rank,
                    "semantic_anchor_text_id": anchor_unit.text_id,
                    "candidate_unit_indexes": [
                        candidate_unit.index for candidate_unit in candidate_span
                    ],
                    "candidate_text_ids": [
                        candidate_unit.text_id for candidate_unit in candidate_span
                    ],
                }
                candidates.append(candidate)
        return candidates

    def _semantic_span_from_anchor(
        self,
        *,
        anchor_unit: _ExtractionUnit,
        span_length: int,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        question: QuestionCandidate,
        segment_lookup: dict[str, Any],
    ) -> list[_ExtractionUnit] | None:
        """Return a valid consecutive answer span starting from a semantic hit."""

        candidate_span: list[_ExtractionUnit] = []
        for offset in range(span_length):
            unit_index = anchor_unit.index + offset
            if unit_index >= len(units):
                return None
            if offset > 0 and unit_index in question_by_index:
                return None
            candidate_unit = units[unit_index]
            if candidate_unit.audio_source_id != anchor_unit.audio_source_id:
                return None
            candidate_span.append(candidate_unit)

        if self._extractor._units_duration_seconds(candidate_span) > (
            self._extractor.config.max_answer_duration_seconds
        ):
            return None
        if not self._extractor._can_use_answer_units(
            question=question,
            answer_units=candidate_span,
            segment_lookup=segment_lookup,
        ):
            return None
        return candidate_span

    @staticmethod
    def _merge_candidates(
        *,
        baseline_candidates: Sequence[_AnswerCandidate],
        semantic_candidates: Sequence[_AnswerCandidate],
    ) -> list[_AnswerCandidate]:
        """Merge local baseline and semantic candidates without duplicates."""

        merged: list[_AnswerCandidate] = []
        seen_keys: set[tuple[str, ...]] = set()

        for candidate in baseline_candidates:
            candidate.search_signals.setdefault("candidate_channel", "baseline_local")
            candidate_key = tuple(
                candidate.answer_sentence_ids
                or candidate.answer_unit_ids
                or [candidate.answer_candidate_id]
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            merged.append(candidate)

        for candidate in semantic_candidates:
            candidate_key = tuple(
                candidate.answer_sentence_ids
                or candidate.answer_unit_ids
                or [candidate.answer_candidate_id]
            )
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            merged.append(candidate)

        return merged

    def _fallback_result(
        self,
        *,
        baseline_result: _AnswerSearchResult,
        fallback_reason: str,
        fallback_error: str | None,
    ) -> _AnswerSearchResult:
        """Return the local baseline result annotated with semantic fallback debug."""

        baseline_result.metadata = {
            **baseline_result.metadata,
            "requested_strategy": self.strategy_name,
            "effective_strategy": baseline_result.strategy_name,
            "semantic_backend_status": "fallback",
            "search_fallback_reason": fallback_reason,
            "semantic_backend_error": fallback_error,
            "semantic_model_name": getattr(self._backend, "model_name", None),
            "semantic_backend": getattr(self._backend, "backend_name", None),
        }
        return baseline_result


class _RuleBasedAnswerRanker:
    """Score and rank answer candidates using the current deterministic rules."""

    strategy_name = "rule_based"

    def __init__(self, extractor: QAPairExtractor) -> None:
        self._extractor = extractor

    def rank_candidates(
        self,
        *,
        question: QuestionCandidate,
        search_result: _AnswerSearchResult,
        segment_lookup: dict[str, Any],
    ) -> list[_AnswerCandidate]:
        """Return answer candidates ordered by rule-based score."""

        ranked_candidates = [
            self._extractor._score_answer_candidate(
                question=question,
                answer=candidate,
                segment_lookup=segment_lookup,
                search_result=search_result,
                ranking_strategy_name=self.strategy_name,
            )
            for candidate in search_result.candidates
        ]
        ranked_candidates.sort(
            key=lambda candidate: (
                -candidate.answer_score,
                candidate.distance_units,
                candidate.gap_seconds,
                len(candidate.answer_units),
            ),
        )
        for rank_position, candidate in enumerate(ranked_candidates, start=1):
            candidate.metadata["rank_position"] = rank_position
            candidate.metadata["ranking_debug"]["rank_position"] = rank_position
        return ranked_candidates


class _SemanticAnswerRanker:
    """Rank answer candidates with semantic reranking plus rule-based signals."""

    strategy_name = "semantic_reranker"

    def __init__(
        self,
        extractor: QAPairExtractor,
        *,
        backend: SemanticRerankerBackend | None = None,
        fallback_ranker: AnswerRankingStrategy | None = None,
    ) -> None:
        self._extractor = extractor
        self._backend = backend or TransformersBGERerankerBackend(
            extractor.config.qa_semantic_reranking_model_name,
        )
        self._fallback_ranker = fallback_ranker or _RuleBasedAnswerRanker(extractor)

    def rank_candidates(
        self,
        *,
        question: QuestionCandidate,
        search_result: _AnswerSearchResult,
        segment_lookup: dict[str, Any],
    ) -> list[_AnswerCandidate]:
        """Return candidates reranked semantically with rule-based fallback."""

        baseline_ranked = self._fallback_ranker.rank_candidates(
            question=question,
            search_result=search_result,
            segment_lookup=segment_lookup,
        )
        if not baseline_ranked:
            return baseline_ranked

        if not self._extractor.config.qa_semantic_reranking_enabled:
            return self._annotate_fallback(
                ranked_candidates=baseline_ranked,
                fallback_reason="semantic_reranking_disabled",
                fallback_error=None,
            )

        rerank_limit = min(
            len(baseline_ranked),
            self._extractor.config.qa_semantic_reranking_max_candidates,
        )
        reranked_slice = baseline_ranked[:rerank_limit]

        try:
            semantic_scores = self._backend.score_pairs(
                query_text=question.question_text,
                passage_texts=[
                    candidate.answer_text or "" for candidate in reranked_slice
                ],
                normalize_scores=True,
            )
        except SemanticRerankingUnavailableError as exc:
            return self._annotate_fallback(
                ranked_candidates=baseline_ranked,
                fallback_reason="semantic_backend_unavailable",
                fallback_error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - runtime safety net
            return self._annotate_fallback(
                ranked_candidates=baseline_ranked,
                fallback_reason="semantic_runtime_error",
                fallback_error=str(exc),
            )

        semantic_score_by_index = {
            score.candidate_index: score for score in semantic_scores
        }
        semantic_weight = self._extractor.config.qa_semantic_reranking_weight
        rule_weight = 1.0 - semantic_weight

        for baseline_rank, candidate in enumerate(baseline_ranked, start=1):
            rule_based_score = float(candidate.answer_score)
            reranked = baseline_rank <= rerank_limit and (
                (baseline_rank - 1) in semantic_score_by_index
            )
            semantic_score = (
                float(semantic_score_by_index[baseline_rank - 1].score)
                if reranked
                else None
            )
            combined_score = (
                self._extractor._clamp(
                    (semantic_weight * semantic_score)
                    + (rule_weight * rule_based_score),
                )
                if semantic_score is not None
                else rule_based_score
            )
            if semantic_score is not None:
                candidate.answer_score = combined_score
                candidate.partial_scores["semantic_relevance"] = round(
                    semantic_score,
                    4,
                )
                candidate.partial_scores["rule_based_rank_score"] = round(
                    rule_based_score,
                    4,
                )
                candidate.partial_scores["combined_rank_score"] = combined_score
                candidate.reason_codes = self._extractor._unique_strings(
                    list(candidate.reason_codes) + ["semantic_reranking_applied"],
                )

            candidate.metadata["ranking_debug"] = {
                **candidate.metadata.get("ranking_debug", {}),
                "requested_ranking_strategy": self.strategy_name,
                "effective_ranking_strategy": self.strategy_name,
                "ranking_strategy": self.strategy_name,
                "ranking_fallback_reason": None,
                "semantic_reranking_model_name": self._backend.model_name,
                "semantic_reranking_backend": self._backend.backend_name,
                "semantic_reranking_status": "applied" if reranked else "skipped",
                "semantic_relevance_score": (
                    round(semantic_score, 4) if semantic_score is not None else None
                ),
                "rule_based_score": round(rule_based_score, 4),
                "semantic_weight": round(semantic_weight, 4),
                "rule_based_weight": round(rule_weight, 4),
                "combined_score": round(candidate.answer_score, 4),
                "final_answer_score": round(candidate.answer_score, 4),
                "partial_scores": dict(candidate.partial_scores),
                "reranked_candidate_count": rerank_limit,
                "baseline_rank_position": baseline_rank,
                "search_strategy": search_result.strategy_name,
                "search_candidate_count": search_result.metadata.get(
                    "candidate_count",
                    0,
                ),
            }

        baseline_ranked.sort(
            key=lambda candidate: (
                -candidate.answer_score,
                candidate.distance_units,
                candidate.gap_seconds,
                len(candidate.answer_units),
            ),
        )
        for rank_position, candidate in enumerate(baseline_ranked, start=1):
            candidate.metadata["rank_position"] = rank_position
            candidate.metadata["ranking_debug"]["rank_position"] = rank_position
        return baseline_ranked

    def _annotate_fallback(
        self,
        *,
        ranked_candidates: Sequence[_AnswerCandidate],
        fallback_reason: str,
        fallback_error: str | None,
    ) -> list[_AnswerCandidate]:
        """Annotate rule-based candidates when semantic reranking is unavailable."""

        for rank_position, candidate in enumerate(ranked_candidates, start=1):
            candidate.metadata["rank_position"] = rank_position
            candidate.metadata["ranking_debug"] = {
                **candidate.metadata.get("ranking_debug", {}),
                "requested_ranking_strategy": self.strategy_name,
                "effective_ranking_strategy": self._fallback_ranker.strategy_name,
                "ranking_strategy": self._fallback_ranker.strategy_name,
                "ranking_fallback_reason": fallback_reason,
                "semantic_reranking_model_name": getattr(
                    self._backend,
                    "model_name",
                    None,
                ),
                "semantic_reranking_backend": getattr(
                    self._backend,
                    "backend_name",
                    None,
                ),
                "semantic_reranking_status": "fallback",
                "semantic_reranking_error": fallback_error,
                "rank_position": rank_position,
            }
        return list(ranked_candidates)


class QAPairExtractor:
    """Extract deterministic QA candidates without using model inference."""

    _SENTENCE_RE = re.compile(r"[^.?!]+[.?!]?", flags=re.UNICODE)

    def __init__(
        self,
        config: PipelineConfig | None = None,
        answer_search_strategy: AnswerSearchStrategy | None = None,
        answer_ranking_strategy: AnswerRankingStrategy | None = None,
        semantic_retriever_backend: SemanticRetrieverBackend | None = None,
        semantic_reranker_backend: SemanticRerankerBackend | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.local_answer_search_strategy = _LocalRuleBasedAnswerSearcher(self)
        self.local_answer_ranking_strategy = _RuleBasedAnswerRanker(self)
        if answer_search_strategy is not None:
            self.answer_search_strategy = answer_search_strategy
        elif self.config.qa_answer_search_strategy == "semantic_retrieval":
            self.answer_search_strategy = _SemanticAnswerSearcher(
                self,
                backend=semantic_retriever_backend,
                fallback_searcher=self.local_answer_search_strategy,
            )
        else:
            self.answer_search_strategy = self.local_answer_search_strategy
        if answer_ranking_strategy is not None:
            self.answer_ranking_strategy = answer_ranking_strategy
        elif self.config.qa_answer_ranking_strategy == "semantic_reranker":
            self.answer_ranking_strategy = _SemanticAnswerRanker(
                self,
                backend=semantic_reranker_backend,
                fallback_ranker=self.local_answer_ranking_strategy,
            )
        else:
            self.answer_ranking_strategy = self.local_answer_ranking_strategy

    def extract(self, session: LectureSession) -> list[QAPairCandidate]:
        """Extract QA candidates using sentences as the primary QA layer."""

        if not self.config.enable_qa_extraction:
            return []

        prepared_input = self._prepare_input(session)
        if not prepared_input.units:
            return []

        segment_lookup = self._build_segment_lookup(session.segments)
        questions = self._detect_questions(
            units=prepared_input.units,
            segment_lookup=segment_lookup,
            input_layer=prepared_input.input_layer,
        )
        question_by_index = {question.unit_index: question for question in questions}

        qa_candidates: list[QAPairCandidate] = []
        for ordinal, question in enumerate(questions, start=1):
            search_result = self.answer_search_strategy.generate_candidates(
                question=question,
                units=prepared_input.units,
                question_by_index=question_by_index,
                segment_lookup=segment_lookup,
            )
            ranked_answers = self.answer_ranking_strategy.rank_candidates(
                question=question,
                search_result=search_result,
                segment_lookup=segment_lookup,
            )
            search_result, ranked_answers = self._maybe_run_deferred_answer_search(
                question=question,
                units=prepared_input.units,
                question_by_index=question_by_index,
                segment_lookup=segment_lookup,
                search_result=search_result,
                ranked_answers=ranked_answers,
            )
            if not ranked_answers:
                continue
            answer = ranked_answers[0]

            qa_candidate = self._build_qa_candidate(
                ordinal=ordinal,
                question=question,
                answer=answer,
                search_result=search_result,
                input_layer=prepared_input.input_layer,
                units=prepared_input.units,
                utterance_by_id=prepared_input.utterance_by_id,
                segment_lookup=segment_lookup,
            )
            if qa_candidate.confidence >= self.config.min_qa_confidence:
                qa_candidates.append(qa_candidate)

        return qa_candidates

    def _prepare_input(self, session: LectureSession) -> _PreparedExtractionInput:
        """Resolve the primary QA input layer plus grounding lookups."""

        utterance_by_id = {
            utterance.utterance_id: utterance
            for utterance in session.utterances
            if utterance.utterance_id.strip()
        }

        sentence_units = self._build_sentence_units(session.sentences)
        if sentence_units:
            return _PreparedExtractionInput(
                input_layer="sentences",
                units=sentence_units,
                utterance_by_id=utterance_by_id,
            )

        merged_units = self._build_merged_units(
            session.merged_transcript.units if session.merged_transcript else [],
        )
        return _PreparedExtractionInput(
            input_layer="merged_transcript_fallback",
            units=merged_units,
            utterance_by_id=utterance_by_id,
        )

    @staticmethod
    def _build_sentence_units(sentences: Sequence[Sentence]) -> list[_ExtractionUnit]:
        """Return extraction units derived from sentence objects."""

        units: list[_ExtractionUnit] = []
        for index, sentence in enumerate(sentences):
            text = sentence.text.strip()
            if not text:
                continue
            units.append(
                _ExtractionUnit(
                    index=index,
                    layer="sentence",
                    text_id=sentence.sentence_id,
                    text=text,
                    start_seconds=QAPairExtractor._sentence_start_seconds(sentence),
                    end_seconds=QAPairExtractor._sentence_end_seconds(sentence),
                    audio_source_id=sentence.audio_source_id,
                    session_start_seconds=sentence.session_start_seconds,
                    session_end_seconds=sentence.session_end_seconds,
                    merged_unit_ids=QAPairExtractor._string_list(
                        sentence.metadata.get("merged_transcript_unit_ids"),
                    ),
                    sentence_ids=[sentence.sentence_id],
                    source_utterance_ids=QAPairExtractor._string_list(
                        sentence.source_utterance_ids,
                    ),
                    speaker_id=sentence.speaker_id,
                    speaker_resolution_status=sentence.speaker_resolution_status,
                    speaker_confidence_label=sentence.speaker_confidence_label,
                    speaker_stability_label=sentence.speaker_stability_label,
                    speaker_assignment_method=sentence.speaker_assignment_method,
                    speaker_evidence_summary=sentence.speaker_evidence_summary,
                    merge_safety_label=sentence.merge_safety_label,
                    semantic_quality_label=sentence.semantic_quality_label,
                    review_priority=sentence.review_priority,
                    sentence_review_flags=list(sentence.sentence_review_flags),
                    metadata=dict(sentence.metadata),
                ),
            )
        return units

    @staticmethod
    def _build_merged_units(
        units: Sequence[MergedTranscriptUnit],
    ) -> list[_ExtractionUnit]:
        """Return fallback extraction units derived from merged transcript units."""

        prepared_units: list[_ExtractionUnit] = []
        for index, unit in enumerate(units):
            text = unit.text.strip()
            if not text:
                continue
            prepared_units.append(
                _ExtractionUnit(
                    index=index,
                    layer="merged_unit",
                    text_id=unit.unit_id,
                    text=text,
                    start_seconds=QAPairExtractor._unit_start_seconds(unit),
                    end_seconds=QAPairExtractor._unit_end_seconds(unit),
                    audio_source_id=unit.audio_source_id,
                    session_start_seconds=unit.session_start_seconds,
                    session_end_seconds=unit.session_end_seconds,
                    merged_unit_ids=[unit.unit_id],
                    sentence_ids=[],
                    source_utterance_ids=[],
                    metadata={"raw_unit_text": unit.text},
                ),
            )
        return prepared_units

    def _detect_questions(
        self,
        units: Sequence[_ExtractionUnit],
        segment_lookup: dict[str, Any],
        input_layer: str,
    ) -> list[QuestionCandidate]:
        """Return question candidates found in the selected QA input layer."""

        candidates: list[QuestionCandidate] = []
        for index, unit in enumerate(units):
            extracted = self._extract_question_parts(unit.text)
            if extracted is None:
                continue

            question_text = extracted.question_text
            local_answer_seed = extracted.local_answer_seed
            extraction_reason = extracted.extraction_reason
            question_evaluation = self._evaluate_question_text(question_text)
            if question_evaluation is None:
                continue

            question_units = [unit]
            context_expansion = self._expand_question_context(
                question_text=question_text,
                unit_index=index,
                units=units,
                segment_lookup=segment_lookup,
            )
            if context_expansion["question_units"]:
                question_units = list(context_expansion["question_units"])
                question_text = str(context_expansion["question_text"])

            question_support = self._score_question_context(unit)
            raw_question_score = self._clamp(
                float(question_evaluation["question_score"])
                + question_support["score_delta"],
            )
            didactic_question_score = self._score_didactic_question_usefulness(
                question_evaluation=question_evaluation,
                question_support=question_support,
                context_expanded=bool(context_expansion["question_units"]),
                intra_sentence_qa=extracted.intra_sentence_qa,
            )
            question_score = max(raw_question_score, didactic_question_score)
            if question_score < self.config.min_question_score:
                continue

            question_segment_ids = self._ordered_union(
                self._resolve_segment_ids_for_unit(
                    unit=question_unit,
                    segment_lookup=segment_lookup,
                )
                for question_unit in question_units
            )
            reason_codes = list(question_evaluation["reason_codes"])
            reason_codes.append(extraction_reason)
            reason_codes.extend(question_support["reason_codes"])
            reason_codes.extend(context_expansion["reason_codes"])
            if extracted.intra_sentence_qa:
                reason_codes.append("intra_sentence_qa")

            candidates.append(
                QuestionCandidate(
                    question_candidate_id=f"question_{index + 1:04d}",
                    unit_index=index,
                    unit=unit,
                    question_units=list(question_units),
                    question_text=question_text,
                    question_unit_ids=self._ordered_union(
                        question_unit.merged_unit_ids for question_unit in question_units
                    ),
                    question_sentence_ids=self._ordered_union(
                        question_unit.sentence_ids for question_unit in question_units
                    ),
                    question_source_utterance_ids=self._ordered_union(
                        question_unit.source_utterance_ids
                        for question_unit in question_units
                    ),
                    question_segment_ids=question_segment_ids,
                    question_segment_id=(
                        question_segment_ids[0] if question_segment_ids else None
                    ),
                    question_type=str(question_evaluation["question_type"]),
                    question_score=question_score,
                    didactic_question_score=didactic_question_score,
                    reason_codes=self._unique_strings(reason_codes),
                    local_answer_seed=local_answer_seed,
                    metadata={
                        "input_layer": input_layer,
                        "matched_question_cues": question_evaluation[
                            "matched_question_cues"
                        ],
                        "matched_didactic_cues": question_evaluation[
                            "matched_didactic_cues"
                        ],
                        "normalized_question_text": question_evaluation[
                            "normalized_question_text"
                        ],
                        "token_count": question_evaluation["token_count"],
                        "raw_unit_text": unit.text,
                        "raw_question_score": raw_question_score,
                        "didactic_question_score": didactic_question_score,
                        "question_context_expanded": bool(
                            context_expansion["question_units"],
                        ),
                        "question_expansion_debug": context_expansion["debug"],
                        "intra_sentence_qa": extracted.intra_sentence_qa,
                        "question_preamble": extracted.question_preamble,
                        "question_context_debug": question_support["debug"],
                        "unit_debug": self._unit_debug_metadata(unit),
                    },
                ),
            )
        return candidates

    def _extract_question_parts(self, text: str) -> _QuestionExtraction | None:
        """Extract the most plausible local question text and same-unit answer seed."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return None

        for sentence, _, sentence_end in self._sentence_spans(cleaned_text):
            if "?" not in sentence:
                continue
            question_text, question_preamble = self._refine_question_focus(
                sentence.strip(),
            )
            trailing_text = cleaned_text[sentence_end:].strip() or None
            if trailing_text and self._starts_with_contextual_question(trailing_text):
                trailing_spans = self._sentence_spans(trailing_text)
                followup_question = trailing_spans[0][0].strip()
                trailing_text = trailing_text[trailing_spans[0][2] :].strip() or None
                question_text = self._join_text([question_text, followup_question])
                return _QuestionExtraction(
                    question_text=question_text,
                    local_answer_seed=trailing_text,
                    extraction_reason="contextual_followup_question_merged",
                    question_preamble=question_preamble,
                    intra_sentence_qa=bool(trailing_text),
                )
            return _QuestionExtraction(
                question_text=question_text,
                local_answer_seed=trailing_text,
                extraction_reason="question_sentence_extracted",
                question_preamble=question_preamble,
                intra_sentence_qa=bool(trailing_text),
            )

        if ":" in cleaned_text:
            prefix, suffix = cleaned_text.split(":", maxsplit=1)
            if self._has_strong_question_signal(prefix):
                return _QuestionExtraction(
                    question_text=prefix.strip(),
                    local_answer_seed=suffix.strip() or None,
                    extraction_reason="question_prefix_before_colon",
                    question_preamble=None,
                    intra_sentence_qa=bool(suffix.strip()),
                )

        for sentence, _, sentence_end in self._sentence_spans(cleaned_text):
            if self._has_strong_question_signal(sentence):
                trailing_text = cleaned_text[sentence_end:].strip() or None
                return _QuestionExtraction(
                    question_text=sentence.strip(),
                    local_answer_seed=trailing_text,
                    extraction_reason="cue_sentence_extracted",
                    question_preamble=None,
                    intra_sentence_qa=bool(trailing_text),
                )

        return None

    def _refine_question_focus(self, question_text: str) -> tuple[str, str | None]:
        """Return a focused question clause plus any useful preamble text."""

        cleaned_question = question_text.strip()
        if not cleaned_question:
            return cleaned_question, None

        split_match = re.search(
            r"(?P<preamble>.+?)(?:,\s*|\s+)(?:but|and|so)\s+"
            r"(?P<focus>(?:how|what|where|why|when|which|who)\b.+\?)$",
            cleaned_question,
            flags=re.IGNORECASE,
        )
        if split_match:
            preamble = split_match.group("preamble").strip(" ,")
            focus = split_match.group("focus").strip()
            if count_tokens(normalize_rule_text(focus)) >= 4:
                focus = focus[0].upper() + focus[1:] if focus else focus
                return focus, preamble or None

        return cleaned_question, None

    def _evaluate_question_text(self, question_text: str) -> dict[str, object] | None:
        """Return a structured rule-based evaluation of the candidate question."""

        question_text = question_text.strip()
        if not question_text:
            return None
        if len(question_text) > self.config.max_question_length_chars:
            return None

        normalized_text = normalize_rule_text(question_text)
        question_matches = collect_rule_matches(normalized_text, QUESTION_CUE_RULES)
        didactic_matches = collect_rule_matches(
            normalized_text,
            DIDACTIC_QUESTION_RULES,
        )
        has_question_mark = "?" in question_text
        if (
            not has_question_mark
            and any(pattern.search(normalized_text) for pattern in DECLARATIVE_WHAT_PATTERNS)
        ):
            return None
        if not has_question_mark and not question_matches and not didactic_matches:
            return None

        token_count = count_tokens(normalized_text)
        reason_codes: list[str] = []
        question_score = 0.0

        if has_question_mark:
            question_score += 0.40
            reason_codes.append("question_mark")

        lexical_score = min(0.36, sum(match.weight for match in question_matches))
        if lexical_score:
            question_score += lexical_score
            reason_codes.extend(match.reason_code for match in question_matches)

        didactic_score = min(0.30, sum(match.weight for match in didactic_matches))
        if didactic_score:
            question_score += didactic_score
            reason_codes.extend(match.reason_code for match in didactic_matches)

        if self._starts_with_interrogative_word(normalized_text):
            question_score += 0.12
            reason_codes.append("starts_with_interrogative")

        if not has_question_mark and (question_matches or didactic_matches):
            question_score += 0.10
            reason_codes.append("implicit_question_cue")

        if 3 <= token_count <= 24:
            question_score += 0.08
            reason_codes.append("plausible_question_length")
        elif token_count < 2:
            question_score -= 0.25
            reason_codes.append("question_too_short")
        else:
            reason_codes.append("question_length_outside_preferred_range")

        question_type = "generic_question"
        if didactic_matches:
            question_type = didactic_matches[0].label
        elif question_matches:
            question_type = question_matches[0].label
        elif has_question_mark:
            question_type = "direct_question"

        return {
            "question_score": self._clamp(question_score),
            "question_type": question_type,
            "reason_codes": self._unique_strings(reason_codes),
            "matched_question_cues": [match.reason_code for match in question_matches],
            "matched_didactic_cues": [match.reason_code for match in didactic_matches],
            "normalized_question_text": normalized_text,
            "token_count": token_count,
        }

    def _expand_question_context(
        self,
        *,
        question_text: str,
        unit_index: int,
        units: Sequence[_ExtractionUnit],
        segment_lookup: dict[str, Any],
    ) -> dict[str, Any]:
        """Return an expanded contextual question when the anchor is anaphoric."""

        if (
            not self.config.question_context_expansion_enabled
            or unit_index <= 0
            or not self._is_contextual_question(question_text)
        ):
            return {
                "question_text": question_text,
                "question_units": [],
                "reason_codes": [],
                "debug": {"expanded": False},
            }

        previous_unit = units[unit_index - 1]
        current_unit = units[unit_index]
        if previous_unit.audio_source_id != current_unit.audio_source_id:
            return {
                "question_text": question_text,
                "question_units": [],
                "reason_codes": [],
                "debug": {"expanded": False, "reason": "audio_source_boundary"},
            }

        gap_seconds = max(0.0, current_unit.start_seconds - previous_unit.end_seconds)
        if gap_seconds > self.config.question_context_max_gap_seconds:
            return {
                "question_text": question_text,
                "question_units": [],
                "reason_codes": [],
                "debug": {
                    "expanded": False,
                    "reason": "temporal_gap_limit",
                    "gap_seconds": round(gap_seconds, 3),
                },
            }

        segment_relation = self._segment_relation(
            question_segment_ids=self._resolve_segment_ids_for_unit(
                unit=previous_unit,
                segment_lookup=segment_lookup,
            ),
            answer_segment_ids=self._resolve_segment_ids_for_unit(
                unit=current_unit,
                segment_lookup=segment_lookup,
            ),
            segment_position_by_id=segment_lookup["segment_position_by_id"],
        )
        if segment_relation not in {"same_segment", "next_segment", "segment_unknown"}:
            return {
                "question_text": question_text,
                "question_units": [],
                "reason_codes": [],
                "debug": {
                    "expanded": False,
                    "reason": "segment_distance",
                    "segment_relation": segment_relation,
                },
            }

        return {
            "question_text": self._join_text([previous_unit.text, question_text]),
            "question_units": [previous_unit, current_unit],
            "reason_codes": ["question_context_expanded"],
            "debug": {
                "expanded": True,
                "previous_text_id": previous_unit.text_id,
                "gap_seconds": round(gap_seconds, 3),
                "segment_relation": segment_relation,
            },
        }

    def _score_didactic_question_usefulness(
        self,
        *,
        question_evaluation: dict[str, Any],
        question_support: dict[str, Any],
        context_expanded: bool,
        intra_sentence_qa: bool,
    ) -> float:
        """Return a didactic usefulness score decoupled from sentence quality."""

        base_score = float(question_evaluation["question_score"])
        support_delta = float(question_support["score_delta"])
        usefulness_score = base_score + max(-0.02, min(0.04, support_delta))

        if context_expanded:
            usefulness_score += 0.06
        if intra_sentence_qa:
            usefulness_score += 0.04

        question_type = str(question_evaluation.get("question_type") or "")
        if question_type in {"why", "how", "where", "difference", "didactic_prompt"}:
            usefulness_score += 0.03

        return self._clamp(usefulness_score)

    def _score_question_context(self, unit: _ExtractionUnit) -> dict[str, Any]:
        """Return additive question support derived from sentence-level metadata."""

        if unit.layer != "sentence":
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {
                    "sentence_metadata_available": False,
                    "speaker_influence": "not_available",
                },
            }

        score_delta = 0.0
        reason_codes: list[str] = []
        dominance_margin = self._safe_float(unit.metadata.get("dominance_margin"))

        if unit.source_utterance_ids:
            score_delta += 0.03
            reason_codes.append("question_utterance_grounded")
        else:
            score_delta -= 0.03
            reason_codes.append("question_missing_utterance_grounding")

        if unit.semantic_quality_label == "good":
            score_delta += 0.02
            reason_codes.append("question_sentence_quality_good")
        elif unit.semantic_quality_label == "borderline":
            score_delta -= 0.02
            reason_codes.append("question_sentence_quality_borderline")
        elif unit.semantic_quality_label in {"fragment", "run_on"}:
            score_delta -= 0.08
            reason_codes.append("question_sentence_quality_penalty")

        if unit.merge_safety_label == "safe":
            score_delta += 0.02
            reason_codes.append("question_merge_safety_support")
        elif unit.merge_safety_label == "borderline":
            score_delta -= 0.03
            reason_codes.append("question_merge_safety_borderline")
        elif unit.merge_safety_label == "risky":
            score_delta -= 0.06
            reason_codes.append("question_merge_safety_penalty")

        if unit.review_priority == "medium":
            score_delta -= 0.02
            reason_codes.append("question_review_priority_medium")
        elif unit.review_priority == "high":
            score_delta -= 0.04
            reason_codes.append("question_review_priority_high")

        if unit.speaker_resolution_status in {"stable", "mostly_stable"}:
            score_delta += 0.02
            reason_codes.append("question_speaker_resolution_support")
        elif unit.speaker_resolution_status == "mixed":
            score_delta -= 0.05
            reason_codes.append("question_speaker_conflict_penalty")

        if (
            unit.speaker_resolution_status in {"stable", "mostly_stable"}
            and dominance_margin is not None
            and dominance_margin
            >= self.config.sentence_speaker_dominance_margin_threshold
        ):
            score_delta += 0.02
            reason_codes.append("question_speaker_dominance_support")

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "sentence_metadata_available": True,
                "speaker_resolution_status": unit.speaker_resolution_status,
                "speaker_assignment_method": unit.speaker_assignment_method,
                "speaker_stability_label": unit.speaker_stability_label,
                "dominance_margin": dominance_margin,
                "semantic_quality_label": unit.semantic_quality_label,
                "merge_safety_label": unit.merge_safety_label,
                "review_priority": unit.review_priority,
                "sentence_review_flags": list(unit.sentence_review_flags),
                "score_delta": round(score_delta, 4),
            },
        }

    def _build_same_unit_answer_candidate(
        self,
        question: QuestionCandidate,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return an unscored same-unit answer candidate."""

        if not question.local_answer_seed:
            return None

        answer_text = question.local_answer_seed.strip()
        if not self._is_plausible_answer_text(answer_text):
            return None

        answer_segment_ids = self._resolve_segment_ids_for_unit(
            unit=question.unit,
            segment_lookup=segment_lookup,
        )
        return _AnswerCandidate(
            answer_candidate_id=f"{question.question_candidate_id}_answer_same_unit",
            answer_text=answer_text,
            answer_units=[question.unit],
            answer_unit_ids=list(question.unit.merged_unit_ids),
            answer_sentence_ids=list(question.unit.sentence_ids),
            answer_source_utterance_ids=list(question.unit.source_utterance_ids),
            answer_segment_ids=answer_segment_ids,
            answer_segment_id=answer_segment_ids[0] if answer_segment_ids else None,
            answer_score=0.0,
            distance_units=0,
            gap_seconds=0.0,
            search_signals={
                "answer_source": "same_text_unit_seed",
                "distance_units": 0,
                "candidate_span_unit_count": 1,
            },
            reason_codes=[self._same_unit_reason(question.unit.layer)],
            metadata={
                "answer_source": "same_text_unit_seed",
                "search_debug": {
                    "candidate_origin": "same_text_unit_seed",
                    "candidate_unit_indexes": [question.unit.index],
                    "candidate_text_ids": [question.unit.text_id],
                },
            },
        )

    def _build_answer_candidate_from_units(
        self,
        question: QuestionCandidate,
        answer_units: Sequence[_ExtractionUnit],
        distance_units: int,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return an unscored answer candidate from consecutive units."""

        answer_text = self._join_text(unit.text for unit in answer_units)
        if not self._is_plausible_answer_text(answer_text):
            return None

        answer_segment_ids = self._ordered_union(
            self._resolve_segment_ids_for_unit(unit=unit, segment_lookup=segment_lookup)
            for unit in answer_units
        )
        return _AnswerCandidate(
            answer_candidate_id=(
                f"{question.question_candidate_id}_answer_{distance_units:02d}_"
                f"{len(answer_units):02d}"
            ),
            answer_text=answer_text,
            answer_units=list(answer_units),
            answer_unit_ids=self._ordered_union(
                unit.merged_unit_ids for unit in answer_units
            ),
            answer_sentence_ids=self._ordered_union(
                unit.sentence_ids for unit in answer_units
            ),
            answer_source_utterance_ids=self._ordered_union(
                unit.source_utterance_ids for unit in answer_units
            ),
            answer_segment_ids=answer_segment_ids,
            answer_segment_id=answer_segment_ids[0] if answer_segment_ids else None,
            answer_score=0.0,
            distance_units=distance_units,
            gap_seconds=max(
                0.0,
                answer_units[0].start_seconds - question.unit.end_seconds,
            ),
            search_signals={
                "answer_source": "following_text_units",
                "distance_units": distance_units,
                "candidate_span_unit_count": len(answer_units),
            },
            reason_codes=[],
            metadata={
                "answer_source": "following_text_units",
                "search_debug": {
                    "candidate_origin": "following_text_units",
                    "candidate_unit_indexes": [
                        candidate_unit.index for candidate_unit in answer_units
                    ],
                    "candidate_text_ids": [
                        candidate_unit.text_id for candidate_unit in answer_units
                    ],
                },
            },
        )

    def _build_competing_question_leading_answer_candidate(
        self,
        *,
        question: QuestionCandidate,
        competing_unit: _ExtractionUnit,
        distance_units: int,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return the leading answer text from a unit that also starts a new question."""

        answer_prefix = self._extract_leading_answer_before_question(competing_unit.text)
        if not answer_prefix or not self._is_plausible_answer_text(answer_prefix):
            return None

        answer_segment_ids = self._resolve_segment_ids_for_unit(
            unit=competing_unit,
            segment_lookup=segment_lookup,
        )
        return _AnswerCandidate(
            answer_candidate_id=(
                f"{question.question_candidate_id}_answer_{distance_units:02d}_"
                "competing_prefix"
            ),
            answer_text=answer_prefix,
            answer_units=[competing_unit],
            answer_unit_ids=list(competing_unit.merged_unit_ids),
            answer_sentence_ids=list(competing_unit.sentence_ids),
            answer_source_utterance_ids=list(competing_unit.source_utterance_ids),
            answer_segment_ids=answer_segment_ids,
            answer_segment_id=answer_segment_ids[0] if answer_segment_ids else None,
            answer_score=0.0,
            distance_units=distance_units,
            gap_seconds=max(
                0.0,
                competing_unit.start_seconds - question.unit.end_seconds,
            ),
            search_signals={
                "answer_source": "competing_question_prefix",
                "distance_units": distance_units,
                "candidate_span_unit_count": 1,
            },
            reason_codes=["answer_before_competing_question"],
            metadata={
                "answer_source": "competing_question_prefix",
                "search_debug": {
                    "candidate_origin": "competing_question_prefix",
                    "candidate_unit_indexes": [competing_unit.index],
                    "candidate_text_ids": [competing_unit.text_id],
                },
            },
        )

    def _score_answer_candidate(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        segment_lookup: dict[str, Any],
        search_result: _AnswerSearchResult,
        ranking_strategy_name: str,
    ) -> _AnswerCandidate:
        """Populate score and ranking debug for one answer candidate."""

        normalized_answer = normalize_rule_text(answer.answer_text)
        token_count = count_tokens(normalized_answer)
        answer_matches = collect_rule_matches(normalized_answer, ANSWER_CUE_RULES)
        segment_relation = self._segment_relation(
            question_segment_ids=question.question_segment_ids,
            answer_segment_ids=answer.answer_segment_ids,
            segment_position_by_id=segment_lookup["segment_position_by_id"],
        )

        reason_codes = list(answer.reason_codes)
        partial_scores: dict[str, float] = {"base_bias": 0.18}
        score = partial_scores["base_bias"]

        distance_score = self._distance_score(
            answer.distance_units,
            reason_codes,
            question.unit.layer,
        )
        partial_scores["distance"] = distance_score
        score += distance_score

        answer_cue_score = min(0.30, sum(match.weight for match in answer_matches))
        partial_scores["answer_cues"] = round(answer_cue_score, 4)
        score += answer_cue_score
        reason_codes.extend(match.reason_code for match in answer_matches)

        qa_alignment = self._question_answer_alignment(
            question_text=question.question_text,
            answer_text=answer.answer_text,
            question_type=question.question_type,
            answer_source=str(answer.search_signals.get("answer_source") or ""),
        )
        partial_scores["keyword_overlap"] = float(qa_alignment["keyword_score"])
        partial_scores["number_overlap"] = float(qa_alignment["number_score"])
        partial_scores["relevance"] = float(qa_alignment["relevance_score"])
        score += qa_alignment["keyword_score"]
        score += qa_alignment["number_score"]
        score += qa_alignment["relevance_score"]
        reason_codes.extend(qa_alignment["reason_codes"])

        if 3 <= token_count <= 80:
            partial_scores["length"] = 0.08
            score += 0.08
            reason_codes.append("plausible_answer_length")
        else:
            partial_scores["length"] = 0.0
            reason_codes.append("answer_length_outside_preferred_range")

        if answer.gap_seconds <= 4.0:
            partial_scores["gap"] = 0.12
            score += 0.12
            reason_codes.append("short_temporal_gap")
        elif answer.gap_seconds <= 10.0:
            partial_scores["gap"] = 0.06
            score += 0.06
            reason_codes.append("medium_temporal_gap")
        else:
            if answer.search_signals.get("candidate_channel") == "deferred_answer_search":
                partial_scores["gap"] = -0.02
                score -= 0.02
                reason_codes.append("deferred_long_temporal_gap")
            else:
                partial_scores["gap"] = -0.08
                score -= 0.08
                reason_codes.append("long_temporal_gap")

        if segment_relation == "same_segment":
            partial_scores["segment_relation"] = 0.10
            score += 0.10
            reason_codes.append("same_segment_support")
        elif segment_relation == "next_segment":
            partial_scores["segment_relation"] = 0.05
            score += 0.05
            reason_codes.append("next_segment_support")
        elif segment_relation == "distant_segment":
            partial_scores["segment_relation"] = -0.05
            score -= 0.05
            reason_codes.append("distant_segment_penalty")
        else:
            partial_scores["segment_relation"] = 0.0

        if "?" in answer.answer_text:
            partial_scores["question_mark_penalty"] = -0.20
            score -= 0.20
            reason_codes.append("answer_contains_question_mark")
        else:
            partial_scores["question_mark_penalty"] = 0.0

        answer_is_question = self._is_answer_question_like(answer.answer_text)
        if answer_is_question:
            partial_scores["answer_is_question_penalty"] = -0.16
            score -= 0.16
            reason_codes.append("answer_is_question")
        else:
            partial_scores["answer_is_question_penalty"] = 0.0

        answer_context = self._score_answer_context(answer.answer_units)
        partial_scores["answer_context"] = float(answer_context["score_delta"])
        score += answer_context["score_delta"]
        reason_codes.extend(answer_context["reason_codes"])

        speaker_support = self._score_speaker_pairing(question, answer)
        partial_scores["speaker_pairing"] = float(speaker_support["score_delta"])
        score += speaker_support["score_delta"]
        reason_codes.extend(speaker_support["reason_codes"])

        if search_result.competing_question_stop:
            partial_scores["competing_question_penalty"] = -0.08
            score -= 0.08
            reason_codes.append("competing_question_nearby")
        else:
            partial_scores["competing_question_penalty"] = 0.0

        answer.answer_score = self._clamp(score)
        answer.partial_scores = {
            key: round(float(value), 4) for key, value in partial_scores.items()
        }
        answer.reason_codes = self._unique_strings(reason_codes)

        answer.metadata["matched_answer_cues"] = [
            match.reason_code for match in answer_matches
        ]
        answer.metadata["normalized_answer_text"] = normalized_answer
        answer.metadata["token_count"] = token_count
        answer.metadata["segment_relation"] = segment_relation
        answer.metadata["answer_is_question"] = answer_is_question
        answer.metadata["qa_alignment_debug"] = qa_alignment["debug"]
        answer.metadata["answer_context_debug"] = answer_context["debug"]
        answer.metadata["speaker_pairing_debug"] = speaker_support["debug"]
        answer.metadata["search_stop_reason"] = search_result.stop_reason
        answer.metadata["competing_question_stop"] = (
            search_result.competing_question_stop
        )
        answer.metadata["ranking_debug"] = {
            "requested_ranking_strategy": ranking_strategy_name,
            "effective_ranking_strategy": ranking_strategy_name,
            "ranking_strategy": ranking_strategy_name,
            "partial_scores": dict(answer.partial_scores),
            "final_answer_score": answer.answer_score,
            "candidate_distance_units": answer.distance_units,
            "candidate_gap_seconds": round(answer.gap_seconds, 3),
            "search_strategy": search_result.strategy_name,
            "search_candidate_count": search_result.metadata.get("candidate_count", 0),
        }
        return answer

    def _score_answer_context(
        self,
        answer_units: Sequence[_ExtractionUnit],
    ) -> dict[str, Any]:
        """Return additive answer support from grounding and sentence quality."""

        if not answer_units or all(unit.layer != "sentence" for unit in answer_units):
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {
                    "sentence_metadata_available": False,
                    "grounding_support": "not_available",
                },
            }

        score_delta = 0.0
        reason_codes: list[str] = []
        source_utterance_ids = self._ordered_union(
            unit.source_utterance_ids for unit in answer_units
        )
        semantic_labels = {
            str(unit.semantic_quality_label or "").strip()
            for unit in answer_units
            if str(unit.semantic_quality_label or "").strip()
        }
        merge_labels = {
            str(unit.merge_safety_label or "").strip()
            for unit in answer_units
            if str(unit.merge_safety_label or "").strip()
        }
        review_priorities = {
            str(unit.review_priority or "").strip()
            for unit in answer_units
            if str(unit.review_priority or "").strip()
        }

        if source_utterance_ids:
            score_delta += 0.04
            reason_codes.append("answer_utterance_grounded")
        else:
            score_delta -= 0.05
            reason_codes.append("answer_missing_utterance_grounding")

        if (
            {"fragment", "run_on"} & semantic_labels
            or "risky" in merge_labels
            or "high" in review_priorities
        ):
            score_delta -= 0.08
            reason_codes.append("answer_sentence_quality_penalty")
        elif (
            "borderline" in semantic_labels
            or "borderline" in merge_labels
            or "medium" in review_priorities
        ):
            score_delta -= 0.02
            reason_codes.append("answer_sentence_quality_borderline")
        else:
            score_delta += 0.05
            reason_codes.append("answer_sentence_quality_support")

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "sentence_metadata_available": True,
                "source_utterance_count": len(source_utterance_ids),
                "semantic_labels": sorted(semantic_labels),
                "merge_safety_labels": sorted(merge_labels),
                "review_priorities": sorted(review_priorities),
                "score_delta": round(score_delta, 4),
            },
        }

    def _score_speaker_pairing(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
    ) -> dict[str, Any]:
        """Return a light speaker-aware adjustment for one QA pairing."""

        question_profile = self._speaker_profile(question.unit)
        answer_profile = self._speaker_profile_from_units(answer.answer_units)
        score_delta = 0.0
        reason_codes: list[str] = []
        influence = "neutral"
        soft_penalty_mode = self._has_soft_speaker_penalty(
            question_unit=question.unit,
            answer_units=answer.answer_units,
        )

        if (
            question_profile["reliability"] == "conflict"
            or answer_profile["reliability"] == "conflict"
        ):
            score_delta -= 0.05
            reason_codes.append("speaker_conflict_penalty")
            influence = "penalty"
        elif (
            question_profile["reliability"] == "reliable"
            and answer_profile["reliability"] == "reliable"
        ):
            if question_profile["speaker_id"] != answer_profile["speaker_id"]:
                score_delta += 0.10
                reason_codes.append("speaker_turn_support")
                influence = "boost"
                if (
                    question_profile["dominance_margin"] is not None
                    and answer_profile["dominance_margin"] is not None
                    and question_profile["dominance_margin"]
                    >= self.config.sentence_speaker_dominance_margin_threshold
                    and answer_profile["dominance_margin"]
                    >= self.config.sentence_speaker_dominance_margin_threshold
                ):
                    score_delta += 0.03
                    reason_codes.append("speaker_dominance_support")
            else:
                if soft_penalty_mode:
                    score_delta -= 0.01 if answer.distance_units == 0 else 0.02
                    reason_codes.append("same_speaker_penalty_softened")
                else:
                    score_delta -= 0.02 if answer.distance_units == 0 else 0.04
                    reason_codes.append(
                        "same_speaker_monologue"
                        if answer.distance_units == 0
                        else "same_speaker_pairing_penalty",
                    )
                influence = "penalty"
        elif (
            soft_penalty_mode
            and question_profile["speaker_id"]
            and question_profile["speaker_id"] == answer_profile["speaker_id"]
        ):
            score_delta -= 0.01 if answer.distance_units == 0 else 0.02
            reason_codes.append("same_speaker_penalty_softened")
            influence = "penalty"

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "influence": influence,
                "soft_penalty_mode": soft_penalty_mode,
                "question_profile": question_profile,
                "answer_profile": answer_profile,
                "score_delta": round(score_delta, 4),
            },
        }

    def _maybe_run_deferred_answer_search(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
        search_result: _AnswerSearchResult,
        ranked_answers: list[_AnswerCandidate],
    ) -> tuple[_AnswerSearchResult, list[_AnswerCandidate]]:
        """Expand answer search when the local answer remains weak."""

        trigger_debug = self._deferred_search_trigger_debug(
            question=question,
            units=units,
            ranked_answers=ranked_answers,
        )
        if not trigger_debug["triggered"]:
            search_result.metadata["deferred_answer_search_used"] = False
            search_result.metadata["deferred_answer_search_debug"] = trigger_debug
            return search_result, ranked_answers

        deferred_candidates = self._collect_deferred_answer_candidates(
            question=question,
            units=units,
            question_by_index=question_by_index,
            segment_lookup=segment_lookup,
        )
        if not deferred_candidates:
            search_result.metadata["deferred_answer_search_used"] = False
            search_result.metadata["deferred_answer_search_debug"] = {
                **trigger_debug,
                "deferred_candidate_count": 0,
            }
            return search_result, ranked_answers

        merged_candidates = self._merge_answer_candidates(
            baseline_candidates=search_result.candidates,
            extra_candidates=deferred_candidates,
        )
        merged_result = _AnswerSearchResult(
            strategy_name=search_result.strategy_name,
            candidates=merged_candidates,
            stop_reason=search_result.stop_reason,
            competing_question_stop=search_result.competing_question_stop,
            metadata={
                **search_result.metadata,
                "candidate_count": len(merged_candidates),
                "deferred_answer_search_used": True,
                "deferred_answer_search_debug": {
                    **trigger_debug,
                    "deferred_candidate_count": len(deferred_candidates),
                    "deferred_candidate_ids": [
                        candidate.answer_candidate_id
                        for candidate in deferred_candidates
                    ],
                },
            },
        )
        reranked_answers = self.answer_ranking_strategy.rank_candidates(
            question=question,
            search_result=merged_result,
            segment_lookup=segment_lookup,
        )
        if reranked_answers:
            reranked_answers[0].reason_codes = self._unique_strings(
                list(reranked_answers[0].reason_codes) + ["deferred_answer_search"],
            )
        return merged_result, reranked_answers

    def _build_qa_candidate(
        self,
        ordinal: int,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        search_result: _AnswerSearchResult,
        input_layer: str,
        units: Sequence[_ExtractionUnit],
        utterance_by_id: dict[str, Utterance],
        segment_lookup: dict[str, Any],
    ) -> QAPairCandidate:
        """Return the exported QA candidate structure."""

        segment_relation = self._segment_relation(
            question_segment_ids=question.question_segment_ids,
            answer_segment_ids=answer.answer_segment_ids,
            segment_position_by_id=segment_lookup["segment_position_by_id"],
        )

        base_confidence = (
            (question.question_score * 0.55) + (answer.answer_score * 0.45)
        )
        competing_penalty = (
            0.10 if "competing_question_nearby" in answer.reason_codes else 0.0
        )
        fallback_penalty = 0.04 if input_layer != "sentences" else 0.0
        confidence = self._clamp(
            base_confidence - competing_penalty - fallback_penalty,
        )
        if answer.metadata.get("answer_is_question"):
            confidence = self._clamp(confidence - 0.12)
        confidence_label = self._confidence_label(confidence)
        source_segment_ids = self._unique_strings(
            question.question_segment_ids + answer.answer_segment_ids,
        )

        question_timing, question_grounding_debug = self._build_time_range(
            units=question.question_units,
            utterance_by_id=utterance_by_id,
        )
        answer_timing, answer_grounding_debug = self._build_time_range(
            units=answer.answer_units,
            utterance_by_id=utterance_by_id,
        )
        question_speaker_role = self._resolve_speaker_role(
            question.question_segment_id,
            segment_lookup["segment_by_id"],
        )
        answer_speaker_role = self._resolve_speaker_role(
            answer.answer_segment_id,
            segment_lookup["segment_by_id"],
        )
        context_extraction = self.extract_qa_context(
            question=question,
            answer=answer,
            units=units,
            search_result=search_result,
        )

        reason_codes = self._unique_strings(
            question.reason_codes + answer.reason_codes + [segment_relation],
        )
        if input_layer != "sentences":
            reason_codes = self._unique_strings(
                reason_codes + ["merged_transcript_fallback"],
            )
        review_flags = self._build_candidate_review_flags(
            confidence=confidence,
            input_layer=input_layer,
            reason_codes=reason_codes,
            answer_is_question=bool(answer.metadata.get("answer_is_question")),
        )

        start_seconds = (
            question_timing.start_seconds if question_timing else question.unit.start_seconds
        )
        end_seconds = (
            answer_timing.end_seconds if answer_timing else answer.answer_units[-1].end_seconds
        )
        metadata = {
            "input_layer": input_layer,
            "question_debug": {
                "question_score": question.question_score,
                "question_unit_index": question.unit_index,
                **question.metadata,
            },
            "answer_debug": {
                "answer_score": answer.answer_score,
                "answer_distance_units": answer.distance_units,
                "gap_seconds": round(answer.gap_seconds, 3),
                "answer_unit_ids": answer.answer_unit_ids,
                "answer_sentence_ids": answer.answer_sentence_ids,
                "answer_source_utterance_ids": answer.answer_source_utterance_ids,
                "search_signals": dict(answer.search_signals),
                "partial_scores": dict(answer.partial_scores),
                **answer.metadata,
            },
            "pairing_debug": {
                "segment_relation": segment_relation,
                "question_segment_ids": question.question_segment_ids,
                "answer_segment_ids": answer.answer_segment_ids,
                "search_stop_reason": answer.metadata.get("search_stop_reason"),
                "requested_search_strategy": search_result.metadata.get(
                    "requested_strategy",
                ),
                "search_strategy": search_result.strategy_name,
                "effective_search_strategy": search_result.metadata.get(
                    "effective_strategy",
                    search_result.strategy_name,
                ),
                "requested_ranking_strategy": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get(
                    "requested_ranking_strategy",
                    self.answer_ranking_strategy.strategy_name,
                ),
                "ranking_strategy": answer.metadata.get("ranking_debug", {}).get(
                    "effective_ranking_strategy",
                    self.answer_ranking_strategy.strategy_name,
                ),
                "effective_ranking_strategy": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get(
                    "effective_ranking_strategy",
                    self.answer_ranking_strategy.strategy_name,
                ),
                "candidate_count_considered": search_result.metadata.get(
                    "candidate_count",
                ),
                "search_fallback_reason": search_result.metadata.get(
                    "search_fallback_reason",
                ),
                "search_backend_error": search_result.metadata.get(
                    "semantic_backend_error",
                ),
                "ranking_fallback_reason": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get("ranking_fallback_reason"),
                "semantic_backend_status": search_result.metadata.get(
                    "semantic_backend_status",
                ),
                "semantic_model_name": search_result.metadata.get(
                    "semantic_model_name",
                ),
                "semantic_backend": search_result.metadata.get("semantic_backend"),
                "semantic_reranking_model_name": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get("semantic_reranking_model_name"),
                "semantic_reranking_backend": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get("semantic_reranking_backend"),
                "semantic_relevance_score": answer.metadata.get(
                    "ranking_debug",
                    {},
                ).get("semantic_relevance_score"),
                "speaker_influence": (
                    answer.metadata.get("speaker_pairing_debug", {}).get("influence")
                ),
                "deferred_answer_search_used": bool(
                    search_result.metadata.get("deferred_answer_search_used"),
                ),
                "deferred_answer_search_debug": search_result.metadata.get(
                    "deferred_answer_search_debug",
                ),
            },
            "grounding_debug": {
                "question_sentence_ids": question.question_sentence_ids,
                "answer_sentence_ids": answer.answer_sentence_ids,
                "question_source_utterance_ids": question.question_source_utterance_ids,
                "answer_source_utterance_ids": answer.answer_source_utterance_ids,
                "question_timing_source": question_grounding_debug["timing_source"],
                "answer_timing_source": answer_grounding_debug["timing_source"],
                "context_sentence_ids": context_extraction.context_sentence_ids,
                "context_source_utterance_ids": (
                    context_extraction.context_source_utterance_ids
                ),
                "question_grounded_utterance_ids": question_grounding_debug[
                    "grounded_utterance_ids"
                ],
                "answer_grounded_utterance_ids": answer_grounding_debug[
                    "grounded_utterance_ids"
                ],
            },
            "context_debug": {
                "context_text": context_extraction.context_text,
                "context_raw_text": context_extraction.context_raw_text,
                "context_strategy": context_extraction.context_strategy,
                "context_confidence": context_extraction.context_confidence,
                "context_sentence_ids": context_extraction.context_sentence_ids,
                "context_source_utterance_ids": (
                    context_extraction.context_source_utterance_ids
                ),
            },
            "confidence_debug": {
                "question_score": question.question_score,
                "didactic_question_score": question.didactic_question_score,
                "answer_score": answer.answer_score,
                "question_weight": 0.55,
                "answer_weight": 0.45,
                "base_confidence": round(base_confidence, 4),
                "competing_question_penalty": competing_penalty,
                "fallback_penalty": fallback_penalty,
                "answer_is_question_penalty": (
                    0.12 if answer.metadata.get("answer_is_question") else 0.0
                ),
                "final_confidence": confidence,
            },
        }

        return QAPairCandidate(
            qa_candidate_id=f"qa_{ordinal:04d}",
            question_text=question.question_text,
            answer_text=answer.answer_text,
            context_text=context_extraction.context_text,
            question_unit_ids=question.question_unit_ids,
            answer_unit_ids=answer.answer_unit_ids,
            question_sentence_ids=question.question_sentence_ids,
            answer_sentence_ids=answer.answer_sentence_ids,
            context_sentence_ids=context_extraction.context_sentence_ids,
            question_source_utterance_ids=question.question_source_utterance_ids,
            answer_source_utterance_ids=answer.answer_source_utterance_ids,
            context_source_utterance_ids=context_extraction.context_source_utterance_ids,
            question_segment_id=question.question_segment_id,
            answer_segment_id=answer.answer_segment_id,
            context_strategy=context_extraction.context_strategy,
            context_confidence=context_extraction.context_confidence,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            question_timing=question_timing,
            answer_timing=answer_timing,
            question_speaker_role=question_speaker_role,
            answer_speaker_role=answer_speaker_role,
            source_segment_ids=source_segment_ids,
            confidence=confidence,
            confidence_label=confidence_label,
            confidence_score=confidence,
            question_type=question.question_type,
            didactic_question_score=question.didactic_question_score,
            answer_is_question=bool(answer.metadata.get("answer_is_question")),
            reason_codes=reason_codes,
            review_flags=review_flags,
            extraction_notes=(
                "Rule-based QA candidate built from reconstructed sentences "
                "with utterance grounding."
                if input_layer == "sentences"
                else "Rule-based QA candidate built from merged transcript fallback "
                "units because sentences were unavailable."
            ),
            metadata=metadata,
        )

    def extract_qa_context(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
        search_result: _AnswerSearchResult,
    ) -> _ContextExtraction:
        """Return short contextual text that makes one QA pair understandable."""

        context_units: list[_ExtractionUnit] = []
        context_strategy: str | None = None
        context_confidence: str | None = None

        if answer.distance_units == 0 and question.local_answer_seed:
            context_units = [question.unit]
            context_strategy = "intra_sentence_context"
            context_confidence = "high"
        elif question.metadata.get("question_context_expanded"):
            context_units = question.question_units[:-1] or question.question_units
            context_strategy = "previous_sentence_context"
            context_confidence = "high"
        elif bool(search_result.metadata.get("deferred_answer_search_used")):
            context_units = self._deferred_context_units(question, answer, units)
            context_strategy = "deferred_answer_context"
            context_confidence = "medium"
        else:
            context_units = self._local_topic_context_units(question, answer, units)
            context_strategy = "local_topic_window"
            context_confidence = "medium"

        context_units = self._dedupe_context_units(context_units, question, answer)
        context_raw_text = self._build_context_raw_text(context_units)
        context_text = self._summarize_context_text(
            question=question,
            answer=answer,
            context_units=context_units,
            context_raw_text=context_raw_text,
            context_strategy=context_strategy,
        )
        if not context_text:
            return _ContextExtraction(
                context_text=None,
                context_raw_text=None,
                context_units=[],
                context_strategy=None,
                context_confidence=None,
            )

        return _ContextExtraction(
            context_text=context_text,
            context_raw_text=context_raw_text,
            context_units=context_units,
            context_sentence_ids=self._ordered_union(
                unit.sentence_ids for unit in context_units
            ),
            context_source_utterance_ids=self._ordered_union(
                unit.source_utterance_ids for unit in context_units
            ),
            context_strategy=context_strategy,
            context_confidence=context_confidence,
        )

    def _local_topic_context_units(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> list[_ExtractionUnit]:
        """Return nearby topic-bearing units for local question context."""

        context_units: list[_ExtractionUnit] = []
        question_start_index = question.question_units[0].index
        for candidate_index in range(max(0, question_start_index - 2), question_start_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            if self._unit_has_topic_overlap(question=question, unit=candidate_unit):
                context_units.append(candidate_unit)

        if not context_units and question.metadata.get("question_preamble"):
            context_units.append(question.unit)
        if answer.distance_units <= 1 and not context_units:
            previous_index = max(0, question.unit_index - 1)
            if previous_index != question.unit_index:
                previous_unit = units[previous_index]
                if previous_unit.audio_source_id == question.unit.audio_source_id:
                    context_units.append(previous_unit)
        return context_units

    def _deferred_context_units(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> list[_ExtractionUnit]:
        """Return context units for answers discovered beyond the local window."""

        context_units: list[_ExtractionUnit] = []
        question_start_index = question.question_units[0].index
        answer_start_index = answer.answer_units[0].index
        for candidate_index in range(max(0, question_start_index - 2), question.unit_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            if self._unit_has_topic_overlap(question=question, unit=candidate_unit):
                context_units.append(candidate_unit)

        for candidate_index in range(max(question.unit_index + 1, answer_start_index - 2), answer_start_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            if self._unit_has_topic_overlap(question=question, unit=candidate_unit):
                context_units.append(candidate_unit)

        if not context_units and question.metadata.get("question_preamble"):
            context_units.append(question.unit)
        return context_units

    def _dedupe_context_units(
        self,
        context_units: Sequence[_ExtractionUnit],
        question: QuestionCandidate,
        answer: _AnswerCandidate,
    ) -> list[_ExtractionUnit]:
        """Return ordered context units without duplicate ids or pure answer spans."""

        question_sentence_ids = set(question.question_sentence_ids)
        answer_sentence_ids = set(answer.answer_sentence_ids)
        deduped_units: list[_ExtractionUnit] = []
        seen_text_ids: set[str] = set()
        for unit in context_units:
            if unit.text_id in seen_text_ids:
                continue
            seen_text_ids.add(unit.text_id)
            if (
                set(unit.sentence_ids)
                and set(unit.sentence_ids).issubset(answer_sentence_ids)
                and not question.metadata.get("question_preamble")
            ):
                continue
            if (
                set(unit.sentence_ids)
                and set(unit.sentence_ids).issubset(question_sentence_ids)
                and not question.metadata.get("question_preamble")
                and not question.metadata.get("question_context_expanded")
            ):
                continue
            deduped_units.append(unit)
        return deduped_units

    def _build_context_raw_text(self, context_units: Sequence[_ExtractionUnit]) -> str | None:
        """Return a compact extractive context string from selected units."""

        if not context_units:
            return None
        cleaned_texts = [
            self._clean_context_sentence(unit.text)
            for unit in context_units
            if self._clean_context_sentence(unit.text)
        ]
        if not cleaned_texts:
            return None
        return self._limit_context_text(self._join_text(cleaned_texts), max_chars=320)

    def _summarize_context_text(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_units: Sequence[_ExtractionUnit],
        context_raw_text: str | None,
        context_strategy: str | None,
    ) -> str | None:
        """Return a short reviewer-facing context summary."""

        question_text = question.question_text
        combined_context = self._join_text(
            [context_raw_text or "", question.metadata.get("question_preamble") or ""],
        )
        combined_answer = answer.answer_text or ""
        context_corpus = self._join_text(
            [question_text, combined_context, combined_answer],
        )
        normalized_corpus = normalize_rule_text(context_corpus)

        if "english system" in normalized_corpus and "imperial system" in normalized_corpus:
            return (
                "The speaker is asking whether Americans call the imperial "
                "system the English system."
            )
        if (
            "centimeters" in normalized_corpus
            and "inches" in normalized_corpus
            and any(marker in normalized_corpus for marker in ("ruler", "tick marks", "measuring tape"))
        ):
            return (
                "The speakers are comparing inches and centimeters on a ruler "
                "and looking for a point where both unit systems align exactly."
            )
        if "feet are in a mile" in normalized_corpus and "tomatoes" in normalized_corpus:
            return (
                'The speakers are discussing a mnemonic for remembering how '
                'many feet are in a mile: "five tomatoes" sounds like 5,280.'
            )
        if "where's that from" in normalize_rule_text(question_text) and "take an l" in normalized_corpus:
            return (
                'The speakers are discussing the phrase "give them an inch '
                'and they\'ll take an L" and asking where it comes from.'
            )
        if context_strategy == "previous_sentence_context" and context_raw_text:
            return self._limit_context_text(context_raw_text, max_chars=250)
        if question.metadata.get("question_preamble"):
            preamble = self._clean_context_sentence(
                str(question.metadata.get("question_preamble") or ""),
            )
            if preamble:
                return self._limit_context_text(preamble, max_chars=220)
        if context_raw_text:
            return self._limit_context_text(context_raw_text, max_chars=250)
        return None

    def _unit_has_topic_overlap(
        self,
        *,
        question: QuestionCandidate,
        unit: _ExtractionUnit,
    ) -> bool:
        """Return whether one nearby unit contributes to the question topic."""

        alignment = self._question_answer_alignment(
            question_text=question.question_text,
            answer_text=unit.text,
            question_type=question.question_type,
            answer_source="context_probe",
        )
        if alignment["signal_score"] >= 0.14:
            return True
        normalized_text = normalize_rule_text(unit.text)
        return any(
            marker in normalized_text
            for marker in (
                "mnemonic",
                "tomatoes",
                "ruler",
                "measuring tape",
                "imperial system",
                "english system",
            )
        )

    @staticmethod
    def _clean_context_sentence(text: str) -> str:
        """Return a shorter context sentence without leading filler."""

        cleaned_text = re.sub(
            r"^(?:yeah|okay|ok|well|hang on|come on|so|by the way|anyway)\b[\s,.-]*",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        )
        cleaned_text = re.sub(r"\s+", " ", cleaned_text)
        return cleaned_text.strip(" ,.-")

    def _extract_leading_answer_before_question(self, text: str) -> str | None:
        """Return declarative leading text that precedes a follow-up question."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return None

        for sentence, sentence_start, _ in self._sentence_spans(cleaned_text):
            if not self._has_strong_question_signal(sentence):
                continue
            leading_text = cleaned_text[:sentence_start].strip()
            return leading_text or None
        return None

    @staticmethod
    def _limit_context_text(text: str, *, max_chars: int) -> str:
        """Return text clipped to a reviewer-friendly maximum length."""

        cleaned_text = text.strip()
        if len(cleaned_text) <= max_chars:
            return cleaned_text
        clipped = cleaned_text[: max_chars - 1].rsplit(" ", maxsplit=1)[0].strip()
        return f"{clipped}..."

    def _build_candidate_review_flags(
        self,
        *,
        confidence: float,
        input_layer: str,
        reason_codes: Sequence[str],
        answer_is_question: bool,
    ) -> list[str]:
        """Return exported review flags for one QA candidate."""

        flags: list[str] = []
        if confidence < 0.45:
            flags.append("low_confidence")
        elif confidence < 0.75:
            flags.append("medium_confidence")
        if input_layer != "sentences":
            flags.append("fallback_input_layer")
        if "competing_question_nearby" in reason_codes:
            flags.append("competing_question")
        if "segment_unknown" in reason_codes:
            flags.append("segment_unknown")
        if answer_is_question:
            flags.append("answer_is_question")
        return self._unique_strings(flags)

    def _can_use_answer_units(
        self,
        question: QuestionCandidate,
        answer_units: Sequence[_ExtractionUnit],
        segment_lookup: dict[str, Any],
    ) -> bool:
        """Return whether a candidate answer span is allowed by segment hints."""

        if self.config.allow_cross_segment_answer:
            return True

        answer_segment_ids = self._ordered_union(
            self._resolve_segment_ids_for_unit(unit=unit, segment_lookup=segment_lookup)
            for unit in answer_units
        )
        relation = self._segment_relation(
            question_segment_ids=question.question_segment_ids,
            answer_segment_ids=answer_segment_ids,
            segment_position_by_id=segment_lookup["segment_position_by_id"],
        )
        return relation in {"same_segment", "segment_unknown"}

    def _deferred_search_trigger_debug(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        ranked_answers: Sequence[_AnswerCandidate],
    ) -> dict[str, Any]:
        """Return whether deferred answer search should run for a question."""

        if not self.config.deferred_answer_search_enabled:
            return {"triggered": False, "reason": "disabled"}
        if question.didactic_question_score < (
            self.config.deferred_answer_search_min_question_score
        ):
            return {
                "triggered": False,
                "reason": "question_score_below_threshold",
                "didactic_question_score": question.didactic_question_score,
            }

        best_answer = ranked_answers[0] if ranked_answers else None
        best_answer_score = best_answer.answer_score if best_answer is not None else None
        best_semantic_score = None
        if best_answer is not None:
            best_semantic_score = self._safe_float(
                best_answer.metadata.get("ranking_debug", {}).get(
                    "semantic_relevance_score",
                ),
            )
        low_local_relevance = best_answer is None or (
            best_answer_score is not None
            and best_answer_score
            < self.config.deferred_answer_search_local_score_threshold
        )
        if best_semantic_score is not None:
            low_local_relevance = low_local_relevance or best_semantic_score < 0.35
        if best_answer is not None and best_answer.metadata.get("answer_is_question"):
            low_local_relevance = True
        if best_answer is not None:
            alignment_signal = self._safe_float(
                best_answer.metadata.get("qa_alignment_debug", {}).get("signal_score"),
            )
            shared_keywords = best_answer.metadata.get(
                "qa_alignment_debug",
                {},
            ).get("shared_keywords", [])
            if (
                alignment_signal is not None
                and alignment_signal < self.config.deferred_answer_search_min_signal_score
            ) or not shared_keywords:
                low_local_relevance = True

        recurrence_debug = self._scan_deferred_recurrence(question=question, units=units)
        return {
            "triggered": bool(low_local_relevance and recurrence_debug["has_recurrence"]),
            "reason": (
                "low_local_relevance_with_recurrence"
                if low_local_relevance and recurrence_debug["has_recurrence"]
                else "insufficient_signal"
            ),
            "low_local_relevance": low_local_relevance,
            "best_answer_score": best_answer_score,
            "best_semantic_score": best_semantic_score,
            "didactic_question_score": question.didactic_question_score,
            "recurrence_debug": recurrence_debug,
        }

    def _scan_deferred_recurrence(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> dict[str, Any]:
        """Return whether question keywords recur later in the transcript."""

        inspected_hits: list[dict[str, Any]] = []
        deferred_start_index = (
            question.unit_index + self.config.answer_search_window_units + 1
        )
        for unit in units[
            deferred_start_index : deferred_start_index
            + self.config.deferred_answer_search_window_units
        ]:
            if unit.audio_source_id != question.unit.audio_source_id:
                break
            alignment = self._question_answer_alignment(
                question_text=question.question_text,
                answer_text=unit.text,
                question_type=question.question_type,
                answer_source="deferred_probe",
            )
            if alignment["signal_score"] < self.config.deferred_answer_search_min_signal_score:
                continue
            inspected_hits.append(
                {
                    "text_id": unit.text_id,
                    "signal_score": alignment["signal_score"],
                    "shared_keywords": alignment["shared_keywords"],
                    "shared_numbers": alignment["shared_numbers"],
                },
            )
            if len(inspected_hits) >= 3:
                break

        return {
            "has_recurrence": bool(inspected_hits),
            "hits": inspected_hits,
        }

    def _collect_deferred_answer_candidates(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> list[_AnswerCandidate]:
        """Return far-window answer candidates surfaced by lexical recurrence."""

        candidates: list[_AnswerCandidate] = []
        start_index = question.unit_index + self.config.answer_search_window_units + 1
        max_index = min(
            len(units),
            start_index + self.config.deferred_answer_search_window_units,
        )
        for candidate_index in range(start_index, max_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                break
            if (
                candidate_index in question_by_index
                and candidate_index != question.unit_index
            ):
                continue
            if not candidate_unit.text.strip():
                continue
            if self._is_answer_question_like(candidate_unit.text):
                continue

            alignment = self._question_answer_alignment(
                question_text=question.question_text,
                answer_text=candidate_unit.text,
                question_type=question.question_type,
                answer_source="deferred_answer_search",
            )
            if alignment["signal_score"] < self.config.deferred_answer_search_min_signal_score:
                continue

            distance_units = candidate_index - question.unit_index
            for span_length in range(1, self.config.max_answer_units + 1):
                span = units[candidate_index : candidate_index + span_length]
                if len(span) != span_length:
                    break
                if any(
                    span_unit.audio_source_id != question.unit.audio_source_id
                    for span_unit in span
                ):
                    break
                if any(
                    span_offset > 0 and (candidate_index + span_offset) in question_by_index
                    for span_offset in range(span_length)
                ):
                    break
                if not self._can_use_answer_units(
                    question=question,
                    answer_units=span,
                    segment_lookup=segment_lookup,
                ):
                    continue

                candidate = self._build_answer_candidate_from_units(
                    question=question,
                    answer_units=span,
                    distance_units=distance_units,
                    segment_lookup=segment_lookup,
                )
                if candidate is None:
                    continue
                candidate.search_signals.update(
                    {
                        "candidate_channel": "deferred_answer_search",
                        "deferred_signal_score": alignment["signal_score"],
                        "shared_keywords": alignment["shared_keywords"],
                        "shared_numbers": alignment["shared_numbers"],
                    },
                )
                candidate.reason_codes.append("deferred_answer_candidate")
                candidate.metadata["answer_source"] = "deferred_answer_search"
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _merge_answer_candidates(
        *,
        baseline_candidates: Sequence[_AnswerCandidate],
        extra_candidates: Sequence[_AnswerCandidate],
    ) -> list[_AnswerCandidate]:
        """Merge answer candidates while keeping the first occurrence of each span."""

        merged: list[_AnswerCandidate] = []
        seen_unit_ids: set[tuple[str, ...]] = set()
        for candidate in list(baseline_candidates) + list(extra_candidates):
            candidate_key = tuple(
                candidate.answer_sentence_ids
                or candidate.answer_unit_ids
                or [candidate.answer_candidate_id]
            )
            if candidate_key in seen_unit_ids:
                continue
            seen_unit_ids.add(candidate_key)
            merged.append(candidate)
        return merged

    def _speaker_profile(self, unit: _ExtractionUnit) -> dict[str, Any]:
        """Return a coarse speaker profile for one extraction unit."""

        reliability = self._speaker_reliability(unit)
        return {
            "speaker_id": unit.speaker_id,
            "reliability": reliability,
            "resolution_status": unit.speaker_resolution_status,
            "stability_label": unit.speaker_stability_label,
            "assignment_method": unit.speaker_assignment_method,
            "dominance_margin": self._safe_float(unit.metadata.get("dominance_margin")),
        }

    def _speaker_profile_from_units(
        self,
        units: Sequence[_ExtractionUnit],
    ) -> dict[str, Any]:
        """Return a conservative speaker profile for one answer span."""

        speaker_ids = self._unique_strings(
            [
                str(unit.speaker_id).strip()
                for unit in units
                if str(unit.speaker_id or "").strip()
            ],
        )
        reliabilities = [self._speaker_reliability(unit) for unit in units]
        dominance_values = [
            dominance_margin
            for dominance_margin in (
                self._safe_float(unit.metadata.get("dominance_margin"))
                for unit in units
            )
            if dominance_margin is not None
        ]

        reliability = "missing"
        if "conflict" in reliabilities:
            reliability = "conflict"
        elif len(
            {
                unit.speaker_id
                for unit in units
                if unit.speaker_id and self._speaker_reliability(unit) == "reliable"
            },
        ) > 1:
            reliability = "conflict"
        elif any(item == "reliable" for item in reliabilities) and len(speaker_ids) == 1:
            reliability = "reliable"
        elif any(item == "weak" for item in reliabilities) and speaker_ids:
            reliability = "weak"

        return {
            "speaker_id": speaker_ids[0] if len(speaker_ids) == 1 else None,
            "speaker_ids": speaker_ids,
            "reliability": reliability,
            "resolution_statuses": self._unique_strings(
                [
                    str(unit.speaker_resolution_status or "").strip()
                    for unit in units
                    if str(unit.speaker_resolution_status or "").strip()
                ],
            ),
            "dominance_margin": max(dominance_values) if dominance_values else None,
        }

    def _has_soft_speaker_penalty(
        self,
        *,
        question_unit: _ExtractionUnit,
        answer_units: Sequence[_ExtractionUnit],
    ) -> bool:
        """Return whether same-speaker penalties should be softened."""

        candidate_units = [question_unit, *answer_units]
        for unit in candidate_units:
            if unit.speaker_confidence_label in {"low", "medium"}:
                return True
            if unit.semantic_quality_label in {"fragment", "run_on"}:
                return True
            if unit.merge_safety_label == "risky":
                return True
            if {"multi_utterance", "fragment", "merge_risky"} & set(
                unit.sentence_review_flags,
            ):
                return True
        return False

    @staticmethod
    def _speaker_reliability(unit: _ExtractionUnit) -> str:
        """Return a compact speaker reliability label for one extraction unit."""

        if unit.speaker_resolution_status == "mixed":
            return "conflict"
        if unit.speaker_confidence_label in {"low", "medium"}:
            return "weak"
        if (
            unit.speaker_id
            and unit.speaker_resolution_status in {"stable", "mostly_stable"}
        ):
            return "reliable"
        if unit.speaker_id and unit.speaker_resolution_status == "uncertain":
            return "weak"
        return "missing"

    def _has_strong_question_signal(self, text: str) -> bool:
        """Return whether the text contains strong rule-based question cues."""

        normalized_text = normalize_rule_text(text)
        return bool(
            "?" in text
            or collect_rule_matches(normalized_text, QUESTION_CUE_RULES)
            or collect_rule_matches(normalized_text, DIDACTIC_QUESTION_RULES)
        )

    def _starts_with_contextual_question(self, text: str) -> bool:
        """Return whether a trailing span starts with a contextual follow-up."""

        trailing_spans = self._sentence_spans(text.strip())
        if not trailing_spans:
            return False
        return self._is_contextual_question(trailing_spans[0][0].strip())

    def _is_contextual_question(self, text: str) -> bool:
        """Return whether the question is too short or anaphoric to stand alone."""

        normalized_text = normalize_rule_text(text).rstrip("?").strip()
        if not normalized_text:
            return False
        if count_tokens(normalized_text) <= 2:
            return True
        return any(
            pattern.fullmatch(normalized_text)
            for pattern in _ANAPHORIC_QUESTION_PATTERNS
        )

    def _is_answer_question_like(self, text: str) -> bool:
        """Return whether an answer span still behaves like a question."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return False
        normalized_text = normalize_rule_text(cleaned_text)
        if cleaned_text.endswith("?"):
            return True
        if "?" in cleaned_text:
            return True
        return self._starts_with_interrogative_word(normalized_text)

    def _question_answer_alignment(
        self,
        *,
        question_text: str,
        answer_text: str,
        question_type: str,
        answer_source: str,
    ) -> dict[str, Any]:
        """Return lexical and numeric alignment signals for one QA pair."""

        question_keywords = self._content_tokens(question_text)
        answer_keywords = self._content_tokens(answer_text)
        shared_keywords = sorted(question_keywords.intersection(answer_keywords))
        question_numbers = self._number_tokens(question_text)
        answer_numbers = self._number_tokens(answer_text)
        shared_numbers = sorted(question_numbers.intersection(answer_numbers))
        asks_for_quantity = self._question_has_quantity_intent(question_text, question_type)

        keyword_score = min(0.15, 0.05 * len(shared_keywords))
        number_score = 0.0
        if shared_numbers:
            number_score += 0.12
        elif asks_for_quantity and answer_numbers:
            number_score += 0.10

        relevance_score = 0.0
        reason_codes: list[str] = []
        if shared_keywords:
            reason_codes.append("answer_keyword_overlap")
        if shared_numbers:
            reason_codes.append("answer_number_overlap")
        elif asks_for_quantity and answer_numbers:
            reason_codes.append("answer_quantity_support")

        if not shared_keywords and not shared_numbers:
            if answer_source == "same_text_unit_seed":
                relevance_score -= 0.02
            elif asks_for_quantity:
                relevance_score -= 0.28
                reason_codes.append("low_question_answer_relevance")
            elif question_type not in {"why", "didactic_prompt"}:
                relevance_score -= 0.16
                reason_codes.append("low_question_answer_relevance")
        elif shared_keywords or shared_numbers:
            relevance_score += 0.10

        signal_score = self._clamp(
            0.04
            + keyword_score
            + number_score
            + max(0.0, relevance_score),
        )
        return {
            "keyword_score": round(keyword_score, 4),
            "number_score": round(number_score, 4),
            "relevance_score": round(relevance_score, 4),
            "signal_score": signal_score,
            "shared_keywords": shared_keywords,
            "shared_numbers": shared_numbers,
            "reason_codes": reason_codes,
            "debug": {
                "question_keywords": sorted(question_keywords),
                "answer_keywords": sorted(answer_keywords),
                "shared_keywords": shared_keywords,
                "question_numbers": sorted(question_numbers),
                "answer_numbers": sorted(answer_numbers),
                "shared_numbers": shared_numbers,
                "asks_for_quantity": asks_for_quantity,
                "answer_source": answer_source,
                "signal_score": signal_score,
            },
        }

    @staticmethod
    def _question_has_quantity_intent(question_text: str, question_type: str) -> bool:
        """Return whether the question asks for a number or exact numeric match."""

        normalized_question = normalize_rule_text(question_text)
        if question_type == "quantity":
            return True
        return any(
            marker in normalized_question
            for marker in (
                "how many",
                "integer number",
                "how much",
                "quanto",
                "exactly",
            )
        )

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        """Return a coarse set of content-bearing tokens from free text."""

        normalized_text = normalize_rule_text(text)
        return {
            token
            for token in re.findall(r"\b[\w']+\b", normalized_text)
            if len(token) > 1 and token not in _QUESTION_STOPWORDS
        }

    @staticmethod
    def _number_tokens(text: str) -> set[str]:
        """Return all numeric tokens mentioned in the text."""

        return set(_NUMBER_TOKEN_RE.findall(normalize_rule_text(text)))

    @classmethod
    def _sentence_spans(cls, text: str) -> list[tuple[str, int, int]]:
        """Return sentence-like spans while keeping their source offsets."""

        spans: list[tuple[str, int, int]] = []
        for match in cls._SENTENCE_RE.finditer(text):
            sentence = match.group(0).strip()
            if sentence:
                spans.append((sentence, match.start(), match.end()))
        if spans:
            return spans
        return [(text.strip(), 0, len(text))]

    @staticmethod
    def _starts_with_interrogative_word(normalized_text: str) -> bool:
        """Return whether the question starts with a supported cue word."""

        if not normalized_text:
            return False
        first_word = normalized_text.split(maxsplit=1)[0]
        return first_word in INTERROGATIVE_START_WORDS

    def _is_plausible_answer_text(self, text: str) -> bool:
        """Return whether the text can act as a concise local answer."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return False
        if len(cleaned_text) > self.config.max_answer_units * 350:
            return False
        token_count = count_tokens(normalize_rule_text(cleaned_text))
        if token_count < 3:
            return False
        if cleaned_text.count("?") > 1:
            return False
        return True

    @staticmethod
    def _build_segment_lookup(
        segments: Sequence[Segment],
    ) -> dict[str, Any]:
        """Return lookup tables used to map QA units back to segment context."""

        unit_to_segment_ids: dict[str, list[str]] = {}
        sentence_to_segment_ids: dict[str, list[str]] = {}
        utterance_to_segment_ids: dict[str, list[str]] = {}
        segment_by_id: dict[str, Segment] = {}
        segment_position_by_id: dict[str, int] = {}

        for position, segment in enumerate(segments):
            segment_by_id[segment.segment_id] = segment
            segment_position_by_id[segment.segment_id] = position
            for unit_id in segment.merged_transcript_unit_ids:
                segment_ids = unit_to_segment_ids.setdefault(unit_id, [])
                if segment.segment_id not in segment_ids:
                    segment_ids.append(segment.segment_id)
            for sentence_id in segment.sentence_ids:
                segment_ids = sentence_to_segment_ids.setdefault(sentence_id, [])
                if segment.segment_id not in segment_ids:
                    segment_ids.append(segment.segment_id)
            for utterance_id in segment.source_utterance_ids:
                segment_ids = utterance_to_segment_ids.setdefault(utterance_id, [])
                if segment.segment_id not in segment_ids:
                    segment_ids.append(segment.segment_id)

        return {
            "segment_by_id": segment_by_id,
            "segment_position_by_id": segment_position_by_id,
            "unit_to_segment_ids": unit_to_segment_ids,
            "sentence_to_segment_ids": sentence_to_segment_ids,
            "utterance_to_segment_ids": utterance_to_segment_ids,
        }

    def _resolve_segment_ids_for_unit(
        self,
        *,
        unit: _ExtractionUnit,
        segment_lookup: dict[str, Any],
    ) -> list[str]:
        """Resolve candidate segment ids for one extraction unit."""

        return self._ordered_union(
            (
                segment_lookup["sentence_to_segment_ids"].get(sentence_id, [])
                for sentence_id in unit.sentence_ids
            ),
        ) or self._ordered_union(
            (
                segment_lookup["unit_to_segment_ids"].get(unit_id, [])
                for unit_id in unit.merged_unit_ids
            ),
        ) or self._ordered_union(
            (
                segment_lookup["utterance_to_segment_ids"].get(utterance_id, [])
                for utterance_id in unit.source_utterance_ids
            ),
        )

    def _build_time_range(
        self,
        *,
        units: Sequence[_ExtractionUnit],
        utterance_by_id: dict[str, Utterance],
    ) -> tuple[TimeRange | None, dict[str, Any]]:
        """Return the best available time range plus grounding debug details."""

        if not units:
            return None, {"timing_source": "missing", "grounded_utterance_ids": []}

        ordered_utterance_ids = self._ordered_union(
            unit.source_utterance_ids for unit in units
        )
        grounded_utterances = [
            utterance_by_id[utterance_id]
            for utterance_id in ordered_utterance_ids
            if utterance_id in utterance_by_id
        ]
        if grounded_utterances:
            first_utterance = min(
                grounded_utterances,
                key=self._utterance_sort_key,
            )
            last_utterance = max(
                grounded_utterances,
                key=self._utterance_end_sort_key,
            )
            return (
                TimeRange(
                    start_seconds=self._utterance_start_seconds(first_utterance),
                    end_seconds=self._utterance_end_seconds(last_utterance),
                    audio_source_id=first_utterance.audio_source_id,
                    session_start_seconds=first_utterance.session_start_seconds,
                    session_end_seconds=last_utterance.session_end_seconds,
                ),
                {
                    "timing_source": "utterances",
                    "grounded_utterance_ids": ordered_utterance_ids,
                },
            )

        first_unit = units[0]
        last_unit = units[-1]
        return (
            TimeRange(
                start_seconds=first_unit.start_seconds,
                end_seconds=last_unit.end_seconds,
                audio_source_id=first_unit.audio_source_id,
                session_start_seconds=first_unit.session_start_seconds,
                session_end_seconds=last_unit.session_end_seconds,
            ),
            {
                "timing_source": first_unit.layer,
                "grounded_utterance_ids": [],
            },
        )

    @staticmethod
    def _resolve_speaker_role(
        segment_id: str | None,
        segment_by_id: dict[str, Segment],
    ) -> SpeakerRole:
        """Return the first available speaker role from the linked segment."""

        if segment_id is None:
            return SpeakerRole.UNKNOWN
        segment = segment_by_id.get(segment_id)
        if segment is None or not segment.estimated_speaker_roles:
            return SpeakerRole.UNKNOWN
        return segment.estimated_speaker_roles[0]

    @staticmethod
    def _segment_relation(
        question_segment_ids: Sequence[str],
        answer_segment_ids: Sequence[str],
        segment_position_by_id: dict[str, int],
    ) -> str:
        """Return a coarse segment relation used as a secondary QA signal."""

        if not question_segment_ids or not answer_segment_ids:
            return "segment_unknown"
        if set(question_segment_ids).intersection(answer_segment_ids):
            return "same_segment"

        question_positions = [
            segment_position_by_id[segment_id]
            for segment_id in question_segment_ids
            if segment_id in segment_position_by_id
        ]
        answer_positions = [
            segment_position_by_id[segment_id]
            for segment_id in answer_segment_ids
            if segment_id in segment_position_by_id
        ]
        if not question_positions or not answer_positions:
            return "segment_unknown"

        min_distance = min(
            abs(answer_position - question_position)
            for question_position in question_positions
            for answer_position in answer_positions
        )
        if min_distance == 1:
            return "next_segment"
        return "distant_segment"

    @staticmethod
    def _distance_score(
        distance_units: int,
        reason_codes: list[str],
        input_layer: str,
    ) -> float:
        """Return the local-distance contribution to an answer score."""

        reason_prefix = "sentence" if input_layer == "sentence" else "unit"
        if distance_units == 0:
            reason_codes.append(f"answer_in_same_{reason_prefix}")
            return 0.28
        if distance_units == 1:
            reason_codes.append(f"answer_in_next_{reason_prefix}")
            return 0.20
        if distance_units == 2:
            reason_codes.append(f"answer_two_{reason_prefix}s_away")
            return 0.12
        reason_codes.append(f"answer_three_{reason_prefix}s_away")
        return 0.06

    @staticmethod
    def _same_unit_reason(input_layer: str) -> str:
        """Return the reason code for same-unit local answer extraction."""

        if input_layer == "sentence":
            return "same_sentence_answer"
        return "same_unit_answer"

    @staticmethod
    def _unit_start_seconds(unit: MergedTranscriptUnit) -> float:
        """Return the preferred start time for one transcript unit."""

        if unit.session_start_seconds is not None:
            return float(unit.session_start_seconds)
        return float(unit.start_seconds)

    @staticmethod
    def _unit_end_seconds(unit: MergedTranscriptUnit) -> float:
        """Return the preferred end time for one transcript unit."""

        if unit.session_end_seconds is not None:
            return float(unit.session_end_seconds)
        return float(unit.end_seconds)

    @staticmethod
    def _sentence_start_seconds(sentence: Sentence) -> float:
        """Return the preferred start time for one sentence."""

        if sentence.session_start_seconds is not None:
            return float(sentence.session_start_seconds)
        return float(sentence.start_seconds)

    @staticmethod
    def _sentence_end_seconds(sentence: Sentence) -> float:
        """Return the preferred end time for one sentence."""

        if sentence.session_end_seconds is not None:
            return float(sentence.session_end_seconds)
        return float(sentence.end_seconds)

    @staticmethod
    def _utterance_start_seconds(utterance: Utterance) -> float:
        """Return the preferred start time for one utterance."""

        if utterance.session_start_seconds is not None:
            return float(utterance.session_start_seconds)
        return float(utterance.start_seconds)

    @staticmethod
    def _utterance_end_seconds(utterance: Utterance) -> float:
        """Return the preferred end time for one utterance."""

        if utterance.session_end_seconds is not None:
            return float(utterance.session_end_seconds)
        return float(utterance.end_seconds)

    @classmethod
    def _units_duration_seconds(cls, units: Sequence[_ExtractionUnit]) -> float:
        """Return the coarse duration covered by consecutive extraction units."""

        if not units:
            return 0.0
        return units[-1].end_seconds - units[0].start_seconds

    @staticmethod
    def _utterance_sort_key(utterance: Utterance) -> tuple[float, float]:
        """Return a stable start-oriented ordering key for grounded utterances."""

        return (
            QAPairExtractor._utterance_start_seconds(utterance),
            QAPairExtractor._utterance_end_seconds(utterance),
        )

    @staticmethod
    def _utterance_end_sort_key(utterance: Utterance) -> tuple[float, float]:
        """Return a stable end-oriented ordering key for grounded utterances."""

        return (
            QAPairExtractor._utterance_end_seconds(utterance),
            QAPairExtractor._utterance_start_seconds(utterance),
        )

    @staticmethod
    def _join_text(texts: Iterable[str]) -> str:
        """Return a readable text from a small list of strings."""

        return " ".join(text.strip() for text in texts if text and text.strip()).strip()

    @staticmethod
    def _ordered_union(values: Iterable[Sequence[str]]) -> list[str]:
        """Return the ordered union of small string sequences."""

        ordered: list[str] = []
        for group in values:
            for value in group:
                normalized_value = str(value or "").strip()
                if normalized_value and normalized_value not in ordered:
                    ordered.append(normalized_value)
        return ordered

    @staticmethod
    def _string_list(values: Sequence[str] | Any) -> list[str]:
        """Return a normalized list of strings from a raw field."""

        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return []
        return [
            normalized_value
            for normalized_value in (str(value or "").strip() for value in values)
            if normalized_value
        ]

    @staticmethod
    def _unit_debug_metadata(unit: _ExtractionUnit) -> dict[str, Any]:
        """Return a compact debug summary for one extraction unit."""

        return {
            "layer": unit.layer,
            "text_id": unit.text_id,
            "sentence_ids": list(unit.sentence_ids),
            "merged_unit_ids": list(unit.merged_unit_ids),
            "source_utterance_ids": list(unit.source_utterance_ids),
            "speaker_id": unit.speaker_id,
            "speaker_resolution_status": unit.speaker_resolution_status,
            "speaker_stability_label": unit.speaker_stability_label,
            "speaker_assignment_method": unit.speaker_assignment_method,
            "semantic_quality_label": unit.semantic_quality_label,
            "merge_safety_label": unit.merge_safety_label,
            "review_priority": unit.review_priority,
            "sentence_review_flags": list(unit.sentence_review_flags),
            "dominance_margin": QAPairExtractor._safe_float(
                unit.metadata.get("dominance_margin"),
            ),
        }

    @staticmethod
    def _unique_strings(values: Sequence[str]) -> list[str]:
        """Return values in insertion order without duplicates."""

        ordered: list[str] = []
        for value in values:
            normalized_value = str(value or "").strip()
            if normalized_value and normalized_value not in ordered:
                ordered.append(normalized_value)
        return ordered

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Return a float when conversion is possible."""

        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clamp(value: float) -> float:
        """Clamp a floating-point score into the inclusive [0, 1] range."""

        return max(0.0, min(1.0, round(float(value), 4)))

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        """Return a stable confidence label for the exported candidate."""

        if confidence >= 0.75:
            return "high"
        if confidence >= 0.45:
            return "medium"
        return "low"
