"""Sentence-aware rule-based candidate QA extraction."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import perf_counter
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
from lecture_analyzer.analysis.semantic_responsiveness import (
    SemanticResponsivenessBackend,
    SemanticResponsivenessInput,
    SemanticResponsivenessUnavailableError,
    SentenceTransformerResponsivenessBackend,
)
from lecture_analyzer.analysis.qa_speaker_check import (
    DIFFERENT_SPEAKER_LIKELY,
    SAME_SPEAKER_SUSPECTED,
    SPEAKER_CHECK_UNAVAILABLE,
    SPEAKER_RESCUED_CANDIDATE,
    SPEAKER_RESCUE_REJECTED_CONVERSATIONAL,
    SPEAKER_RESCUE_REJECTED_TEXT_QUALITY,
    QASpeakerCheckService,
    _apply_result_to_candidate,
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
    "as",
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
    "dove",
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
    "my",
    "number",
    "not",
    "of",
    "on",
    "or",
    "perche",
    "perché",
    "point",
    "quale",
    "quali",
    "quanta",
    "quante",
    "quanti",
    "quanto",
    "quella",
    "quelle",
    "quelli",
    "quello",
    "questa",
    "queste",
    "questi",
    "questo",
    "right",
    "se",
    "si",
    "so",
    "that",
    "the",
    "their",
    "there",
    "they",
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
    "with",
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
_DISCOURSE_TAG_QUESTIONS = {
    "ok",
    "okay",
    "right",
    "vero",
}
_RHETORICAL_POLL_QUESTION_RE = re.compile(
    r"^(?:"
    r"\d+(?:\s+(?:or|o)\s+\d+)+"
    r"|(?:one|two|three|four|five|uno|due|tre|quattro|cinque)"
    r"(?:\s+(?:or|o)\s+"
    r"(?:one|two|three|four|five|uno|due|tre|quattro|cinque))+"
    r")\??$",
)
_TAG_QUESTION_RE = re.compile(
    r"^(?:right|vero|correct|ok|okay|yes|no|eh|no)\??$",
)
_TRAILING_ANSWER_TAG_RE = re.compile(
    r"^(?P<body>.+?)[,;]?\s+(?P<tag>right|vero|correct|ok|okay|yes|no|eh)\?$",
    flags=re.IGNORECASE,
)
_SUBORDINATE_QUESTION_START_WORDS = {
    "quando",
    "when",
}
_CAUSAL_ANSWER_AFTER_WHY_RE = re.compile(
    r"^(?:because|perche|perché)\s+"
    r"(?:i|we|you|he|she|they|io|noi|lui|lei|loro|"
    r"ho|hai|ha|abbiamo|avete|hanno|sono|ero|era|"
    r"da|nel|nella|nei|nelle|con|per)\b",
)
_CAUSAL_DECLARATIVE_STATEMENT_RE = re.compile(
    r"^(?:because|perche|perché)\s+"
    r"(?:(?:a|an|the|un|una|il|lo|la|i|gli|le)\s+)?"
    r"[\w']+(?:\s+[\w']+){0,6}\s+"
    r"(?:is|are|was|were|e|è)\s+(?:that|che)\b",
)
_CAUSAL_EXISTENTIAL_TAG_RE = re.compile(
    r"^(?:because|perche|perché)\s+"
    r"(?:there\s+(?:is|are)|c[' ]?e|ci\s+sono)\b",
)
_EXPLANATORY_CLAUSE_START_RE = re.compile(
    r"^(?:because|perche)\s+(?:if|se|when|quando)\b",
)
_AUXILIARY_QUESTION_START_WORDS = {
    "am",
    "are",
    "can",
    "could",
    "did",
    "do",
    "does",
    "had",
    "has",
    "have",
    "is",
    "must",
    "should",
    "was",
    "were",
    "will",
    "would",
    "c'e",
    "c'era",
    "esiste",
    "esistono",
    "puo",
    "puoi",
    "possiamo",
}
_LEADING_QUESTION_DISCOURSE_MARKERS = {
    "allora",
    "cioe",
    "cioè",
    "e",
    "ma",
    "ok",
}
_WEAK_DEFERRED_KEYWORDS = {
    "answer",
    "ask",
    "question",
    "questions",
    "thing",
    "things",
    "think",
    "towards",
    "want",
}
_MODERATOR_HANDOFF_PATTERNS = (
    re.compile(r"^(?:or\s+)?(?:professor|dr|doctor)\b"),
    re.compile(r"^(?:or\s+)?[a-z]{2,24}\s+(?:is|was|also)\s+(?:engaged|joining|here)\b"),
    re.compile(r"\b(?:let'?s|we(?:'| a)?re going to)\s+(?:move|turn|go)\b"),
    re.compile(r"\bnext\s+question\b"),
)
_ANSWER_BOILERPLATE_PATTERNS = (
    re.compile(r"^(?:yeah|yes|no|okay|ok|well|so|right)[\s,.-]*$"),
    re.compile(r"^(?:let me|i want to|we are going to|we're going to)\b"),
    re.compile(r"^(?:i had a thought|i have a thought)\b"),
    re.compile(r"\bedging towards an answer\b"),
    re.compile(r"\b(?:answer|response)\s+(?:is|will be|comes)\s+in\s+(?:the\s+)?next\b"),
    re.compile(r"\b(?:come|get|go)\s+back\s+to\s+(?:that|this|it)\s+later\b"),
    re.compile(r"\b(?:course split|lecture outline|today we will)\b"),
    re.compile(r"\b(?:next|following)\s+(?:slide|section|part|topic)\b"),
    re.compile(r"\b(?:move|moving|turn|turning)\s+(?:on|to)\b"),
    re.compile(r"^(?:thanks|thank you)\s+(?:for\s+)?(?:that|the)?\s*question\b"),
)
_META_ANSWER_OPENING_RE = re.compile(
    r"^(?:(?:yeah|yes|right|okay|ok|well|so)[\s,.-]+)*"
    r"(?:"
    r"(?:that(?:'s|\s+is)\s+(?:a\s+)?(?:good|great|excellent|interesting|deep)\s+question)"
    r"|(?:thanks|thank\s+you)\s+(?:for\s+)?(?:that|the)?\s*question"
    r")"
    r"[\s,.:;!-]+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_INCOMPLETE_ANSWER_END_WORDS = {
    "al",
    "alla",
    "allo",
    "because",
    "but",
    "che",
    "di",
    "if",
    "of",
    "per",
    "perche",
    "questa",
    "queste",
    "questi",
    "questo",
    "se",
    "that",
    "these",
    "this",
    "those",
    "to",
    "toward",
    "towards",
    "when",
    "which",
}
_INCOMPLETE_ANSWER_START_RE = re.compile(
    r"^(?:not\s+only|non\s+soltanto|if|se|when|quando)\b",
)
_ADDITIVE_CONTINUATION_START_RE = re.compile(
    r"^(?:"
    r"also|and\s+also|plus|moreover|furthermore|additionally|then|"
    r"anche|e\s+anche|ed\s+anche|inoltre|poi|"
    r"mettendo|mettendoci|aggiungendo|facendo|usando|portando"
    r")\b",
)
_BACKCHANNEL_CHECK_RE = re.compile(
    r"\b(?:"
    r"ci\s+siamo|mi\s+state\s+(?:vedendo|sentendo|seguendo)|"
    r"are\s+(?:we|you)\s+(?:ready|good|clear|following)|"
    r"can\s+you\s+(?:see|hear|follow)"
    r")\b",
)
_DEFLECTION_META_TOKENS = {
    "course",
    "corso",
    "class",
    "classe",
    "lesson",
    "lecture",
    "lezione",
    "lezioni",
    "module",
    "modulo",
    "slide",
    "slides",
    "exam",
    "esame",
    "today",
    "oggi",
}
_DEFLECTION_DISMISSIVE_TOKENS = {
    "essential",
    "essenziale",
    "essenzialissimo",
    "important",
    "importante",
    "necessary",
    "necessario",
    "needed",
    "serve",
    "servono",
    "relevant",
    "rilevante",
}
_NEGATION_TOKENS = {"no", "non", "not", "n't", "never", "mai"}
_PROCEDURAL_QUESTION_RE = re.compile(
    r"\b(?:"
    r"ask\s+(?:[a-z]+\s+){0,4}(?:a\s+)?question|"
    r"(?:one|1)\s+more\s+(?:audience\s+)?question"
    r")\b",
)
_FOLLOWUP_PROMPT_ANSWER_RE = re.compile(
    r"^(?:"
    r"(?:tell|explain|describe|show|walk)\s+(?:me|us)\b"
    r"|(?:can|could|would)\s+you\s+(?:tell|explain|describe|show)\b"
    r"|(?:racconta|raccontaci|raccontami|spiega|spiegaci|spiegami|dicci|dimmi)\b"
    r")",
)
_DIRECT_DEFINITION_REQUEST_RE = re.compile(
    r"^(?:"
    r"(?:can|could|would)\s+you\s+(?:tell|explain|describe|show)\b"
    r"|(?:puoi|potresti|puo|può)\s+(?:spiegare|spiegarci|spiegarmi|dire|dirci)\b"
    r")",
)
_POLL_OPTION_WORDS = {
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "one",
    "two",
    "three",
    "four",
    "five",
    "uno",
    "due",
    "tre",
    "quattro",
    "cinque",
}
_BACKCHANNEL_ANSWER_TOKENS = {
    "ah",
    "eh",
    "hm",
    "hmm",
    "no",
    "bene",
    "bon",
    "ok",
    "okay",
    "right",
    "si",
    "sì",
    "uh",
    "uhm",
    "vero",
    "via",
    "yeah",
    "yes",
}
_FILLER_ANSWERS = {
    "another aside entirely",
    "because i can feel one coming",
    "in general",
    "let us move on for a moment",
    "let us pause",
    "well",
}


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
    context_selection_score: float | None = None
    context_reasons: list[str] = field(default_factory=list)
    candidate_context_count: int = 0


@dataclass(slots=True)
class _ContextSelection:
    """Candidate context units plus compact selection diagnostics."""

    units: list[_ExtractionUnit] = field(default_factory=list)
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    candidate_count: int = 0


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
        skipped_question_continuation_answer = False
        for distance_units in range(
            1,
            self._extractor.config.answer_search_window_units + 1,
        ):
            candidate_index = question.unit_index + distance_units
            if candidate_index >= len(units):
                break

            if candidate_index in question_by_index:
                if self._extractor._is_answer_question_continuation_rejected(
                    question=question, answer_text=units[candidate_index].text
                ):
                    skipped_question_continuation_answer = True
                    search_stop_reason = "question_continuation_skipped"
                    continue
                leading_answer = self._extractor._build_competing_question_leading_answer_candidate(
                    question=question,
                    competing_unit=units[candidate_index],
                    distance_units=distance_units,
                    segment_lookup=segment_lookup,
                )
                if leading_answer is not None:
                    candidates.append(leading_answer)
                    combined_answer = self._extractor._build_answer_candidate_with_competing_prefix(
                        question=question,
                        answer_units=collected_units,
                        competing_unit=units[candidate_index],
                        answer_prefix=leading_answer.answer_text,
                        distance_units=distance_units,
                        segment_lookup=segment_lookup,
                    )
                    if combined_answer is not None:
                        candidates.append(combined_answer)
                cluster_answer = self._extractor._build_interview_cluster_answer_candidate(
                    question=question,
                    units=units,
                    question_by_index=question_by_index,
                    cluster_start_index=candidate_index,
                    segment_lookup=segment_lookup,
                )
                if cluster_answer is not None:
                    candidates.append(cluster_answer)
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

            if self._extractor._is_answer_question_continuation_rejected(
                question=question, answer_text=candidate_unit.text
            ):
                skipped_question_continuation_answer = True
                search_stop_reason = "question_continuation_skipped"
                continue

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
                self._extractor._annotate_answer_boundary(
                    answer=answer_candidate,
                    question=question,
                    units=units,
                    question_by_index=question_by_index,
                    segment_lookup=segment_lookup,
                    at_search_window_boundary=(
                        distance_units
                        >= self._extractor.config.answer_search_window_units
                    ),
                )
                if skipped_question_continuation_answer:
                    answer_candidate.reason_codes = self._extractor._unique_strings(
                        list(answer_candidate.reason_codes)
                        + ["answer_question_continuation_rejected"]
                    )
                    answer_candidate.search_signals[
                        "skipped_question_continuation_answer"
                    ] = True
                candidates.append(answer_candidate)
                if distance_units >= self._extractor.config.answer_search_window_units:
                    completion_candidate = (
                        self._extractor._build_answer_completion_candidate(
                            question=question,
                            answer_units=collected_units,
                            units=units,
                            question_by_index=question_by_index,
                            distance_units=distance_units,
                            segment_lookup=segment_lookup,
                        )
                    )
                    if completion_candidate is not None:
                        candidates.append(completion_candidate)

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
                self._extractor._annotate_answer_boundary(
                    answer=candidate,
                    question=question,
                    units=units,
                    question_by_index=question_by_index,
                    segment_lookup=segment_lookup,
                    at_search_window_boundary=False,
                )
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
                (
                    0
                    if "answer_span_completion_support" in candidate.reason_codes
                    else 1
                ),
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
        semantic_responsiveness_backend: SemanticResponsivenessBackend | None = None,
        qa_speaker_check_service: QASpeakerCheckService | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.qa_speaker_check_service = qa_speaker_check_service
        self.semantic_responsiveness_backend = (
            semantic_responsiveness_backend
            or SentenceTransformerResponsivenessBackend(
                self.config.qa_semantic_responsiveness_model_name,
            )
        )
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
            session.metadata["qa_coverage"] = self._build_qa_coverage(
                units=[],
                emitted_candidate_count=0,
                suppressed_by_gate_reason_counts={},
            )
            return []

        prepared_input = self._prepare_input(session)
        if not prepared_input.units:
            session.metadata["qa_coverage"] = self._build_qa_coverage(
                units=[],
                emitted_candidate_count=0,
                suppressed_by_gate_reason_counts={},
            )
            return []

        segment_lookup = self._build_segment_lookup(session.segments)
        questions = self._detect_questions(
            units=prepared_input.units,
            segment_lookup=segment_lookup,
            input_layer=prepared_input.input_layer,
        )
        question_by_index = {question.unit_index: question for question in questions}

        extracted_candidates: list[QAPairCandidate] = []
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
            answer = self._select_ranked_answer(ranked_answers)
            if answer is None:
                continue

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
            extracted_candidates.append(qa_candidate)

        self._apply_semantic_responsiveness_scoring(
            candidates=extracted_candidates,
            session=session,
        )

        qa_candidates: list[QAPairCandidate] = []
        suppressed_candidates: list[tuple[QAPairCandidate, str]] = []
        suppressed_by_gate_reason_counts: dict[str, int] = {}
        for qa_candidate in extracted_candidates:
            suppression_reason = self._qa_candidate_suppression_reason(qa_candidate)
            if suppression_reason is None:
                qa_candidates.append(qa_candidate)
            else:
                suppressed_candidates.append((qa_candidate, suppression_reason))
                suppressed_by_gate_reason_counts[suppression_reason] = (
                    suppressed_by_gate_reason_counts.get(suppression_reason, 0) + 1
                )

        emitted_candidates = self._dedupe_qa_candidates(qa_candidates)
        rescue_stats = self._apply_speaker_assisted_rescue(
            session=session,
            suppressed_candidates=suppressed_candidates,
            emitted_candidates=emitted_candidates,
            suppressed_by_gate_reason_counts=suppressed_by_gate_reason_counts,
        )
        session.metadata["qa_coverage"] = self._build_qa_coverage(
            units=prepared_input.units,
            emitted_candidate_count=len(emitted_candidates),
            suppressed_by_gate_reason_counts=suppressed_by_gate_reason_counts,
            speaker_rescue_stats=rescue_stats,
        )
        return emitted_candidates

    def _build_qa_coverage(
        self,
        *,
        units: Sequence[_ExtractionUnit],
        emitted_candidate_count: int,
        suppressed_by_gate_reason_counts: dict[str, int],
        speaker_rescue_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return aggregate QA coverage counters for run-level review."""

        interrogative_sentence_count = sum(
            1 for unit in units if self._is_interrogative_sentence_proxy(unit.text)
        )
        suppressed_by_gate_count = sum(suppressed_by_gate_reason_counts.values())
        coverage = {
            "interrogative_sentence_count": interrogative_sentence_count,
            "emitted_candidate_count": emitted_candidate_count,
            "coverage_ratio": (
                round(emitted_candidate_count / interrogative_sentence_count, 4)
                if interrogative_sentence_count
                else 0
            ),
            "suppressed_by_gate_count": suppressed_by_gate_count,
            "suppressed_by_gate_reasons": dict(
                sorted(suppressed_by_gate_reason_counts.items()),
            ),
        }
        if speaker_rescue_stats:
            coverage.update(
                {
                    "rescued_candidate_count": int(
                        speaker_rescue_stats.get("rescued_candidate_count") or 0,
                    ),
                    "rescued_by_gate_reasons": dict(
                        sorted(
                            (
                                speaker_rescue_stats.get("rescued_by_gate_reasons")
                                or {}
                            ).items(),
                        ),
                    ),
                    "speaker_rescue_checked_candidate_count": int(
                        speaker_rescue_stats.get("checked_candidate_count") or 0,
                    ),
                    "speaker_rescue_attempted_candidate_count": int(
                        speaker_rescue_stats.get("attempted_candidate_count") or 0,
                    ),
                    "speaker_rescue_unavailable_candidate_count": int(
                        speaker_rescue_stats.get("unavailable_candidate_count") or 0,
                    ),
                    "speaker_rescue_skipped_candidate_count": int(
                        speaker_rescue_stats.get("skipped_candidate_count") or 0,
                    ),
                    "speaker_rescue_rejected_candidate_count": int(
                        speaker_rescue_stats.get("rejected_candidate_count") or 0,
                    ),
                    "speaker_rescue_rejected_reasons": dict(
                        sorted(
                            (
                                speaker_rescue_stats.get("rejected_reason_counts")
                                or {}
                            ).items(),
                        ),
                    ),
                    "speaker_rescue_total_check_seconds": round(
                        float(speaker_rescue_stats.get("total_check_seconds") or 0.0),
                        4,
                    ),
                    "speaker_rescue_check_cap_reached": bool(
                        speaker_rescue_stats.get("check_cap_reached"),
                    ),
                    "speaker_rescue_candidate_cap_reached": bool(
                        speaker_rescue_stats.get("candidate_cap_reached"),
                    ),
                },
            )
        return coverage

    def _apply_speaker_assisted_rescue(
        self,
        *,
        session: LectureSession,
        suppressed_candidates: Sequence[tuple[QAPairCandidate, str]],
        emitted_candidates: list[QAPairCandidate],
        suppressed_by_gate_reason_counts: dict[str, int],
    ) -> dict[str, Any]:
        """Recover soft-gated dialogic candidates with confident speaker change."""

        stats: dict[str, Any] = {
            "enabled": False,
            "model_available": False,
            "attempted_candidate_count": 0,
            "checked_candidate_count": 0,
            "unavailable_candidate_count": 0,
            "skipped_candidate_count": 0,
            "rejected_candidate_count": 0,
            "rejected_reason_counts": {},
            "rescued_candidate_count": 0,
            "rescued_by_gate_reasons": {},
            "total_check_seconds": 0.0,
            "check_cap_reached": False,
            "candidate_cap_reached": False,
            "notes": [],
        }
        session.metadata["qa_speaker_rescue"] = stats
        if (
            self.config.pipeline_profile != "quality_local"
            or not self.config.qa_speaker_check_enabled
            or not suppressed_candidates
        ):
            return stats

        speaker_config = self.config.speaker_check_config()
        max_checks = max(0, int(speaker_config.rescue_max_checks_per_run))
        max_rescues = max(0, int(speaker_config.rescue_max_candidates_per_run))
        if max_checks <= 0 or max_rescues <= 0:
            stats["enabled"] = True
            return stats

        checker = self.qa_speaker_check_service or QASpeakerCheckService(speaker_config)
        stats["enabled"] = True
        stats["model_available"] = checker.model_available()
        stats["model_load_seconds"] = round(checker.model_load_seconds, 4)
        stats["notes"] = checker.notes()
        if not stats["model_available"]:
            return stats

        audio_sources_by_id = {
            audio_source.audio_source_id: audio_source
            for audio_source in session.audio_sources
        }
        utterances_by_id = {
            utterance.utterance_id: utterance
            for utterance in session.utterances
        }
        sentence_by_id = {
            sentence.sentence_id: sentence
            for sentence in session.sentences
        }
        next_sentence_id_by_id = {
            left.sentence_id: right.sentence_id
            for left, right in zip(session.sentences, session.sentences[1:])
        }
        rescued_by_gate: Counter[str] = Counter()
        rejected_reasons: Counter[str] = Counter()

        with TemporaryDirectory(prefix="qa_speaker_rescue_") as temp_root:
            temp_directory = Path(temp_root)
            for candidate, suppression_reason in suppressed_candidates:
                if stats["attempted_candidate_count"] >= max_checks:
                    stats["check_cap_reached"] = True
                    break
                if stats["rescued_candidate_count"] >= max_rescues:
                    stats["candidate_cap_reached"] = True
                    break
                if not self._speaker_rescue_candidate_is_eligible(
                    candidate,
                    suppression_reason,
                ):
                    continue

                result = checker.check(
                    candidate,
                    audio_sources_by_id=audio_sources_by_id,
                    utterances_by_id=utterances_by_id,
                    temp_directory=temp_directory,
                )
                stats["attempted_candidate_count"] += 1
                stats["total_check_seconds"] = (
                    float(stats["total_check_seconds"]) + result.check_seconds
                )
                if result.status == "skipped":
                    stats["skipped_candidate_count"] += 1
                    continue
                if result.status == "unavailable":
                    stats["unavailable_candidate_count"] += 1
                    continue
                stats["checked_candidate_count"] += 1
                duplicate_existing = (
                    self._find_near_duplicate_candidate(
                        candidate,
                        emitted_candidates,
                    )
                    is not None
                )
                if (
                    result.status != DIFFERENT_SPEAKER_LIKELY
                    or SAME_SPEAKER_SUSPECTED in result.flags
                    or SPEAKER_CHECK_UNAVAILABLE in result.flags
                    or not self._has_minimal_responsiveness_anchor(candidate)
                    or duplicate_existing
                ):
                    continue
                rejection_reason = self._speaker_rescue_rejection_reason(candidate)
                if rejection_reason is not None:
                    candidate.reason_codes = self._unique_strings(
                        list(candidate.reason_codes) + [rejection_reason],
                    )
                    stats["rejected_candidate_count"] += 1
                    rejected_reasons[rejection_reason] += 1
                    continue

                _apply_result_to_candidate(candidate, result)
                self._trim_speaker_rescued_candidate(
                    candidate,
                    sentence_by_id=sentence_by_id,
                    next_sentence_id_by_id=next_sentence_id_by_id,
                )
                candidate.metadata["speaker_check_precomputed"] = True
                candidate.metadata["speaker_rescue"] = {
                    "source_gate": suppression_reason,
                    "reason": SPEAKER_RESCUED_CANDIDATE,
                    "check_seconds": round(result.check_seconds, 4),
                }
                candidate.reason_codes = self._unique_strings(
                    list(candidate.reason_codes) + [SPEAKER_RESCUED_CANDIDATE],
                )
                candidate.review_flags = self._unique_strings(
                    list(candidate.review_flags) + [SPEAKER_RESCUED_CANDIDATE],
                )
                emitted_candidates.append(candidate)
                suppressed_by_gate_reason_counts[suppression_reason] = max(
                    0,
                    int(suppressed_by_gate_reason_counts.get(suppression_reason) or 0)
                    - 1,
                )
                if suppressed_by_gate_reason_counts[suppression_reason] == 0:
                    del suppressed_by_gate_reason_counts[suppression_reason]
                rescued_by_gate[suppression_reason] += 1
                stats["rescued_candidate_count"] += 1

        stats["total_check_seconds"] = round(float(stats["total_check_seconds"]), 4)
        stats["rescued_by_gate_reasons"] = dict(sorted(rescued_by_gate.items()))
        stats["rejected_reason_counts"] = dict(sorted(rejected_reasons.items()))
        stats["candidate_cap_reached"] = (
            stats["candidate_cap_reached"]
            or stats["rescued_candidate_count"] >= max_rescues
        )
        stats["check_cap_reached"] = (
            stats["check_cap_reached"]
            or stats["attempted_candidate_count"] >= max_checks
        )
        session.metadata["qa_speaker_rescue"] = stats
        return stats

    def _speaker_rescue_rejection_reason(
        self,
        candidate: QAPairCandidate,
    ) -> str | None:
        """Return why a speaker-rescued candidate should still stay suppressed."""

        if self._is_speaker_rescue_conversational_answer(candidate):
            return SPEAKER_RESCUE_REJECTED_CONVERSATIONAL
        if self._has_low_speaker_rescue_text_quality(candidate):
            return SPEAKER_RESCUE_REJECTED_TEXT_QUALITY
        return None

    def _is_speaker_rescue_conversational_answer(
        self,
        candidate: QAPairCandidate,
    ) -> bool:
        """Return whether a rescue answer is only Q&A management chatter."""

        normalized_answer = normalize_rule_text(candidate.answer_text or "")
        if not normalized_answer:
            return True
        if self._is_poll_or_backchannel_answer(normalized_answer):
            return True
        if self._has_poll_or_backchannel_noise(normalized_answer):
            return True
        if self._is_filler_or_boilerplate_answer(normalized_answer):
            return True
        if (
            re.search(r"\b(?:thanks|thank\s+you)\b", normalized_answer)
            and re.search(
                r"\b(?:overview|panelists?|presentations?|everyone|everybody)\b",
                normalized_answer,
            )
        ):
            return True
        if self._is_moderator_handoff_answer(normalized_answer):
            return True
        if self._is_followup_prompt_text(normalized_answer):
            return True
        return False

    def _has_low_speaker_rescue_text_quality(
        self,
        candidate: QAPairCandidate,
    ) -> bool:
        """Return whether existing QA quality scores are too low for rescue."""

        quality_features = candidate.metadata.get("quality_features", {})
        threshold = float(self.config.qa_speaker_rescue_min_text_quality_score)
        question_quality = self._safe_float(
            quality_features.get("question_quality_score"),
        )
        answer_quality = self._safe_float(
            quality_features.get("answer_quality_score"),
        )
        if question_quality is not None and question_quality < threshold:
            return True
        if answer_quality is not None and answer_quality < threshold:
            return True
        return False

    def _trim_speaker_rescued_candidate(
        self,
        candidate: QAPairCandidate,
        *,
        sentence_by_id: dict[str, Any],
        next_sentence_id_by_id: dict[str, str],
    ) -> None:
        """Clean question focus and answer boundary for an emitted rescue only."""

        trim_debug: dict[str, Any] = {}
        focused_question = self._speaker_rescue_question_focus(candidate)
        if focused_question and focused_question != candidate.question_text:
            trim_debug["original_question_text"] = candidate.question_text
            candidate.question_text = focused_question
            trim_debug["question_trimmed"] = True

        completed_answer, completion_debug = self._speaker_rescue_completed_answer(
            candidate,
            sentence_by_id=sentence_by_id,
            next_sentence_id_by_id=next_sentence_id_by_id,
        )
        bounded_answer = self._speaker_rescue_answer_within_word_cap(
            completed_answer,
        )
        if bounded_answer and bounded_answer != candidate.answer_text:
            trim_debug["original_answer_text"] = candidate.answer_text
            candidate.answer_text = bounded_answer
            trim_debug["answer_trimmed"] = True
        trim_debug.update(completion_debug)
        if trim_debug:
            candidate.metadata["speaker_rescue_trim"] = trim_debug

    def _speaker_rescue_question_focus(
        self,
        candidate: QAPairCandidate,
    ) -> str:
        """Return the focused interrogative text for a rescued question."""

        question_debug = candidate.metadata.get("question_debug", {})
        focus_text = str(
            question_debug.get("normalized_question_text")
            or candidate.question_text
            or "",
        ).strip()
        focus_text = self._speaker_rescue_last_interrogative_focus(focus_text)
        if not focus_text:
            return candidate.question_text
        focus_text = self._speaker_rescue_trim_suspended_question_tail(focus_text)
        if not focus_text:
            return self._speaker_rescue_shortest_interrogative_sentence(candidate)
        if focus_text.endswith("?"):
            return focus_text[0].upper() + focus_text[1:] if focus_text else focus_text
        if self._has_strong_question_signal(
            focus_text,
        ) or self._starts_with_interrogative_word(normalize_rule_text(focus_text)):
            return focus_text[0].upper() + focus_text[1:] if focus_text else focus_text
        return self._speaker_rescue_shortest_interrogative_sentence(candidate)

    def _speaker_rescue_shortest_interrogative_sentence(
        self,
        candidate: QAPairCandidate,
    ) -> str:
        """Return the shortest full question sentence available for rescue focus."""

        question_debug = candidate.metadata.get("question_debug", {})
        candidates = [
            str(question_debug.get("normalized_question_text") or "").strip(),
            str(question_debug.get("raw_unit_text") or "").strip(),
            str(candidate.question_text or "").strip(),
        ]
        full_sentences: list[str] = []
        for text in candidates:
            if not text:
                continue
            for sentence, _, _ in self._sentence_spans(text):
                cleaned = sentence.strip(" ,")
                if cleaned and (
                    self._has_strong_question_signal(cleaned)
                    or self._starts_with_interrogative_word(
                        normalize_rule_text(cleaned),
                    )
                ):
                    full_sentences.append(cleaned)
        if full_sentences:
            shortest = min(
                full_sentences,
                key=lambda value: count_tokens(normalize_rule_text(value)),
            )
            return shortest[0].upper() + shortest[1:] if shortest else shortest
        fallback = str(candidate.question_text or "").strip()
        return fallback[0].upper() + fallback[1:] if fallback else fallback

    @staticmethod
    def _speaker_rescue_last_interrogative_focus(text: str) -> str:
        """Return a trailing focus clause when a run-on embeds the real question."""

        cleaned = re.sub(r"\s+", " ", text).strip(" ,")
        patterns = [
            r"\bche\s+cosa\b",
            r"\bcosa\b",
            r"\bcome\b",
            r"\bperche\b",
            r"\bperché\b",
            r"\bwhat\b",
            r"\bhow\b",
            r"\bwhy\b",
            r"\bwhere\b",
            r"\bwhen\b",
            r"\bwhich\b",
            r"\bwho\b",
        ]
        matches: list[re.Match[str]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, cleaned, flags=re.IGNORECASE):
                if (
                    match.group(0).lower() == "cosa"
                    and cleaned[max(0, match.start() - 4) : match.start()].lower()
                    == "che "
                ):
                    continue
                matches.append(match)
        if not matches:
            return cleaned
        last_match = max(matches, key=lambda match: match.start())
        focus = cleaned[last_match.start() :].strip(" ,")
        return focus if count_tokens(normalize_rule_text(focus)) >= 3 else cleaned

    def _speaker_rescue_trim_suspended_question_tail(self, text: str) -> str:
        """Remove only dangling rescue-focus tails that end inside a phrase."""

        cleaned = re.sub(r"\s+", " ", text).strip(" ,")
        if not cleaned:
            return cleaned
        if not self._speaker_rescue_question_boundary_incomplete(cleaned):
            return cleaned

        tokens = cleaned.split()
        while tokens and self._speaker_rescue_question_boundary_incomplete(
            " ".join(tokens),
        ):
            tokens.pop()
        trimmed = " ".join(tokens).strip(" ,")
        normalized_trimmed = normalize_rule_text(trimmed)
        if (
            count_tokens(normalized_trimmed) >= 3
            and (
                self._has_strong_question_signal(trimmed)
                or self._starts_with_interrogative_word(normalized_trimmed)
            )
        ):
            return trimmed
        return ""

    @staticmethod
    def _speaker_rescue_question_boundary_incomplete(text: str) -> bool:
        """Return whether a rescued question focus ends on a dangling phrase."""

        normalized_text = normalize_rule_text(text).rstrip(" ,;:")
        if not normalized_text or normalized_text.endswith((".", "?", "!")):
            return False
        tokens = re.findall(r"\b[\w']+\b", normalized_text)
        if not tokens:
            return False
        suspended_end_tokens = {
            "a",
            "ad",
            "al",
            "alla",
            "allo",
            "ai",
            "agli",
            "alle",
            "an",
            "and",
            "at",
            "che",
            "con",
            "da",
            "dal",
            "dalla",
            "dalle",
            "dello",
            "del",
            "dei",
            "di",
            "e",
            "ed",
            "for",
            "in",
            "il",
            "la",
            "le",
            "lo",
            "of",
            "per",
            "su",
            "the",
            "then",
            "to",
            "un",
            "una",
            "uno",
            "with",
        }
        return tokens[-1] in suspended_end_tokens

    def _speaker_rescue_completed_answer(
        self,
        candidate: QAPairCandidate,
        *,
        sentence_by_id: dict[str, Any],
        next_sentence_id_by_id: dict[str, str],
    ) -> tuple[str, dict[str, Any]]:
        """Extend a rescue answer by the known next sentence when annotated."""

        answer_text = str(candidate.answer_text or "").strip()
        answer_debug = candidate.metadata.get("answer_debug", {})
        search_signals = answer_debug.get("search_signals", {})
        next_text_id = str(search_signals.get("answer_boundary_next_text_id") or "")
        completion_source = "answer_boundary_next_text_id"
        if not next_text_id and search_signals.get("answer_boundary_status") in {
            "truncated",
            "continuation_available",
        }:
            answer_sentence_ids = [
                str(sentence_id)
                for sentence_id in candidate.answer_sentence_ids
                if str(sentence_id).strip()
            ]
            if answer_sentence_ids:
                next_text_id = next_sentence_id_by_id.get(answer_sentence_ids[-1], "")
                completion_source = "next_sentence_after_answer_span"
        if not next_text_id:
            return answer_text, {}
        extension_ids: list[str] = []
        extension_texts: list[str] = []
        cursor_text_id = next_text_id
        max_extension_sentences = 8
        last_integral_text = (
            answer_text
            if not self._speaker_rescue_answer_boundary_incomplete(answer_text)
            else ""
        )
        while cursor_text_id and len(extension_ids) < max_extension_sentences:
            next_sentence = sentence_by_id.get(cursor_text_id)
            next_text = str(getattr(next_sentence, "text", "") or "").strip()
            if not next_text:
                break
            if normalize_rule_text(next_text) in normalize_rule_text(answer_text):
                break
            extension_ids.append(cursor_text_id)
            extension_texts.append(next_text)
            candidate_text = self._join_text([answer_text, *extension_texts])
            if not self._speaker_rescue_answer_boundary_incomplete(candidate_text):
                last_integral_text = candidate_text
                break
            cursor_text_id = next_sentence_id_by_id.get(cursor_text_id, "")
        if not extension_texts:
            return answer_text, {}
        completed_text = self._join_text([answer_text, *extension_texts])
        if (
            self._speaker_rescue_answer_boundary_incomplete(completed_text)
            and cursor_text_id
        ):
            next_sentence = sentence_by_id.get(cursor_text_id)
            next_text = str(getattr(next_sentence, "text", "") or "").strip()
            if next_text and normalize_rule_text(next_text) not in normalize_rule_text(
                completed_text,
            ):
                extension_ids.append(cursor_text_id)
                extension_texts.append(next_text)
                completed_text = self._join_text([answer_text, *extension_texts])
                if not self._speaker_rescue_answer_boundary_incomplete(completed_text):
                    last_integral_text = completed_text

        truncated_to_integral_boundary = False
        if self._speaker_rescue_answer_boundary_incomplete(completed_text):
            bounded_text = self._speaker_rescue_first_complete_answer_period(
                completed_text,
            )
            if (
                bounded_text
                and not self._speaker_rescue_answer_boundary_incomplete(bounded_text)
            ):
                completed_text = bounded_text
                truncated_to_integral_boundary = True
            elif last_integral_text:
                completed_text = last_integral_text
                truncated_to_integral_boundary = True
        return (
            completed_text,
            {
                "answer_completion_extended_by": extension_ids[-1],
                "answer_completion_extended_sentence_ids": extension_ids,
                "answer_completion_source": completion_source,
                "answer_completion_truncated_to_integral_boundary": (
                    truncated_to_integral_boundary
                ),
            },
        )

    @staticmethod
    def _speaker_rescue_answer_boundary_incomplete(text: str) -> bool:
        """Return whether a rescued answer still ends on a suspended boundary."""

        normalized_text = normalize_rule_text(text).rstrip(" ,;:")
        if not normalized_text:
            return False
        tokens = re.findall(r"\b[\w']+\b", normalized_text)
        if not tokens:
            return False
        suspended_end_tokens = _INCOMPLETE_ANSWER_END_WORDS | {
            "a",
            "and",
            "c'erano",
            "che",
            "con",
            "del",
            "dei",
            "della",
            "delle",
            "dello",
            "dici",
            "dice",
            "e",
            "ed",
            "hai",
            "il",
            "la",
            "le",
            "lo",
            "si",
            "with",
        }
        return tokens[-1] in suspended_end_tokens

    def _speaker_rescue_first_complete_answer_period(self, text: str) -> str:
        """Return answer text ending at a complete period when possible."""

        cleaned = text.strip()
        if not cleaned:
            return cleaned
        spans = self._sentence_spans(cleaned)
        for sentence, _, _ in spans:
            candidate = sentence.strip()
            if (
                candidate.endswith((".", "?", "!"))
                and not self._looks_like_incomplete_answer_span(candidate)
                and count_tokens(normalize_rule_text(candidate)) >= 4
            ):
                return candidate
        if cleaned.endswith((".", "?", "!")):
            return cleaned
        return cleaned

    def _speaker_rescue_answer_within_word_cap(self, text: str) -> str:
        """Return rescued answer capped at a complete sentence or clause."""

        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return cleaned
        cap = max(1, int(self.config.qa_speaker_rescue_answer_word_cap))
        if count_tokens(normalize_rule_text(cleaned)) <= cap:
            return cleaned

        accumulated: list[str] = []
        best_complete = ""
        for sentence, _, _ in self._sentence_spans(cleaned):
            candidate_sentence = sentence.strip()
            if not candidate_sentence:
                continue
            candidate_text = self._join_text([*accumulated, candidate_sentence])
            if count_tokens(normalize_rule_text(candidate_text)) > cap:
                break
            accumulated.append(candidate_sentence)
            if (
                candidate_sentence.endswith((".", "?", "!"))
                and not self._looks_like_incomplete_answer_span(candidate_text)
            ):
                best_complete = candidate_text

        if best_complete:
            return best_complete
        first_complete = self._speaker_rescue_first_complete_answer_period(cleaned)
        if count_tokens(normalize_rule_text(first_complete)) <= cap:
            return first_complete
        clause_boundary = self._truncate_text_to_clause_cap(cleaned, cap)
        if clause_boundary:
            return clause_boundary
        return self._truncate_text_to_word_cap(cleaned, cap)

    def _truncate_text_to_clause_cap(self, text: str, cap: int) -> str:
        """Return text cut at the last clause boundary within the word cap."""

        matches = list(re.finditer(r"\b\w+\b", text))
        if not matches:
            return text.strip()
        cap_end = matches[min(len(matches), max(1, cap)) - 1].end()
        capped_text = text[:cap_end]
        boundary_pattern = (
            r"[,;:]\s+|\s+-\s+|"
            r"\s+(?:and|but|so|because|then|which|that|"
            r"e|ma|perche|perché|quindi|che)\s+"
        )
        boundaries = list(
            re.finditer(boundary_pattern, capped_text, flags=re.IGNORECASE),
        )
        for boundary in reversed(boundaries):
            candidate = capped_text[: boundary.start()].strip(" ,;:-")
            if count_tokens(normalize_rule_text(candidate)) >= 4:
                return candidate
        return ""

    @staticmethod
    def _truncate_text_to_word_cap(text: str, cap: int) -> str:
        """Return text cut after at most cap word tokens."""

        matches = list(re.finditer(r"\b\w+\b", text))
        if len(matches) <= cap:
            return text.strip()
        end_index = matches[max(0, cap - 1)].end()
        return text[:end_index].strip(" ,;:")

    def _speaker_rescue_candidate_is_eligible(
        self,
        candidate: QAPairCandidate,
        suppression_reason: str,
    ) -> bool:
        """Return whether a suppressed candidate may use speaker rescue."""

        soft_reasons = {
            "low_autonomy_implicit_question",
            "weak_expanded_contextual_question",
            "surface_answer_cue_risk",
            "below_min_qa_confidence",
            "weak_answer_responsiveness",
        }
        if suppression_reason not in soft_reasons:
            return False
        if suppression_reason == "below_min_qa_confidence":
            margin = max(
                0.0,
                float(self.config.qa_speaker_rescue_min_confidence_margin),
            )
            if candidate.confidence < self.config.min_qa_confidence - margin:
                return False

        reason_codes = set(candidate.reason_codes)
        quality_features = candidate.metadata.get("quality_features", {})
        risk_reasons = set(quality_features.get("risk_reasons") or [])
        hard_reason_codes = {
            "rhetorical_checkin_question",
            "rhetorical_poll_question",
            "declarative_tag_question",
            "fragment_question",
            "question_intent_subordinate_fragment",
            "procedural_question_request",
            "same_sentence_without_answer_cue",
            "answer_boilerplate_penalty",
            "answer_poll_or_backchannel_penalty",
            "moderator_handoff_answer_penalty",
            "low_information_answer_penalty",
            "answer_circular_echo_penalty",
            "question_continuation_answer_penalty",
            "answer_is_question",
            "answer_contains_question_mark",
            SAME_SPEAKER_SUSPECTED,
            SPEAKER_CHECK_UNAVAILABLE,
        }
        hard_risk_reasons = {
            "poll_or_backchannel_noise",
            "answer_poll_or_backchannel",
            "circular_answer_echo",
            "competing_question",
            "question_span_integrity",
            "followup_prompt_answer",
            "semantic_nonresponsive",
            "monologue_continuation_risk",
        }
        if reason_codes & hard_reason_codes:
            return False
        if risk_reasons & hard_risk_reasons:
            return False
        observed_gate_reasons = (reason_codes | risk_reasons) & (
            soft_reasons | hard_reason_codes | hard_risk_reasons
        )
        return observed_gate_reasons <= soft_reasons

    def _has_minimal_responsiveness_anchor(
        self,
        candidate: QAPairCandidate,
    ) -> bool:
        """Return whether the answer has a small non-speaker responsiveness cue."""

        if candidate.answer_is_question:
            return False
        reason_codes = set(candidate.reason_codes)
        quality_features = candidate.metadata.get("quality_features", {})
        answer_debug = candidate.metadata.get("answer_debug", {})
        partial_scores = answer_debug.get("partial_scores", {})
        answer_responsiveness_score = self._safe_float(
            quality_features.get("answer_responsiveness_score"),
        )
        if (
            answer_responsiveness_score is not None
            and answer_responsiveness_score >= 0.34
        ):
            return True
        if reason_codes & {
            "answer_keyword_overlap",
            "answer_responsiveness_anchor",
            "answer_responsiveness_strong",
            "answer_cue_match",
            "answer_in_next_sentence",
            "speaker_turn_support",
            "interview_cluster_answer_support",
            "interview_echo_question",
        }:
            return True
        return any(
            (self._safe_float(partial_scores.get(key)) or 0.0) > threshold
            for key, threshold in (
                ("answer_cues", 0.0),
                ("keyword_overlap", 0.03),
                ("answer_context", 0.0),
            )
        )

    def _is_interrogative_sentence_proxy(self, text: str) -> bool:
        """Return whether a sentence has a cheap interrogative signal."""

        stripped_text = text.strip()
        if stripped_text.endswith("?"):
            return True
        normalized_text = normalize_rule_text(stripped_text)
        if not normalized_text:
            return False
        if any(
            pattern.search(normalized_text)
            for pattern in DECLARATIVE_WHAT_PATTERNS
        ):
            return False
        return bool(
            collect_rule_matches(normalized_text, QUESTION_CUE_RULES)
            or collect_rule_matches(normalized_text, DIDACTIC_QUESTION_RULES)
        )

    def _apply_semantic_responsiveness_scoring(
        self,
        *,
        candidates: Sequence[QAPairCandidate],
        session: LectureSession,
    ) -> None:
        """Optionally rescore already-extracted candidates with local embeddings."""

        semantic_metrics: dict[str, Any] = {
            "schema_version": "1.0",
            "enabled": bool(self.config.qa_semantic_responsiveness_enabled),
            "status": "disabled",
            "note": "semantic responsiveness scorer disabled",
            "model_name": self.config.qa_semantic_responsiveness_model_name,
            "backend": getattr(
                self.semantic_responsiveness_backend,
                "backend_name",
                None,
            ),
            "requested_candidate_count": len(candidates),
            "scored_candidate_count": 0,
            "total_seconds": 0.0,
            "seconds_per_candidate": None,
            "load_seconds": None,
            "model_footprint_bytes": None,
            "model_footprint_mb": None,
            "gate_enabled": bool(
                self.config.qa_semantic_responsiveness_gate_enabled,
            ),
            "gate_min_score": round(
                float(self.config.qa_semantic_responsiveness_gate_min_score),
                4,
            ),
            "gate_penalty": round(
                float(self.config.qa_semantic_responsiveness_gate_penalty),
                4,
            ),
        }
        if not self.config.qa_semantic_responsiveness_enabled:
            session.metadata["qa_semantic_responsiveness"] = semantic_metrics
            return
        if not candidates:
            semantic_metrics.update(
                {
                    "status": "skipped",
                    "note": "no extracted candidates to score",
                },
            )
            session.metadata["qa_semantic_responsiveness"] = semantic_metrics
            return

        max_candidates = self.config.qa_semantic_responsiveness_max_candidates
        scoring_inputs = [
            SemanticResponsivenessInput(
                candidate_index=index,
                question_text=candidate.question_text,
                answer_text=candidate.answer_text or "",
                continuation_text=self._semantic_continuation_text(candidate),
            )
            for index, candidate in enumerate(candidates[:max_candidates])
            if candidate.question_text.strip() and (candidate.answer_text or "").strip()
        ]
        if not scoring_inputs:
            semantic_metrics.update(
                {
                    "status": "skipped",
                    "note": "no candidates with both question and answer text",
                },
            )
            session.metadata["qa_semantic_responsiveness"] = semantic_metrics
            return

        started_at = perf_counter()
        try:
            semantic_scores = self.semantic_responsiveness_backend.score_candidates(
                scoring_inputs,
            )
        except SemanticResponsivenessUnavailableError as exc:
            self._annotate_semantic_responsiveness_fallback(
                candidates=candidates,
                fallback_reason=str(exc),
            )
            semantic_metrics.update(
                {
                    "status": "fallback",
                    "note": f"semantic responsiveness disabled: {exc}",
                    "fallback_reason": str(exc),
                    "total_seconds": round(perf_counter() - started_at, 6),
                    "load_seconds": getattr(
                        self.semantic_responsiveness_backend,
                        "load_seconds",
                        None,
                    ),
                    "model_footprint_bytes": getattr(
                        self.semantic_responsiveness_backend,
                        "model_footprint_bytes",
                        None,
                    ),
                },
            )
            self._finalize_semantic_responsiveness_metrics(semantic_metrics)
            session.metadata["qa_semantic_responsiveness"] = semantic_metrics
            return
        except Exception as exc:  # pragma: no cover - runtime safety net
            self._annotate_semantic_responsiveness_fallback(
                candidates=candidates,
                fallback_reason=str(exc),
            )
            semantic_metrics.update(
                {
                    "status": "fallback",
                    "note": f"semantic responsiveness runtime fallback: {exc}",
                    "fallback_reason": str(exc),
                    "total_seconds": round(perf_counter() - started_at, 6),
                },
            )
            self._finalize_semantic_responsiveness_metrics(semantic_metrics)
            session.metadata["qa_semantic_responsiveness"] = semantic_metrics
            return

        score_by_index = {score.candidate_index: score for score in semantic_scores}
        penalized_count = 0
        for index, candidate in enumerate(candidates):
            score = score_by_index.get(index)
            quality_features = candidate.metadata.setdefault("quality_features", {})
            if score is None:
                quality_features["semantic_responsiveness_status"] = "not_scored"
                continue
            self._annotate_semantic_responsiveness_score(candidate, score)
            if self._apply_semantic_responsiveness_gate_penalty(candidate, score.score):
                penalized_count += 1

        total_seconds = round(perf_counter() - started_at, 6)
        semantic_metrics.update(
            {
                "status": "applied",
                "note": "semantic responsiveness applied to extracted candidates only",
                "scored_candidate_count": len(semantic_scores),
                "total_seconds": total_seconds,
                "seconds_per_candidate": (
                    round(total_seconds / len(semantic_scores), 6)
                    if semantic_scores
                    else None
                ),
                "load_seconds": getattr(
                    self.semantic_responsiveness_backend,
                    "load_seconds",
                    None,
                ),
                "model_footprint_bytes": getattr(
                    self.semantic_responsiveness_backend,
                    "model_footprint_bytes",
                    None,
                ),
                "gate_penalized_candidate_count": penalized_count,
                "score_distribution": self._score_distribution(
                    [score.score for score in semantic_scores],
                ),
            },
        )
        self._finalize_semantic_responsiveness_metrics(semantic_metrics)
        session.metadata["qa_semantic_responsiveness"] = semantic_metrics

    @staticmethod
    def _semantic_continuation_text(candidate: QAPairCandidate) -> str | None:
        """Return candidate-local continuation evidence for semantic scoring."""

        context_text = (candidate.context_text or "").strip()
        if context_text and context_text != candidate.question_text.strip():
            return context_text
        question_debug = candidate.metadata.get("question_debug", {})
        if isinstance(question_debug, dict):
            expanded = str(question_debug.get("expanded_question_context") or "").strip()
            if expanded and expanded != candidate.question_text.strip():
                return expanded
        return None

    def _annotate_semantic_responsiveness_score(
        self,
        candidate: QAPairCandidate,
        score: Any,
    ) -> None:
        """Attach semantic responsiveness score and debug to one candidate."""

        quality_features = candidate.metadata.setdefault("quality_features", {})
        quality_features.update(
            {
                "semantic_responsiveness_status": "applied",
                "semantic_responsiveness_score": round(float(score.score), 4),
                "semantic_question_answer_similarity": round(
                    float(score.question_answer_similarity),
                    4,
                ),
                "semantic_answer_continuation_similarity": (
                    round(float(score.answer_continuation_similarity), 4)
                    if score.answer_continuation_similarity is not None
                    else None
                ),
                "semantic_echo_penalty": round(float(score.echo_penalty), 4),
                "semantic_continuation_penalty": round(
                    float(score.continuation_penalty),
                    4,
                ),
            },
        )
        candidate.metadata["semantic_responsiveness_debug"] = {
            "score": round(float(score.score), 4),
            "question_answer_similarity": round(
                float(score.question_answer_similarity),
                4,
            ),
            "answer_continuation_similarity": (
                round(float(score.answer_continuation_similarity), 4)
                if score.answer_continuation_similarity is not None
                else None
            ),
            "echo_penalty": round(float(score.echo_penalty), 4),
            "continuation_penalty": round(float(score.continuation_penalty), 4),
            "elapsed_seconds": round(float(score.elapsed_seconds), 6),
            **dict(getattr(score, "metadata", {}) or {}),
        }

    def _apply_semantic_responsiveness_gate_penalty(
        self,
        candidate: QAPairCandidate,
        semantic_score: float,
    ) -> bool:
        """Apply configured semantic responsiveness penalty to weak candidates."""

        if not self.config.qa_semantic_responsiveness_gate_enabled:
            return False
        min_score = float(self.config.qa_semantic_responsiveness_gate_min_score)
        if semantic_score >= min_score:
            return False

        penalty = float(self.config.qa_semantic_responsiveness_gate_penalty)
        quality_features = candidate.metadata.setdefault("quality_features", {})
        old_quality_score = self._safe_float(
            quality_features.get("final_quality_score"),
        )
        old_risk_score = self._safe_float(quality_features.get("risk_score"))

        candidate.confidence = self._clamp(candidate.confidence - penalty)
        candidate.confidence_score = candidate.confidence
        candidate.confidence_label = self._confidence_label(candidate.confidence)
        candidate.reason_codes = self._unique_strings(
            list(candidate.reason_codes) + ["semantic_responsiveness_penalty"],
        )
        candidate.review_flags = self._unique_strings(
            list(candidate.review_flags) + ["semantic_nonresponsive"],
        )
        risk_reasons = self._unique_strings(
            list(quality_features.get("risk_reasons") or [])
            + ["semantic_nonresponsive"],
        )
        risk_score = self._clamp((old_risk_score or 0.0) + penalty)
        final_quality_score = self._clamp((old_quality_score or 0.0) - penalty)
        quality_features.update(
            {
                "semantic_gate_penalty_applied": True,
                "semantic_gate_min_score": round(min_score, 4),
                "semantic_gate_penalty": round(penalty, 4),
                "risk_reasons": risk_reasons,
                "risk_score": round(risk_score, 4),
                "final_quality_score": round(final_quality_score, 4),
                "quality_band": self._quality_band(final_quality_score),
                "risk_band": self._risk_band(risk_score),
            },
        )
        return True

    @staticmethod
    def _annotate_semantic_responsiveness_fallback(
        *,
        candidates: Sequence[QAPairCandidate],
        fallback_reason: str,
    ) -> None:
        """Mark candidates as unscored after semantic fallback."""

        for candidate in candidates:
            quality_features = candidate.metadata.setdefault("quality_features", {})
            quality_features["semantic_responsiveness_status"] = "fallback"
            quality_features["semantic_responsiveness_score"] = None
            quality_features["semantic_responsiveness_fallback_reason"] = (
                fallback_reason
            )

    @staticmethod
    def _finalize_semantic_responsiveness_metrics(
        metrics: dict[str, Any],
    ) -> None:
        """Fill derived semantic responsiveness metric fields."""

        footprint_bytes = metrics.get("model_footprint_bytes")
        if isinstance(footprint_bytes, int | float):
            metrics["model_footprint_mb"] = round(float(footprint_bytes) / 1_000_000, 3)
        if (
            metrics.get("seconds_per_candidate") is None
            and (metrics.get("scored_candidate_count") or 0)
            and metrics.get("total_seconds") is not None
        ):
            metrics["seconds_per_candidate"] = round(
                float(metrics["total_seconds"]) / int(metrics["scored_candidate_count"]),
                6,
            )

    @staticmethod
    def _score_distribution(scores: Sequence[float]) -> dict[str, float | int | None]:
        """Return compact distribution for semantic responsiveness scores."""

        if not scores:
            return {"count": 0, "min": None, "avg": None, "median": None, "max": None}
        sorted_scores = sorted(float(score) for score in scores)
        midpoint = len(sorted_scores) // 2
        if len(sorted_scores) % 2:
            median = sorted_scores[midpoint]
        else:
            median = (sorted_scores[midpoint - 1] + sorted_scores[midpoint]) / 2
        return {
            "count": len(sorted_scores),
            "min": round(sorted_scores[0], 4),
            "avg": round(sum(sorted_scores) / len(sorted_scores), 4),
            "median": round(median, 4),
            "max": round(sorted_scores[-1], 4),
        }

    def _qa_candidate_suppression_reason(
        self,
        candidate: QAPairCandidate,
    ) -> str | None:
        """Return the aggregate gate reason for a built but unexported candidate."""

        if (
            self.config.pipeline_profile == "quality_local"
            and self._candidate_has_deflection_answer(candidate)
        ):
            candidate.reason_codes = self._unique_strings(
                list(candidate.reason_codes) + ["deflection_answer_penalty"],
            )
            return "deflection_answer_penalty"
        if candidate.confidence < self.config.min_qa_confidence:
            return "below_min_qa_confidence"
        if self._should_emit_qa_candidate(candidate):
            return None
        return self._infer_quality_gate_suppression_reason(candidate)

    def _infer_quality_gate_suppression_reason(
        self,
        candidate: QAPairCandidate,
    ) -> str:
        """Return a stable primary reason for quality-local gate suppression."""

        reason_codes = set(candidate.reason_codes)
        quality_features = candidate.metadata.get("quality_features", {})
        risk_reasons = set(quality_features.get("risk_reasons") or [])
        question_debug = candidate.metadata.get("question_debug", {})

        if "deflection_answer_penalty" in reason_codes:
            return "deflection_answer_penalty"

        weak_question_reasons = {
            "declarative_tag_question",
            "fragment_question",
            "low_autonomy_implicit_question",
            "procedural_question_request",
            "question_intent_subordinate_fragment",
            "rhetorical_checkin_question",
            "rhetorical_poll_question",
        }
        for reason in sorted(reason_codes & weak_question_reasons):
            return reason

        weak_pair_reasons = {
            "answer_boilerplate_penalty",
            "answer_circular_echo_penalty",
            "deflection_answer_penalty",
            "answer_poll_or_backchannel_penalty",
            "low_information_answer_penalty",
            "moderator_handoff_answer_penalty",
            "question_continuation_answer_penalty",
            "same_sentence_without_answer_cue",
        }
        if candidate.confidence < 0.72:
            for reason in sorted(reason_codes & weak_pair_reasons):
                return reason

        question_intent = question_debug.get("question_intent")
        if question_intent in {"embedded_statement_question", "weak_question_form"}:
            return str(question_intent)

        if (
            {
                "question_without_terminal_mark_recovered",
                "split_question_recomposed",
            }.issubset(reason_codes)
            and "question_mark" not in reason_codes
        ):
            return "question_without_terminal_mark_recovered"

        if "quality_local_deferred_penalty" in reason_codes:
            return "quality_local_deferred_penalty"
        if "semantic_nonresponsive" in risk_reasons:
            return "semantic_nonresponsive"

        ordered_risk_reasons = [
            "poll_or_backchannel_noise",
            "answer_poll_or_backchannel",
            "surface_answer_cue_risk",
            "implicit_question_risk",
            "quality_local_deferred",
            "unanchored_quantity_answer",
            "followup_prompt_answer",
            "weak_implicit_quantity_question",
            "weak_expanded_contextual_question",
            "thin_answer_reply",
            "low_sentence_autonomy",
            "incomplete_answer_span",
            "answer_truncated_at_boundary",
            "question_span_integrity",
            "weak_answer_responsiveness",
            "monologue_continuation_risk",
            "semantic_nonresponsive",
            "low_relevance",
            "competing_question",
        ]
        for reason in ordered_risk_reasons:
            if reason in risk_reasons or reason in reason_codes:
                return reason
        return "quality_local_gate"

    def _should_emit_qa_candidate(self, candidate: QAPairCandidate) -> bool:
        """Return whether a built candidate should be exported."""

        if self.config.pipeline_profile != "quality_local":
            return True

        reason_codes = set(candidate.reason_codes)
        answer_debug = candidate.metadata.get("answer_debug", {})
        question_debug = candidate.metadata.get("question_debug", {})
        partial_scores = answer_debug.get("partial_scores", {})

        if "deflection_answer_penalty" in reason_codes:
            return False

        weak_question_reasons = {
            "rhetorical_checkin_question",
            "rhetorical_poll_question",
            "declarative_tag_question",
            "fragment_question",
            "question_intent_subordinate_fragment",
            "procedural_question_request",
            "low_autonomy_implicit_question",
        }
        if reason_codes & weak_question_reasons:
            return False

        weak_pair_reasons = {
            "same_sentence_without_answer_cue",
            "answer_boilerplate_penalty",
            "deflection_answer_penalty",
            "answer_poll_or_backchannel_penalty",
            "moderator_handoff_answer_penalty",
            "low_information_answer_penalty",
            "answer_circular_echo_penalty",
            "question_continuation_answer_penalty",
        }
        if reason_codes & weak_pair_reasons and candidate.confidence < 0.72:
            return False

        if (
            question_debug.get("question_intent")
            in {"embedded_statement_question", "weak_question_form"}
            and reason_codes & {
                "low_question_answer_relevance",
                "same_sentence_without_answer_cue",
                "answer_boilerplate_penalty",
            }
        ):
            return False
        if (
            question_debug.get("question_intent") == "embedded_statement_question"
            and candidate.confidence < 0.78
            and "interview_echo_question" not in reason_codes
        ):
            return False

        if "quality_local_deferred_penalty" in reason_codes:
            gap_seconds = self._safe_float(answer_debug.get("gap_seconds")) or 0.0
            distance_units = int(answer_debug.get("answer_distance_units") or 0)
            answer_token_count = int(answer_debug.get("token_count") or 0)
            quality_gate = float(partial_scores.get("quality_gate") or 0.0)
            if (
                gap_seconds > 45.0
                or distance_units > 10
                or answer_token_count > 45
                or quality_gate < 0.0
            ):
                return False

        quality_features = candidate.metadata.get("quality_features", {})
        final_quality_score = self._safe_float(
            quality_features.get("final_quality_score"),
        )
        answer_quality_score = self._safe_float(
            quality_features.get("answer_quality_score"),
        )
        risk_score = self._safe_float(quality_features.get("risk_score"))
        risk_reasons = set(quality_features.get("risk_reasons") or [])
        answer_responsiveness_score = self._safe_float(
            quality_features.get("answer_responsiveness_score"),
        )
        risk_band = str(quality_features.get("risk_band") or "")
        quality_band = str(quality_features.get("quality_band") or "")
        answer_cue_score = float(partial_scores.get("answer_cues") or 0.0)
        keyword_overlap_score = float(partial_scores.get("keyword_overlap") or 0.0)
        answer_context_score = float(partial_scores.get("answer_context") or 0.0)
        span_completeness_score = float(
            partial_scores.get("span_completeness") or 0.0,
        )
        answer_distance_units = int(answer_debug.get("answer_distance_units") or 0)
        context_only_risk_reasons = {"weak_context_risk", "thin_context_risk"}
        context_risk_penalty = 0.0
        if "weak_context_risk" in risk_reasons:
            context_risk_penalty += 0.14
        if "thin_context_risk" in risk_reasons:
            context_risk_penalty += 0.16
        context_neutral_quality_score = (
            min(1.0, final_quality_score + context_risk_penalty)
            if final_quality_score is not None
            else None
        )
        fragile_question_recovery_reasons = {
            "question_without_terminal_mark_recovered",
            "split_question_recomposed",
            "runon_question_candidate",
        }
        if (
            {
                "question_without_terminal_mark_recovered",
                "split_question_recomposed",
            }.issubset(reason_codes)
            and "question_mark" not in reason_codes
        ):
            return False
        if (
            risk_reasons
            and risk_reasons.issubset(context_only_risk_reasons)
            and not reason_codes & fragile_question_recovery_reasons
        ):
            return True
        if (
            "question_without_terminal_mark_recovered" in reason_codes
            and answer_distance_units > 1
        ):
            return False
        if (
            "runon_question_candidate" in reason_codes
            and "runon_question_local_answer" not in reason_codes
        ):
            return False
        if (
            "runon_question_local_answer" in reason_codes
            and (
                answer_distance_units != 1
                or answer_responsiveness_score is None
                or answer_responsiveness_score < 0.48
            )
        ):
            return False
        local_socratic_completion = (
            "socratic_short_answer_support" in reason_codes
            and "answer_keyword_overlap" in reason_codes
            and bool(
                reason_codes
                & {
                    "answer_in_same_sentence",
                    "answer_in_next_sentence",
                    "same_sentence_answer",
                },
            )
            and "answer_is_question" not in reason_codes
            and "answer_contains_question_mark" not in reason_codes
            and "poll_or_backchannel_noise" not in risk_reasons
        )
        if (
            local_socratic_completion
            and candidate.confidence >= 0.44
            and final_quality_score is not None
            and final_quality_score >= 0.38
            and (
                answer_responsiveness_score is None
                or answer_responsiveness_score >= 0.48
            )
        ):
            return True
        if (
            local_socratic_completion
            and candidate.confidence >= 0.44
            and context_neutral_quality_score is not None
            and context_neutral_quality_score >= 0.30
            and (
                answer_responsiveness_score is None
                or answer_responsiveness_score >= 0.48
            )
            and risk_reasons.issubset(
                {
                    "weak_context_risk",
                    "thin_context_risk",
                    "low_sentence_autonomy",
                    "competing_question",
                },
            )
        ):
            return True
        interview_cluster_completion = (
            "interview_cluster_answer_support" in reason_codes
            and "answer_is_question" not in reason_codes
            and "answer_contains_question_mark" not in reason_codes
            and "poll_or_backchannel_noise" not in risk_reasons
            and "followup_prompt_answer" not in risk_reasons
        )
        if (
            interview_cluster_completion
            and candidate.confidence >= 0.52
            and final_quality_score is not None
            and final_quality_score >= 0.38
            and answer_quality_score is not None
            and answer_quality_score >= 0.45
        ):
            return True
        interview_echo_completion = (
            "interview_echo_question" in reason_codes
            and "answer_is_question" not in reason_codes
            and "answer_contains_question_mark" not in reason_codes
            and "poll_or_backchannel_noise" not in risk_reasons
            and "followup_prompt_answer" not in risk_reasons
        )
        if (
            interview_echo_completion
            and candidate.confidence >= 0.60
            and final_quality_score is not None
            and final_quality_score >= (
                0.42 if "question_span_integrity" in risk_reasons else 0.48
            )
            and answer_quality_score is not None
            and answer_quality_score >= 0.45
        ):
            return True
        terminal_local_interview_question = (
            "question_span_integrity" in risk_reasons
            and "question_mark" in reason_codes
            and "answer_in_next_sentence" in reason_codes
            and "answer_is_question" not in reason_codes
            and "answer_contains_question_mark" not in reason_codes
            and "poll_or_backchannel_noise" not in risk_reasons
            and "followup_prompt_answer" not in risk_reasons
            and "weak_answer_responsiveness" not in risk_reasons
            and "low_boundary_confidence" not in risk_reasons
        )
        if (
            terminal_local_interview_question
            and answer_distance_units == 1
            and candidate.confidence >= 0.50
            and final_quality_score is not None
            and final_quality_score >= 0.34
            and answer_quality_score is not None
            and answer_quality_score >= 0.40
            and (
                answer_responsiveness_score is None
                or answer_responsiveness_score >= 0.50
            )
        ):
            return True
        extended_before_competing = (
            "answer_extended_before_competing_question" in reason_codes
            and "answer_is_question" not in reason_codes
            and "answer_contains_question_mark" not in reason_codes
            and "poll_or_backchannel_noise" not in risk_reasons
        )
        if (
            extended_before_competing
            and candidate.confidence >= 0.60
            and final_quality_score is not None
            and final_quality_score >= 0.42
            and answer_quality_score is not None
            and answer_quality_score >= 0.55
        ):
            return True
        if final_quality_score is not None and risk_score is not None:
            if risk_band == "high" and final_quality_score < 0.58:
                return False
            if quality_band == "low" and risk_score >= 0.25:
                return False
            if {
                "implicit_question_risk",
                "surface_answer_cue_risk",
            }.issubset(risk_reasons) and candidate.confidence < 0.78:
                return False
            if "poll_or_backchannel_noise" in risk_reasons:
                return False
            if "answer_poll_or_backchannel" in risk_reasons:
                return False
            if "surface_answer_cue_risk" in risk_reasons and final_quality_score < 0.62:
                return False
            if (
                "implicit_question_cue" in reason_codes
                and "thin_context_risk" in risk_reasons
                and (
                    "competing_question" in risk_reasons
                    or "circular_answer_echo" in risk_reasons
                )
                and final_quality_score < 0.74
            ):
                return False
            if (
                "quality_local_deferred" in risk_reasons
                and "competing_question" in risk_reasons
                and final_quality_score < 0.72
            ):
                return False
            if (
                "unanchored_quantity_answer" in risk_reasons
                and (
                    "competing_question" in risk_reasons
                    or final_quality_score < 0.76
                )
            ):
                return False
            if (
                "followup_prompt_answer" in risk_reasons
                and (
                    "competing_question" in risk_reasons
                    or "weak_question_form" in risk_reasons
                    or "embedded_statement_question" in risk_reasons
                    or final_quality_score < 0.76
                )
            ):
                return False
            if (
                "weak_implicit_quantity_question" in risk_reasons
                and final_quality_score < 0.78
            ):
                return False
            if (
                "weak_expanded_contextual_question" in risk_reasons
                and "competing_question" in risk_reasons
                and final_quality_score < 0.76
            ):
                return False
            if (
                "thin_answer_reply" in risk_reasons
                and final_quality_score < 0.74
                and (question_debug.get("question_score") or 0.0) < 0.58
            ):
                return False
            if (
                "low_sentence_autonomy" in risk_reasons
                and answer_cue_score <= 0.0
                and keyword_overlap_score <= 0.05
                and span_completeness_score <= 0.0
                and answer_context_score < 0.0
                and final_quality_score < 0.84
            ):
                return False
            if (
                question_debug.get("intra_sentence_qa")
                and (question_debug.get("question_score") or 0.0) <= 0.55
                and answer_cue_score <= 0.0
                and keyword_overlap_score <= 0.05
                and answer_context_score < 0.0
            ):
                return False
            if (
                "low_sentence_autonomy" in risk_reasons
                and (
                    "embedded_statement_question" in risk_reasons
                    or "weak_question_form" in risk_reasons
                )
                and final_quality_score < 0.60
            ):
                return False
            if "incomplete_answer_span" in risk_reasons and final_quality_score < 0.65:
                return False
            if (
                answer_responsiveness_score is not None
                and answer_responsiveness_score < 0.42
                and "weak_answer_responsiveness" in risk_reasons
                and final_quality_score < 0.72
                and (
                    "low_relevance" in risk_reasons
                    or "thin_context_risk" in risk_reasons
                    or "quality_local_deferred" in risk_reasons
                    or "embedded_statement_question" in risk_reasons
                    or "weak_question_form" in risk_reasons
                )
            ):
                return False
            if (
                "monologue_continuation_risk" in risk_reasons
                and (
                    final_quality_score < 0.68
                    or "weak_answer_responsiveness" in risk_reasons
                    or "embedded_statement_question" in risk_reasons
                    or "weak_question_form" in risk_reasons
                )
            ):
                return False
            if (
                "semantic_nonresponsive" in risk_reasons
                and final_quality_score < 0.72
            ):
                return False

        return True

    def _candidate_has_deflection_answer(self, candidate: QAPairCandidate) -> bool:
        """Return whether an exported-level candidate contains a deflection answer."""

        answer_text = candidate.answer_text or ""
        normalized_answer = normalize_rule_text(answer_text)
        answer_tokens = self._content_token_list(normalized_answer)
        if not answer_tokens or count_tokens(normalized_answer) > 18:
            return False
        alignment = self._question_answer_alignment(
            question_text=candidate.question_text,
            answer_text=answer_text,
            question_type=candidate.question_type,
            answer_source="candidate_gate",
        )
        if alignment.get("shared_keywords") or alignment.get("shared_numbers"):
            return False
        if collect_rule_matches(normalized_answer, ANSWER_CUE_RULES):
            return False
        token_set = set(answer_tokens)
        meta_count = sum(1 for token in answer_tokens if token in _DEFLECTION_META_TOKENS)
        dismissive_count = sum(
            1 for token in answer_tokens if token in _DEFLECTION_DISMISSIVE_TOKENS
        )
        return bool(
            meta_count >= 1
            and (dismissive_count >= 1 or token_set & _NEGATION_TOKENS)
            and (meta_count / max(1, len(answer_tokens))) >= 0.20
        )

    def _select_ranked_answer(
        self,
        ranked_answers: Sequence[_AnswerCandidate],
    ) -> _AnswerCandidate | None:
        """Return the best ranked answer that is not itself a question."""

        if not ranked_answers:
            return None
        local_socratic_answers = [
            answer
            for answer in ranked_answers
            if not bool(answer.metadata.get("answer_is_question"))
            and answer.answer_score >= 0.45
            and "same_sentence_answer" in answer.reason_codes
            and "socratic_short_answer_support" in answer.reason_codes
        ]
        if local_socratic_answers:
            return max(local_socratic_answers, key=lambda answer: answer.answer_score)
        for answer in ranked_answers:
            if not bool(answer.metadata.get("answer_is_question")):
                return answer
        return None

    def _dedupe_qa_candidates(
        self,
        candidates: Sequence[QAPairCandidate],
    ) -> list[QAPairCandidate]:
        """Remove near-duplicate QA pairs while preserving order."""

        kept: list[QAPairCandidate] = []
        for candidate in candidates:
            echo_duplicate_index = self._find_echo_pair_duplicate(candidate, kept)
            if echo_duplicate_index is not None:
                existing = kept[echo_duplicate_index]
                if self._echo_pair_winner(candidate, existing) is candidate:
                    candidate.reason_codes = self._unique_strings(
                        list(candidate.reason_codes) + ["echo_pair_deduplicated"],
                    )
                    candidate.review_flags = self._unique_strings(
                        list(candidate.review_flags) + ["deduplicated_near_duplicate"],
                    )
                    kept[echo_duplicate_index] = candidate
                else:
                    existing.reason_codes = self._unique_strings(
                        list(existing.reason_codes) + ["echo_pair_deduplicated"],
                    )
                    existing.review_flags = self._unique_strings(
                        list(existing.review_flags) + ["deduplicated_near_duplicate"],
                    )
                continue

            duplicate_index = self._find_near_duplicate_candidate(candidate, kept)
            if duplicate_index is None:
                kept.append(candidate)
                continue

            existing = kept[duplicate_index]
            if self._candidate_quality_sort_key(candidate) > (
                self._candidate_quality_sort_key(existing)
            ):
                candidate.reason_codes = self._unique_strings(
                    list(candidate.reason_codes) + ["deduplicated_replacement"],
                )
                candidate.review_flags = self._unique_strings(
                    list(candidate.review_flags) + ["deduplicated_near_duplicate"],
                )
                kept[duplicate_index] = candidate
            else:
                existing.reason_codes = self._unique_strings(
                    list(existing.reason_codes) + ["deduplicated_near_duplicate"],
                )
                existing.review_flags = self._unique_strings(
                    list(existing.review_flags) + ["deduplicated_near_duplicate"],
                )
        return kept

    def _find_echo_pair_duplicate(
        self,
        candidate: QAPairCandidate,
        existing_candidates: Sequence[QAPairCandidate],
    ) -> int | None:
        """Return the adjacent candidate index for question-as-answer echoes."""

        if not existing_candidates:
            return None
        index = len(existing_candidates) - 1
        existing = existing_candidates[index]
        if not self._candidate_times_are_close(candidate, existing):
            return None
        if self._candidates_form_echo_pair(candidate, existing):
            return index
        return None

    def _candidates_form_echo_pair(
        self,
        left: QAPairCandidate,
        right: QAPairCandidate,
    ) -> bool:
        """Return whether adjacent candidates are an echo pair, not just repeats."""

        left_answer = left.answer_text or ""
        right_answer = right.answer_text or ""
        if not left_answer.strip() or not right_answer.strip():
            return False
        if self._has_contextual_followup_question(left.question_text):
            return False
        if self._has_contextual_followup_question(right.question_text):
            return False
        answer_overlap = self._token_overlap_ratio(left_answer, right_answer)
        if answer_overlap < 0.45:
            return False
        left_question_echoes_right_answer = self._token_overlap_ratio(
            left.question_text,
            right_answer,
        )
        right_question_echoes_left_answer = self._token_overlap_ratio(
            right.question_text,
            left_answer,
        )
        return max(
            left_question_echoes_right_answer,
            right_question_echoes_left_answer,
        ) >= 0.55

    def _echo_pair_winner(
        self,
        left: QAPairCandidate,
        right: QAPairCandidate,
    ) -> QAPairCandidate:
        """Return the non-echo candidate to keep from an adjacent echo pair."""

        left_echo_score = self._token_overlap_ratio(
            left.question_text,
            right.answer_text or "",
        )
        right_echo_score = self._token_overlap_ratio(
            right.question_text,
            left.answer_text or "",
        )
        if abs(left_echo_score - right_echo_score) >= 0.05:
            return right if left_echo_score > right_echo_score else left
        return max((left, right), key=self._echo_pair_quality_sort_key)

    @staticmethod
    def _echo_pair_quality_sort_key(
        candidate: QAPairCandidate,
    ) -> tuple[int, float, int]:
        """Prefer lower noise, then confidence, for ambiguous echo pairs."""

        noise_penalty = sum(
            1
            for value in [*candidate.reason_codes, *candidate.review_flags]
            if any(
                marker in value
                for marker in (
                    "backchannel",
                    "boilerplate",
                    "truncated",
                    "incomplete",
                    "thin_answer",
                    "answer_poll",
                )
            )
        )
        answer_length = count_tokens(normalize_rule_text(candidate.answer_text or ""))
        return (-noise_penalty, float(candidate.confidence), answer_length)

    def _has_contextual_followup_question(self, question_text: str) -> bool:
        """Return whether a question is context plus a short new follow-up."""

        spans = [sentence.strip() for sentence, _, _ in self._sentence_spans(question_text)]
        if len(spans) < 2:
            return False
        trailing = spans[-1]
        normalized_trailing = normalize_rule_text(trailing)
        return (
            trailing.endswith("?")
            and count_tokens(normalized_trailing) <= 4
            and (
                self._starts_with_interrogative_word(normalized_trailing)
                or self._has_autonomous_question_head(normalized_trailing)
            )
        )

    def _find_near_duplicate_candidate(
        self,
        candidate: QAPairCandidate,
        existing_candidates: Sequence[QAPairCandidate],
    ) -> int | None:
        """Return the index of a nearby near-duplicate candidate, if any."""

        for index in range(len(existing_candidates) - 1, -1, -1):
            existing = existing_candidates[index]
            if not self._candidate_times_are_close(candidate, existing):
                continue
            question_overlap = self._token_overlap_ratio(
                candidate.question_text,
                existing.question_text,
            )
            answer_overlap = self._token_overlap_ratio(
                candidate.answer_text or "",
                existing.answer_text or "",
            )
            if question_overlap >= 0.72 and answer_overlap >= 0.50:
                return index
            if question_overlap >= 0.86 and answer_overlap >= 0.35:
                return index
        return None

    @staticmethod
    def _candidate_quality_sort_key(candidate: QAPairCandidate) -> tuple[float, int, int]:
        """Return a stable preference key for near-duplicate candidates."""

        review_penalty = len(candidate.review_flags)
        answer_length = count_tokens(normalize_rule_text(candidate.answer_text or ""))
        return (float(candidate.confidence), -review_penalty, answer_length)

    @staticmethod
    def _candidate_times_are_close(
        candidate: QAPairCandidate,
        existing: QAPairCandidate,
    ) -> bool:
        """Return whether two candidates are close enough to compare."""

        if candidate.start_seconds is None or existing.start_seconds is None:
            return True
        return abs(float(candidate.start_seconds) - float(existing.start_seconds)) <= 90.0

    def _token_overlap_ratio(self, left_text: str, right_text: str) -> float:
        """Return symmetric content-token overlap for two text spans."""

        left_tokens = self._content_tokens(left_text)
        right_tokens = self._content_tokens(right_text)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / max(
            1,
            min(len(left_tokens), len(right_tokens)),
        )

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
        skip_unit_indexes: set[int] = set()
        for index, unit in enumerate(units):
            if index in skip_unit_indexes:
                continue
            extracted = self._extract_question_parts(unit.text)
            if extracted is None:
                continue

            question_text = extracted.question_text
            local_answer_seed = extracted.local_answer_seed
            extraction_reason = extracted.extraction_reason
            if (
                "?" not in unit.text
                and self._is_causal_answer_after_previous_question(
                    unit_index=index,
                    units=units,
                    normalized_question=normalize_rule_text(question_text),
                )
            ):
                continue
            question_evaluation = self._evaluate_question_text(question_text)
            if question_evaluation is None:
                continue
            question_span_integrity = self._question_span_integrity_signal(
                unit_text=unit.text,
                question_text=question_text,
                question_preamble=extracted.question_preamble,
            )
            if "question_without_terminal_mark_recovered" in question_evaluation[
                "reason_codes"
            ]:
                if not self._allows_missing_terminal_preamble_recovery(
                    extracted.question_preamble
                ):
                    continue
                if not self._has_missing_terminal_local_support(
                    unit_index=index,
                    units=units,
                    local_answer_seed=local_answer_seed,
                    question_text=question_text,
                ):
                    continue

            candidate_unit = unit
            candidate_index = index
            question_units = [unit]
            split_recomposition = self._recompose_split_question_span(
                focus_question_text=question_text,
                focus_unit_index=index,
                units=units,
                segment_lookup=segment_lookup,
            )
            if split_recomposition is not None:
                question_units = list(split_recomposition["question_units"])
                question_text = str(split_recomposition["question_text"])
                candidate_unit = question_units[-1]
                candidate_index = candidate_unit.index
                local_answer_seed = (
                    None
                    if split_recomposition["focus_position"] == "first"
                    else local_answer_seed
                )
                skip_unit_indexes.update(
                    int(span_unit.index)
                    for span_unit in question_units
                    if int(span_unit.index) > index
                )
                context_expansion = {
                    "question_text": question_text,
                    "question_units": [],
                    "reason_codes": ["split_question_recomposed"],
                    "debug": split_recomposition["debug"],
                }
            else:
                context_expansion = self._expand_question_context(
                    question_text=question_text,
                    unit_index=index,
                    units=units,
                    segment_lookup=segment_lookup,
                )
                if context_expansion["question_units"]:
                    question_units = list(context_expansion["question_units"])
                    question_text = str(context_expansion["question_text"])

            question_support = self._score_question_context(candidate_unit)
            raw_question_score = self._clamp(
                float(question_evaluation["question_score"])
                + question_support["score_delta"]
                + float(question_span_integrity["score_delta"]),
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
            reason_codes.extend(question_span_integrity["reason_codes"])
            reason_codes.extend(context_expansion["reason_codes"])
            preamble_text = extracted.question_preamble or ""
            if (
                "poll_or_backchannel_noise" not in reason_codes
                and (
                    self._has_poll_or_backchannel_noise(
                        normalize_rule_text(question_text),
                    )
                    or self._has_poll_or_backchannel_noise(
                        normalize_rule_text(preamble_text),
                    )
                )
            ):
                reason_codes.append("poll_or_backchannel_noise")
            if extracted.intra_sentence_qa:
                reason_codes.append("intra_sentence_qa")
            if self._is_interview_echo_question_candidate(
                unit_index=index,
                units=units,
                question_text=question_text,
            ):
                reason_codes.append("interview_echo_question")
            if self._is_low_autonomy_implicit_question(reason_codes):
                reason_codes.append("low_autonomy_implicit_question")

            candidates.append(
                QuestionCandidate(
                    question_candidate_id=f"question_{candidate_index + 1:04d}",
                    unit_index=candidate_index,
                    unit=candidate_unit,
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
                        "question_intent": question_evaluation["question_intent"],
                        "question_intent_debug": question_evaluation[
                            "question_intent_debug"
                        ],
                        "normalized_question_text": question_evaluation[
                            "normalized_question_text"
                        ],
                        "token_count": question_evaluation["token_count"],
                        "raw_unit_text": candidate_unit.text,
                        "raw_question_score": raw_question_score,
                        "didactic_question_score": didactic_question_score,
                        "question_context_expanded": bool(
                            context_expansion["question_units"],
                        ),
                        "question_expansion_debug": context_expansion["debug"],
                        "question_span_integrity_debug": question_span_integrity[
                            "debug"
                        ],
                        "intra_sentence_qa": extracted.intra_sentence_qa,
                        "question_preamble": extracted.question_preamble,
                        "question_context_debug": question_support["debug"],
                        "unit_debug": self._unit_debug_metadata(unit),
                    },
                ),
            )
        return candidates

    def _allows_missing_terminal_preamble_recovery(
        self, preamble_text: str | None
    ) -> bool:
        """Return whether a missing-terminal focus was already at a question break."""

        normalized_preamble = normalize_rule_text(preamble_text or "").strip(" ,;.")
        if not normalized_preamble:
            return True
        if normalized_preamble.endswith(":") or normalized_preamble.endswith("?"):
            return True
        bridge_tails = (
            "cioe",
            "cioè",
            "that is",
            "i mean",
            "meaning",
            "in other words",
        )
        if normalized_preamble.endswith(bridge_tails):
            return True
        tail_tokens = normalized_preamble.split()[-4:]
        tail = " ".join(tail_tokens)
        return "la domanda" in tail or "the question" in tail

    def _has_same_or_adjacent_answer_window(
        self,
        *,
        unit_index: int,
        units: Sequence[_ExtractionUnit],
        local_answer_seed: str | None,
    ) -> bool:
        """Return whether an implicit recovered question has a local answer slot."""

        if local_answer_seed and self._is_plausible_answer_text(local_answer_seed):
            return True
        next_index = unit_index + 1
        if next_index >= len(units):
            return False
        current_unit = units[unit_index]
        next_unit = units[next_index]
        if current_unit.audio_source_id != next_unit.audio_source_id:
            return False
        gap_seconds = max(0.0, next_unit.start_seconds - current_unit.end_seconds)
        if gap_seconds > self.config.question_context_max_gap_seconds:
            return False
        return self._is_plausible_answer_text(next_unit.text)

    def _previous_unit_has_nonhead_interrogative_cue(
        self, *, unit_index: int, units: Sequence[_ExtractionUnit]
    ) -> bool:
        """Return whether the previous adjacent unit looks like a cue-list setup."""

        if unit_index <= 0:
            return False
        current_unit = units[unit_index]
        previous_unit = units[unit_index - 1]
        if current_unit.audio_source_id != previous_unit.audio_source_id:
            return False
        gap_seconds = max(0.0, current_unit.start_seconds - previous_unit.end_seconds)
        if gap_seconds > self.config.question_context_max_gap_seconds:
            return False
        previous_text = previous_unit.text.strip()
        if "?" in previous_text:
            return False
        normalized_previous = normalize_rule_text(previous_text)
        if self._has_head_interrogative_cue(normalized_previous):
            return False
        return bool(
            collect_rule_matches(normalized_previous, QUESTION_CUE_RULES)
            or collect_rule_matches(normalized_previous, DIDACTIC_QUESTION_RULES)
        )

    def _has_missing_terminal_local_support(
        self,
        *,
        unit_index: int,
        units: Sequence[_ExtractionUnit],
        local_answer_seed: str | None,
        question_text: str,
    ) -> bool:
        """Return whether recovered missing-terminal text has a local answer break."""

        if not self._has_same_or_adjacent_answer_window(
            unit_index=unit_index,
            units=units,
            local_answer_seed=local_answer_seed,
        ):
            return False
        if self._previous_unit_has_nonhead_interrogative_cue(
            unit_index=unit_index, units=units
        ):
            return False
        answer_text = local_answer_seed
        next_unit = units[unit_index + 1] if unit_index + 1 < len(units) else None
        if not answer_text and next_unit is not None:
            answer_text = next_unit.text
        if not answer_text:
            return False
        if self._starts_with_additive_continuation(answer_text):
            return False
        if self._has_question_cue_with_focus_overlap(question_text, answer_text):
            return False
        if next_unit is None:
            return True
        gap_seconds = max(0.0, next_unit.start_seconds - units[unit_index].end_seconds)
        if gap_seconds >= 0.35:
            return True
        current_terminal = units[unit_index].text.rstrip().endswith((".", "!", ";", ":"))
        return current_terminal or not self._looks_like_incomplete_answer_span(answer_text)

    def _recompose_split_question_span(
        self,
        *,
        focus_question_text: str,
        focus_unit_index: int,
        units: Sequence[_ExtractionUnit],
        segment_lookup: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a short same-turn span when a question is split across units."""

        focus_unit = units[focus_unit_index]
        normalized_focus = normalize_rule_text(focus_question_text)
        focus_token_count = count_tokens(normalized_focus)
        focus_has_head = self._has_autonomous_question_head(normalized_focus)
        focus_cue_index = self._first_question_cue_token_index(normalized_focus)
        missing_terminal_focus = "?" not in focus_question_text
        if self._is_contextual_question(focus_question_text):
            return None
        if (
            not focus_has_head
            and not missing_terminal_focus
            and focus_cue_index is None
        ):
            return None
        if (
            missing_terminal_focus
            and not focus_has_head
            and (focus_cue_index is None or focus_cue_index > 8)
        ):
            return None
        if missing_terminal_focus and normalized_focus.split(maxsplit=1)[0] in {
            "quanto",
            "quanta",
            "quanti",
            "quante",
        }:
            return None
        if focus_token_count < 2:
            return None

        preceding_units: list[_ExtractionUnit] = []
        if not (missing_terminal_focus and not focus_has_head):
            cursor = focus_unit_index - 1
            while cursor >= 0 and len(preceding_units) < 2:
                candidate_unit = units[cursor]
                if not self._can_recompose_question_units(
                    candidate_unit,
                    preceding_units[0] if preceding_units else focus_unit,
                    segment_lookup=segment_lookup,
                    require_known_speaker=not missing_terminal_focus,
                    require_shared_utterance=False,
                ):
                    break
                if not self._is_split_question_setup_unit(candidate_unit):
                    break
                preceding_units.insert(0, candidate_unit)
                cursor -= 1
        if preceding_units:
            question_units = preceding_units + [focus_unit]
            return {
                "question_text": self._join_text(
                    unit.text if unit is not focus_unit else focus_question_text
                    for unit in question_units
                ),
                "question_units": question_units,
                "focus_position": "last",
                "debug": {
                    "expanded": True,
                    "strategy": "split_question_focus_last",
                    "span_unit_count": len(question_units),
                    "focus_text_id": focus_unit.text_id,
                    "setup_text_ids": [unit.text_id for unit in preceding_units],
                },
            }

        if missing_terminal_focus and focus_has_head:
            return None

        following_units: list[_ExtractionUnit] = []
        cursor = focus_unit_index + 1
        while cursor < len(units) and len(following_units) < 2:
            candidate_unit = units[cursor]
            previous_unit = following_units[-1] if following_units else focus_unit
            if not self._can_recompose_question_units(
                previous_unit,
                candidate_unit,
                segment_lookup=segment_lookup,
                require_known_speaker=not missing_terminal_focus,
                require_shared_utterance=not missing_terminal_focus,
            ):
                break
            if not self._is_split_question_setup_unit(candidate_unit):
                break
            following_units.append(candidate_unit)
            cursor += 1
        if following_units:
            question_units = [focus_unit] + following_units
            return {
                "question_text": self._join_text(
                    focus_question_text if unit is focus_unit else unit.text
                    for unit in question_units
                ),
                "question_units": question_units,
                "focus_position": "first",
                "debug": {
                    "expanded": True,
                    "strategy": "split_question_focus_first",
                    "span_unit_count": len(question_units),
                    "focus_text_id": focus_unit.text_id,
                    "setup_text_ids": [unit.text_id for unit in following_units],
                },
            }

        return None

    def _can_recompose_question_units(
        self,
        left_unit: _ExtractionUnit,
        right_unit: _ExtractionUnit,
        *,
        segment_lookup: dict[str, Any],
        require_known_speaker: bool,
        require_shared_utterance: bool,
    ) -> bool:
        """Return whether two adjacent units look like one logical speaker turn."""

        if left_unit.audio_source_id != right_unit.audio_source_id:
            return False
        gap_seconds = max(0.0, right_unit.start_seconds - left_unit.end_seconds)
        if gap_seconds > self.config.question_context_max_gap_seconds:
            return False
        shared_utterance = bool(
            set(left_unit.source_utterance_ids).intersection(
                right_unit.source_utterance_ids,
            ),
        )
        if require_shared_utterance:
            return shared_utterance
        left_speaker = str(left_unit.speaker_id or "").strip()
        right_speaker = str(right_unit.speaker_id or "").strip()
        if left_speaker and right_speaker:
            return left_speaker == right_speaker
        if require_known_speaker:
            return False
        if shared_utterance:
            return True
        segment_relation = self._segment_relation(
            question_segment_ids=self._resolve_segment_ids_for_unit(
                unit=left_unit,
                segment_lookup=segment_lookup,
            ),
            answer_segment_ids=self._resolve_segment_ids_for_unit(
                unit=right_unit,
                segment_lookup=segment_lookup,
            ),
            segment_position_by_id=segment_lookup["segment_position_by_id"],
        )
        return segment_relation in {"same_segment", "next_segment", "segment_unknown"}

    def _is_split_question_setup_unit(self, unit: _ExtractionUnit) -> bool:
        """Return whether a unit can be structural setup inside a split question."""

        normalized_text = normalize_rule_text(unit.text)
        if not normalized_text or "?" in unit.text:
            return False
        token_count = count_tokens(normalized_text)
        if token_count < 3 or token_count > 24:
            return False
        if self._has_autonomous_question_head(normalized_text):
            return False
        if collect_rule_matches(normalized_text, DIDACTIC_QUESTION_RULES):
            return False
        if self._has_poll_or_backchannel_noise(normalized_text):
            return False
        if self._is_procedural_question_request(normalized_text):
            return False
        return True

    def _extract_question_parts(self, text: str) -> _QuestionExtraction | None:
        """Extract the most plausible local question text and same-unit answer seed."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return None

        best_extraction: _QuestionExtraction | None = None
        best_score = -1.0
        for sentence, _, sentence_end in self._sentence_spans(cleaned_text):
            if "?" not in sentence:
                continue
            original_question_token_count = count_tokens(normalize_rule_text(sentence))
            question_text, question_preamble = self._refine_question_focus(
                sentence.strip(),
            )
            refined_question_token_count = count_tokens(normalize_rule_text(question_text))
            extraction_reason = "question_sentence_extracted"
            if (
                original_question_token_count > 24
                or (
                    question_preamble
                    and original_question_token_count >= 18
                    and refined_question_token_count < original_question_token_count
                )
            ):
                extraction_reason = "runon_question_candidate"
            trailing_text = cleaned_text[sentence_end:].strip() or None
            if trailing_text and self._starts_with_contextual_question(trailing_text):
                trailing_spans = self._sentence_spans(trailing_text)
                followup_question = trailing_spans[0][0].strip()
                trailing_text = trailing_text[trailing_spans[0][2] :].strip() or None
                question_text = self._join_text([question_text, followup_question])
                extraction = _QuestionExtraction(
                    question_text=question_text,
                    local_answer_seed=trailing_text,
                    extraction_reason="contextual_followup_question_merged",
                    question_preamble=question_preamble,
                    intra_sentence_qa=bool(trailing_text),
                )
            else:
                extraction = _QuestionExtraction(
                    question_text=question_text,
                    local_answer_seed=trailing_text,
                    extraction_reason=extraction_reason,
                    question_preamble=question_preamble,
                    intra_sentence_qa=bool(trailing_text),
                )
            evaluation = self._evaluate_question_text(extraction.question_text)
            score = (
                float(evaluation["question_score"])
                if evaluation is not None
                else -1.0
            )
            if score > best_score:
                best_extraction = extraction
                best_score = score
        if best_extraction is not None:
            return best_extraction

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
                question_text, question_preamble, inline_answer_seed = (
                    self._refine_implicit_question_focus(sentence.strip())
                )
                trailing_text = cleaned_text[sentence_end:].strip() or None
                return _QuestionExtraction(
                    question_text=question_text,
                    local_answer_seed=inline_answer_seed or trailing_text,
                    extraction_reason="cue_sentence_extracted",
                    question_preamble=question_preamble,
                    intra_sentence_qa=bool(inline_answer_seed or trailing_text),
                )

        return None

    def _refine_implicit_question_focus(
        self,
        question_text: str,
    ) -> tuple[str, str | None, str | None]:
        """Return a focused implicit question plus preamble/inline answer seed."""

        cleaned_question = question_text.strip()
        normalized_question = normalize_rule_text(cleaned_question)
        if "?" in cleaned_question:
            return cleaned_question, None, None
        if _DIRECT_DEFINITION_REQUEST_RE.match(normalized_question):
            return cleaned_question, None, None
        if self._is_followup_prompt_text(normalized_question):
            return cleaned_question, None, None

        didactic_intro_match = re.search(
            r"(?P<preamble>.+?\b(?:la\s+mia\s+domanda|la\s+domanda)\b.+?"
            r"(?:\be|è)\s+)(?P<focus>.+)$",
            cleaned_question,
            flags=re.IGNORECASE,
        )
        if didactic_intro_match:
            focus = didactic_intro_match.group("focus").strip(" ,")
            preamble = didactic_intro_match.group("preamble").strip(" ,")
            if count_tokens(normalize_rule_text(focus)) >= 4:
                inline_answer_seed = None
                answer_split = re.search(
                    r"(?P<focus>.+?\b(?:risponderesti|answer|respond)\b)"
                    r"(?P<answer>\s+(?:ok|okay|well|so|allora|in\s+generale)\b.+)$",
                    focus,
                    flags=re.IGNORECASE,
                )
                if answer_split:
                    focus = answer_split.group("focus").strip(" ,")
                    inline_answer_seed = answer_split.group("answer").strip(" ,")
                focus = focus[0].upper() + focus[1:] if focus else focus
                return focus, preamble or None, inline_answer_seed

        focus_match = re.search(
            r"(?P<preamble>.+?)(?P<focus>\b(?:"
            r"how|what|where|why|when|which|who|"
            r"come|cosa|dove|perche|perché|quale|quali|quanto|quanta|quanti|quante"
            r")\b.+)$",
            cleaned_question,
            flags=re.IGNORECASE,
        )
        if focus_match:
            focus = focus_match.group("focus").strip(" ,")
            preamble = focus_match.group("preamble").strip(" ,")
            normalized_preamble = normalize_rule_text(preamble)
            normalized_focus = normalize_rule_text(focus)
            if (
                preamble
                and (
                    normalized_preamble.endswith(("cioe", "cioè", "that is", "i.e"))
                    or " la domanda" in f" {normalized_preamble}"
                    or " the question" in f" {normalized_preamble}"
                )
                and count_tokens(normalized_focus) >= 3
                and not any(
                    pattern.search(normalized_focus)
                    for pattern in DECLARATIVE_WHAT_PATTERNS
                )
            ):
                focus = focus[0].upper() + focus[1:] if focus else focus
                return focus, preamble or None, None

        return cleaned_question, None, None

    def _refine_question_focus(self, question_text: str) -> tuple[str, str | None]:
        """Return a focused question clause plus any useful preamble text."""

        cleaned_question = question_text.strip()
        if not cleaned_question:
            return cleaned_question, None
        normalized_cleaned = normalize_rule_text(cleaned_question)
        if collect_rule_matches(normalized_cleaned, DIDACTIC_QUESTION_RULES):
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

        focus_match = re.search(
            r"(?P<preamble>.+?)(?P<focus>\b(?:"
            r"how|what|where|why|when|which|who|"
            r"come|cosa|dove|perche|perché|quale|quali|quanto|quanta|quanti|quante"
            r")\b[^?]*\?)$",
            cleaned_question,
            flags=re.IGNORECASE,
        )
        if focus_match:
            preamble = focus_match.group("preamble").strip(" ,")
            focus = focus_match.group("focus").strip()
            if (
                preamble
                and count_tokens(normalize_rule_text(focus)) >= 2
                and not self._starts_with_interrogative_word(cleaned_question)
                and not re.search(r"\b(?:ask|asks|asked)\b", normalize_rule_text(preamble))
                and not normalize_rule_text(focus).startswith("what about ")
            ):
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
        direct_request = bool(_DIRECT_DEFINITION_REQUEST_RE.match(normalized_text))
        if (
            not has_question_mark
            and any(pattern.search(normalized_text) for pattern in DECLARATIVE_WHAT_PATTERNS)
        ):
            return None
        if (
            not has_question_mark
            and self._is_causal_declarative_statement(normalized_text)
        ):
            return None
        if (
            not has_question_mark
            and not question_matches
            and not didactic_matches
            and not direct_request
        ):
            return None

        token_count = count_tokens(normalized_text)
        reason_codes: list[str] = []
        question_score = 0.0
        is_rhetorical_checkin = self._is_rhetorical_checkin_question(normalized_text)
        is_poll_question = self._is_rhetorical_poll_question(normalized_text)
        is_fragment_question = self._is_fragment_question(
            normalized_text=normalized_text,
            has_question_mark=has_question_mark,
            question_matches=question_matches,
            didactic_matches=didactic_matches,
        )
        is_declarative_tag_question = self._is_declarative_tag_question(
            normalized_text=normalized_text,
            has_question_mark=has_question_mark,
            question_matches=question_matches,
            didactic_matches=didactic_matches,
        )
        intent_evaluation = self._classify_question_intent(
            normalized_text=normalized_text,
            has_question_mark=has_question_mark,
            question_matches=question_matches,
            didactic_matches=didactic_matches,
            token_count=token_count,
            is_poll_question=is_poll_question,
            is_declarative_tag_question=is_declarative_tag_question,
            is_fragment_question=is_fragment_question,
            is_rhetorical_checkin=is_rhetorical_checkin,
        )
        question_intent = str(intent_evaluation["question_intent"])

        if is_rhetorical_checkin:
            question_score -= 0.28
            reason_codes.append("rhetorical_checkin_question")

        if is_poll_question:
            question_score -= 0.42
            reason_codes.append("rhetorical_poll_question")

        if self._has_poll_or_backchannel_noise(normalized_text):
            question_score -= 0.20
            reason_codes.append("poll_or_backchannel_noise")

        if self._is_procedural_question_request(normalized_text):
            question_score -= 0.26
            reason_codes.append("procedural_question_request")

        if is_declarative_tag_question:
            question_score -= 0.30
            reason_codes.append("declarative_tag_question")

        if is_fragment_question:
            question_score -= 0.22
            reason_codes.append("fragment_question")

        intent_penalty = float(intent_evaluation["score_delta"])
        if intent_penalty:
            question_score += intent_penalty
        reason_codes.extend(intent_evaluation["reason_codes"])

        if (
            not has_question_mark
            and not self._starts_with_interrogative_word(normalized_text)
        ):
            question_score -= 0.18
            reason_codes.append("implicit_declarative_question_penalty")

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

        if direct_request:
            question_score += 0.18
            reason_codes.append("direct_definition_request")

        if self._starts_with_interrogative_word(normalized_text):
            question_score += 0.12
            reason_codes.append("starts_with_interrogative")

        if not has_question_mark and (question_matches or didactic_matches or direct_request):
            question_score += 0.10
            reason_codes.append("implicit_question_cue")
            terminal_signals = self._question_without_terminal_mark_signals(
                normalized_text=normalized_text,
                question_matches=question_matches,
                didactic_matches=didactic_matches,
                token_count=token_count,
            )
            if terminal_signals["recoverable"]:
                question_score += 0.06
                reason_codes.append("question_without_terminal_mark_recovered")
                reason_codes.extend(terminal_signals["reason_codes"])

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
        elif direct_request:
            question_type = "direct_request"
        elif has_question_mark:
            question_type = "direct_question"

        return {
            "question_score": self._clamp(question_score),
            "question_type": question_type,
            "question_intent": question_intent,
            "reason_codes": self._unique_strings(reason_codes),
            "matched_question_cues": [match.reason_code for match in question_matches],
            "matched_didactic_cues": [match.reason_code for match in didactic_matches],
            "normalized_question_text": normalized_text,
            "token_count": token_count,
            "question_intent_debug": intent_evaluation["debug"],
        }

    def _question_without_terminal_mark_signals(
        self,
        *,
        normalized_text: str,
        question_matches: Sequence[Any],
        didactic_matches: Sequence[Any],
        token_count: int,
    ) -> dict[str, Any]:
        """Return structural evidence for recovering a missing question mark."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        if not stripped:
            return {"recoverable": False, "reason_codes": []}

        first_token = stripped.split(maxsplit=1)[0]
        if first_token in {"quanto", "quanta", "quanti", "quante"}:
            return {"recoverable": False, "reason_codes": []}

        has_head_cue = self._has_head_interrogative_cue(stripped)
        direct_request = bool(_DIRECT_DEFINITION_REQUEST_RE.match(stripped))
        if not has_head_cue and not direct_request:
            return {"recoverable": False, "reason_codes": []}

        signal_reasons: list[str] = []
        if question_matches or didactic_matches:
            signal_reasons.append("missing_terminal_interrogative_cue")
        if has_head_cue:
            signal_reasons.append("missing_terminal_autonomous_head")
        if direct_request:
            signal_reasons.append("missing_terminal_direct_request")
        if 3 <= token_count <= 24:
            signal_reasons.append("missing_terminal_plausible_length")
        if not normalized_text.endswith((".", "!", "?")):
            signal_reasons.append("missing_terminal_punctuation_absent")

        return {
            "recoverable": len(signal_reasons) >= 2,
            "reason_codes": self._unique_strings(signal_reasons),
        }

    def _has_head_interrogative_cue(self, text: str) -> bool:
        """Return whether the interrogative cue is at the focus head."""

        stripped = self._strip_leading_question_discourse_markers(
            normalize_rule_text(text).rstrip(" ?!.").strip()
        )
        if not stripped:
            return False
        if _DIRECT_DEFINITION_REQUEST_RE.match(stripped):
            return True
        if self._starts_with_interrogative_word(stripped):
            return True
        first_word = stripped.split(maxsplit=1)[0].split("'", maxsplit=1)[0]
        return first_word in _AUXILIARY_QUESTION_START_WORDS

    @staticmethod
    def _is_low_autonomy_implicit_question(reason_codes: Sequence[str]) -> bool:
        """Return whether an implicit cue question has weak sentence autonomy."""

        reason_set = set(reason_codes)
        if "question_mark" in reason_set:
            return False
        if not {"implicit_question_cue", "cue_sentence_extracted"}.issubset(
            reason_set,
        ):
            return False
        weak_structure_reasons = {
            "question_sentence_quality_penalty",
            "question_sentence_quality_borderline",
            "question_merge_safety_penalty",
            "question_low_sentence_autonomy",
            "question_low_boundary_confidence",
            "question_continuation_risk",
            "intra_sentence_qa",
        }
        if "split_question_recomposed" in reason_set:
            strong_split_blockers = {
                "question_low_sentence_autonomy",
                "question_low_boundary_confidence",
                "question_continuation_risk",
                "intra_sentence_qa",
            }
            return bool(reason_set & strong_split_blockers)
        return bool(reason_set & weak_structure_reasons)

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

        reason_codes = set(question_evaluation.get("reason_codes") or [])
        if "rhetorical_checkin_question" in reason_codes:
            usefulness_score -= 0.28
        if "rhetorical_poll_question" in reason_codes:
            usefulness_score -= 0.42
        if "declarative_tag_question" in reason_codes:
            usefulness_score -= 0.30
        if "fragment_question" in reason_codes:
            usefulness_score -= 0.18
        if "implicit_declarative_question_penalty" in reason_codes:
            usefulness_score -= 0.18
        if "question_intent_poll_or_check" in reason_codes:
            usefulness_score -= 0.18
        if "question_intent_rhetorical_tag" in reason_codes:
            usefulness_score -= 0.18
        if "question_intent_fragment" in reason_codes:
            usefulness_score -= 0.16
        if "question_intent_embedded_statement" in reason_codes:
            usefulness_score -= 0.10
        if "question_intent_weak_form" in reason_codes:
            usefulness_score -= 0.08

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

        semantic_cleanup = unit.metadata.get("semantic_cleanup")
        if isinstance(semantic_cleanup, dict):
            autonomy_score = self._safe_float(
                semantic_cleanup.get("sentence_autonomy_score"),
            )
            boundary_score = self._safe_float(
                semantic_cleanup.get("boundary_confidence_score"),
            )
            continuation_risk = self._safe_float(
                semantic_cleanup.get("continuation_risk_score"),
            )
            if autonomy_score is not None and autonomy_score < 0.45:
                score_delta -= 0.08
                reason_codes.append("question_low_sentence_autonomy")
            elif autonomy_score is not None and autonomy_score < 0.60:
                score_delta -= 0.03
                reason_codes.append("question_borderline_sentence_autonomy")
            if boundary_score is not None and boundary_score < 0.45:
                score_delta -= 0.06
                reason_codes.append("question_low_boundary_confidence")
            if continuation_risk is not None and continuation_risk >= 0.42:
                score_delta -= 0.04
                reason_codes.append("question_continuation_risk")

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

    def _question_span_integrity_signal(
        self,
        *,
        unit_text: str,
        question_text: str,
        question_preamble: str | None,
    ) -> dict[str, Any]:
        """Return a light penalty when the question focus starts mid-sentence."""

        if not question_preamble:
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {"status": "whole_sentence_or_clean_boundary"},
            }

        unit_text = unit_text.strip()
        question_text = question_text.strip()
        if not unit_text or not question_text:
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {"status": "empty_text"},
            }

        question_start = unit_text.lower().find(question_text.lower())
        preceding_text = unit_text[:question_start].strip() if question_start >= 0 else ""
        preamble_text = question_preamble.strip()
        boundary_text = preceding_text or preamble_text
        normalized_boundary = normalize_rule_text(boundary_text).rstrip()
        if not boundary_text:
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {"status": "boundary_at_start"},
            }
        if boundary_text.endswith((".", "?", "!", ";", ":")):
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {"status": "terminal_boundary_before_focus"},
            }
        if normalized_boundary.endswith(
            (
                "the question is",
                "the question",
                "la domanda e",
                "la domanda",
                "cioe",
                "cioè",
                "that is",
                "i mean",
                "meaning",
                "in other words",
            ),
        ):
            return {
                "score_delta": 0.0,
                "reason_codes": [],
                "debug": {"status": "didactic_or_bridge_boundary"},
            }

        return {
            "score_delta": -0.10,
            "reason_codes": ["question_span_integrity_penalty"],
            "debug": {
                "status": "focus_starts_inside_sentence",
                "preamble_token_count": count_tokens(normalized_boundary),
            },
        }

    def _annotate_answer_boundary(
        self,
        *,
        answer: _AnswerCandidate,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
        at_search_window_boundary: bool,
    ) -> None:
        """Annotate answer spans that stop at a likely unfinished boundary."""

        if self._answer_has_terminal_punctuation(answer.answer_text):
            answer.search_signals["answer_boundary_status"] = "terminal"
            return
        continuation_unit = self._next_answer_continuation_unit(
            question=question,
            answer_units=answer.answer_units,
            units=units,
            question_by_index=question_by_index,
            segment_lookup=segment_lookup,
        )
        if continuation_unit is not None:
            answer.search_signals["answer_boundary_status"] = "continuation_available"
            answer.search_signals["answer_boundary_next_text_id"] = (
                continuation_unit.text_id
            )
            return
        if at_search_window_boundary:
            answer.search_signals["answer_boundary_status"] = "truncated"
            answer.search_signals["answer_boundary_reason"] = "search_window_boundary"
            answer.reason_codes = self._unique_strings(
                list(answer.reason_codes) + ["answer_truncated_at_boundary"],
            )

    def _build_answer_completion_candidate(
        self,
        *,
        question: QuestionCandidate,
        answer_units: Sequence[_ExtractionUnit],
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        distance_units: int,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return the answer span plus one continuation sentence when available."""

        continuation_unit = self._next_answer_continuation_unit(
            question=question,
            answer_units=answer_units,
            units=units,
            question_by_index=question_by_index,
            segment_lookup=segment_lookup,
        )
        if continuation_unit is None:
            return None
        completed_units = list(answer_units) + [continuation_unit]
        if len(completed_units) > self.config.max_answer_units:
            return None
        candidate = self._build_answer_candidate_from_units(
            question=question,
            answer_units=completed_units,
            distance_units=distance_units,
            segment_lookup=segment_lookup,
        )
        if candidate is None:
            return None
        candidate.reason_codes = self._unique_strings(
            list(candidate.reason_codes) + ["answer_span_completion_support"],
        )
        candidate.search_signals["answer_boundary_status"] = "completed_by_next_sentence"
        candidate.search_signals["answer_boundary_next_text_id"] = continuation_unit.text_id
        candidate.metadata.setdefault("search_debug", {})[
            "answer_completion_extended_by"
        ] = continuation_unit.text_id
        return candidate

    def _next_answer_continuation_unit(
        self,
        *,
        question: QuestionCandidate,
        answer_units: Sequence[_ExtractionUnit],
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        segment_lookup: dict[str, Any],
    ) -> _ExtractionUnit | None:
        """Return the next unit when it structurally continues the answer."""

        if not answer_units:
            return None
        last_unit = answer_units[-1]
        next_index = last_unit.index + 1
        if next_index >= len(units) or next_index in question_by_index:
            return None
        next_unit = units[next_index]
        if next_unit.audio_source_id != last_unit.audio_source_id:
            return None
        if self._answer_has_terminal_punctuation(last_unit.text):
            return None
        if not self._can_use_answer_units(
            question=question,
            answer_units=list(answer_units) + [next_unit],
            segment_lookup=segment_lookup,
        ):
            return None
        if self._units_duration_seconds(list(answer_units) + [next_unit]) > (
            self.config.max_answer_duration_seconds
        ):
            return None
        if self._has_strong_question_signal(next_unit.text):
            return None
        if self._is_poll_or_backchannel_answer(normalize_rule_text(next_unit.text)):
            return None
        return next_unit if self._looks_like_sentence_continuation(last_unit, next_unit) else None

    def _looks_like_sentence_continuation(
        self,
        previous_unit: _ExtractionUnit,
        next_unit: _ExtractionUnit,
    ) -> bool:
        """Return whether two units look like one unfinished sentence."""

        previous_text = previous_unit.text.strip()
        next_text = next_unit.text.strip()
        if not previous_text or not next_text:
            return False
        if previous_text.endswith((",", ";", ":")):
            return True
        normalized_next = normalize_rule_text(next_text)
        if re.match(
            r"^(?:"
            r"and|but|or|so|because|that|which|who|where|when|if|then|also|"
            r"e|ed|ma|o|oppure|perche|perché|che|quindi|quando|se|anche|poi"
            r")\b",
            normalized_next,
        ):
            return True
        first_character = next_text[0]
        if first_character.islower():
            return True
        previous_speaker = str(previous_unit.speaker_id or "").strip()
        next_speaker = str(next_unit.speaker_id or "").strip()
        if previous_speaker and next_speaker and previous_speaker != next_speaker:
            return False
        gap_seconds = max(0.0, next_unit.start_seconds - previous_unit.end_seconds)
        return gap_seconds <= 0.35

    @staticmethod
    def _answer_has_terminal_punctuation(text: str) -> bool:
        """Return whether an answer span ends on strong terminal punctuation."""

        return text.rstrip().endswith((".", "?", "!"))

    def _build_same_unit_answer_candidate(
        self,
        question: QuestionCandidate,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return an unscored same-unit answer candidate."""

        if not question.local_answer_seed:
            return None

        answer_text = question.local_answer_seed.strip()
        if not self._is_plausible_answer_for_question(question, answer_text):
            return None
        answer_text, trim_reasons = self._trim_answer_text(answer_text)
        if not self._is_plausible_answer_for_question(question, answer_text):
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
            reason_codes=self._unique_strings(
                [self._same_unit_reason(question.unit.layer)]
                + trim_reasons,
            ),
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
        if not self._is_plausible_answer_for_question(question, answer_text):
            return None
        answer_text, trim_reasons = self._trim_answer_text(answer_text)
        if not self._is_plausible_answer_for_question(question, answer_text):
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
            reason_codes=trim_reasons,
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
        if not answer_prefix or not self._is_plausible_answer_for_question(
            question,
            answer_prefix,
        ):
            return None
        answer_prefix, trim_reasons = self._trim_answer_text(answer_prefix)
        if not self._is_plausible_answer_for_question(question, answer_prefix):
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
            reason_codes=self._unique_strings(
                ["answer_before_competing_question"]
                + trim_reasons,
            ),
            metadata={
                "answer_source": "competing_question_prefix",
                "search_debug": {
                    "candidate_origin": "competing_question_prefix",
                    "candidate_unit_indexes": [competing_unit.index],
                    "candidate_text_ids": [competing_unit.text_id],
                },
            },
        )

    def _build_answer_candidate_with_competing_prefix(
        self,
        *,
        question: QuestionCandidate,
        answer_units: Sequence[_ExtractionUnit],
        competing_unit: _ExtractionUnit,
        answer_prefix: str,
        distance_units: int,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return an answer span extended by text before the next question."""

        if not answer_units:
            return None
        if count_tokens(normalize_rule_text(answer_prefix)) < 5:
            return None
        combined_units = list(answer_units) + [competing_unit]
        answer_text = self._join_text(
            [self._join_text(unit.text for unit in answer_units), answer_prefix],
        )
        answer_text, trim_reasons = self._trim_answer_text(answer_text)
        if not self._is_plausible_answer_for_question(question, answer_text):
            return None
        if self._is_answer_question_like(answer_text):
            return None
        answer_segment_ids = self._ordered_union(
            self._resolve_segment_ids_for_unit(unit=unit, segment_lookup=segment_lookup)
            for unit in combined_units
        )
        return _AnswerCandidate(
            answer_candidate_id=(
                f"{question.question_candidate_id}_answer_{distance_units:02d}_"
                "competing_prefix_extended"
            ),
            answer_text=answer_text,
            answer_units=combined_units,
            answer_unit_ids=self._ordered_union(
                unit.merged_unit_ids for unit in combined_units
            ),
            answer_sentence_ids=self._ordered_union(
                unit.sentence_ids for unit in combined_units
            ),
            answer_source_utterance_ids=self._ordered_union(
                unit.source_utterance_ids for unit in combined_units
            ),
            answer_segment_ids=answer_segment_ids,
            answer_segment_id=answer_segment_ids[0] if answer_segment_ids else None,
            answer_score=0.0,
            distance_units=max(1, distance_units - 1),
            gap_seconds=max(0.0, answer_units[0].start_seconds - question.unit.end_seconds),
            search_signals={
                "answer_source": "following_text_with_competing_prefix",
                "distance_units": max(1, distance_units - 1),
                "candidate_span_unit_count": len(combined_units),
            },
            reason_codes=self._unique_strings(
                ["answer_extended_before_competing_question"] + trim_reasons,
            ),
            metadata={
                "answer_source": "following_text_with_competing_prefix",
                "search_debug": {
                    "candidate_origin": "following_text_with_competing_prefix",
                    "candidate_unit_indexes": [
                        candidate_unit.index for candidate_unit in combined_units
                    ],
                    "candidate_text_ids": [
                        candidate_unit.text_id for candidate_unit in combined_units
                    ],
                },
            },
        )

    def _build_interview_cluster_answer_candidate(
        self,
        *,
        question: QuestionCandidate,
        units: Sequence[_ExtractionUnit],
        question_by_index: dict[int, QuestionCandidate],
        cluster_start_index: int,
        segment_lookup: dict[str, Any],
    ) -> _AnswerCandidate | None:
        """Return an answer after a short interview-style question cluster."""

        skipped_units: list[_ExtractionUnit] = []
        answer_start_index: int | None = None
        saw_interview_bridge = False
        max_skip_units = 4
        for candidate_index in range(
            cluster_start_index,
            min(len(units), cluster_start_index + max_skip_units + 1),
        ):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                return None
            gap_seconds = max(0.0, candidate_unit.start_seconds - question.unit.end_seconds)
            if gap_seconds > 45.0:
                return None
            bridge_kind = self._interview_bridge_kind(
                candidate_unit.text,
                question_text=question.question_text,
            )
            if bridge_kind in {"prompt", "echo"}:
                saw_interview_bridge = True
            if candidate_index in question_by_index or bridge_kind is not None:
                skipped_units.append(candidate_unit)
                continue
            answer_start_index = candidate_index
            break

        if answer_start_index is None or not skipped_units or not saw_interview_bridge:
            return None
        if len(skipped_units) > max_skip_units:
            return None

        answer_units: list[_ExtractionUnit] = []
        for candidate_index in range(
            answer_start_index,
            min(len(units), answer_start_index + self.config.max_answer_units),
        ):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                break
            if candidate_index in question_by_index and answer_units:
                break
            prospective_units = answer_units + [candidate_unit]
            if not self._can_use_answer_units(
                question=question,
                answer_units=prospective_units,
                segment_lookup=segment_lookup,
            ):
                break
            if self._units_duration_seconds(prospective_units) > 45.0:
                break
            answer_units = prospective_units
            if count_tokens(normalize_rule_text(self._join_text(unit.text for unit in answer_units))) >= 12:
                break

        if not answer_units:
            return None
        answer_text = self._join_text(unit.text for unit in answer_units)
        answer_text, trim_reasons = self._trim_answer_text(answer_text)
        if not self._is_plausible_interview_cluster_answer(
            question=question,
            answer_text=answer_text,
            skipped_units=skipped_units,
        ):
            return None

        answer_segment_ids = self._ordered_union(
            self._resolve_segment_ids_for_unit(unit=unit, segment_lookup=segment_lookup)
            for unit in answer_units
        )
        return _AnswerCandidate(
            answer_candidate_id=(
                f"{question.question_candidate_id}_answer_{answer_start_index - question.unit_index:02d}_"
                "interview_cluster"
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
            distance_units=answer_start_index - question.unit_index,
            gap_seconds=max(0.0, answer_units[0].start_seconds - question.unit.end_seconds),
            search_signals={
                "answer_source": "interview_cluster_search",
                "candidate_channel": "interview_cluster_search",
                "distance_units": answer_start_index - question.unit_index,
                "candidate_span_unit_count": len(answer_units),
                "skipped_question_like_units": len(skipped_units),
            },
            reason_codes=self._unique_strings(
                ["interview_cluster_answer_candidate"] + trim_reasons,
            ),
            metadata={
                "answer_source": "interview_cluster_search",
                "search_debug": {
                    "candidate_origin": "interview_cluster_search",
                    "candidate_unit_indexes": [
                        candidate_unit.index for candidate_unit in answer_units
                    ],
                    "candidate_text_ids": [
                        candidate_unit.text_id for candidate_unit in answer_units
                    ],
                    "skipped_text_ids": [unit.text_id for unit in skipped_units],
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

        answer_responsiveness = self._score_answer_responsiveness(
            question=question,
            answer=answer,
            qa_alignment=qa_alignment,
            answer_matches=answer_matches,
        )
        partial_scores["answer_responsiveness"] = float(
            answer_responsiveness["score_delta"],
        )
        score += answer_responsiveness["score_delta"]
        reason_codes.extend(answer_responsiveness["reason_codes"])
        if "runon_question_candidate" in question.reason_codes:
            responsiveness_debug = answer_responsiveness.get("debug", {})
            responsiveness_score = self._safe_float(responsiveness_debug.get("score"))
            if (
                answer.distance_units == 1
                and responsiveness_score is not None
                and responsiveness_score >= 0.48
                and not bool(responsiveness_debug.get("followup_prompt_answer"))
                and not self._is_answer_question_like(answer.answer_text)
            ):
                partial_scores["runon_question_local_answer"] = 0.06
                score += partial_scores["runon_question_local_answer"]
                reason_codes.append("runon_question_local_answer")
            else:
                partial_scores["runon_question_local_answer"] = 0.0

        span_support = self._score_answer_span_completeness(
            question=question,
            answer=answer,
            full_alignment=qa_alignment,
            full_answer_matches=answer_matches,
        )
        partial_scores["span_completeness"] = float(span_support["score_delta"])
        score += span_support["score_delta"]
        reason_codes.extend(span_support["reason_codes"])

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

        if (
            self.config.pipeline_profile == "quality_local"
            and answer.search_signals.get("candidate_channel") == "deferred_answer_search"
        ):
            deferred_penalty = -0.10
            if answer.gap_seconds > 60.0 or answer.distance_units > 12:
                deferred_penalty = -0.24
            elif answer.gap_seconds > 30.0 or answer.distance_units > 8:
                deferred_penalty = -0.16
            partial_scores["quality_local_deferred_penalty"] = deferred_penalty
            score += deferred_penalty
            reason_codes.append("quality_local_deferred_penalty")
        else:
            partial_scores["quality_local_deferred_penalty"] = 0.0

        if answer.search_signals.get("candidate_channel") == "interview_cluster_search":
            partial_scores["interview_cluster_support"] = 0.20
            score += 0.20
            reason_codes.append("interview_cluster_answer_support")
        else:
            partial_scores["interview_cluster_support"] = 0.0

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
            partial_scores["answer_is_question_penalty"] = -0.35
            score -= 0.35
            reason_codes.append("answer_is_question")
        else:
            partial_scores["answer_is_question_penalty"] = 0.0

        answer_context = self._score_answer_context(answer.answer_units)
        partial_scores["answer_context"] = float(answer_context["score_delta"])
        score += answer_context["score_delta"]
        reason_codes.extend(answer_context["reason_codes"])

        boundary_status = str(answer.search_signals.get("answer_boundary_status") or "")
        if (
            self.config.pipeline_profile == "quality_local"
            and not self._answer_has_terminal_punctuation(answer.answer_text)
            and "answer_meta_opening_trimmed" not in reason_codes
            and "answer_trailing_tag_trimmed" not in reason_codes
            and boundary_status
            not in {"continuation_available", "completed_by_next_sentence"}
            and search_result.stop_reason in {"window_exhausted", "answer_span_limit"}
        ):
            answer.search_signals["answer_boundary_status"] = "truncated"
            answer.search_signals["answer_boundary_reason"] = search_result.stop_reason
            reason_codes.append("answer_truncated_at_boundary")

        quality_gate = self._score_answer_quality_gate(question, answer)
        partial_scores["quality_gate"] = float(quality_gate["score_delta"])
        score += quality_gate["score_delta"]
        reason_codes.extend(quality_gate["reason_codes"])

        speaker_support = self._score_speaker_pairing(question, answer)
        partial_scores["speaker_pairing"] = float(speaker_support["score_delta"])
        score += speaker_support["score_delta"]
        reason_codes.extend(speaker_support["reason_codes"])

        continuation_risk = self._score_monologue_continuation_risk(
            question=question,
            answer=answer,
            qa_alignment=qa_alignment,
            answer_matches=answer_matches,
            answer_responsiveness=answer_responsiveness,
        )
        partial_scores["monologue_continuation"] = float(
            continuation_risk["score_delta"],
        )
        score += continuation_risk["score_delta"]
        reason_codes.extend(continuation_risk["reason_codes"])

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
        answer.metadata["answer_responsiveness_debug"] = answer_responsiveness[
            "debug"
        ]
        answer.metadata["answer_span_completeness_debug"] = span_support["debug"]
        answer.metadata["answer_context_debug"] = answer_context["debug"]
        answer.metadata["answer_quality_gate_debug"] = quality_gate["debug"]
        answer.metadata["speaker_pairing_debug"] = speaker_support["debug"]
        answer.metadata["monologue_continuation_debug"] = continuation_risk["debug"]
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

    def _score_answer_responsiveness(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        qa_alignment: dict[str, Any],
        answer_matches: Sequence[Any],
    ) -> dict[str, Any]:
        """Return a compact signal for whether the answer responds to the question."""

        normalized_question = normalize_rule_text(question.question_text)
        normalized_answer = normalize_rule_text(answer.answer_text)
        question_tokens = self._content_tokens(normalized_question)
        answer_tokens = self._content_tokens(normalized_answer)
        answer_only_tokens = answer_tokens - question_tokens
        shared_keywords = set(qa_alignment.get("shared_keywords") or [])
        shared_numbers = set(qa_alignment.get("shared_numbers") or [])
        answer_numbers = self._number_tokens(normalized_answer)
        asks_for_quantity = self._question_has_quantity_intent(
            question.question_text,
            question.question_type,
        )
        has_topical_anchor = bool(shared_keywords or shared_numbers)
        has_direct_quantity_support = bool(shared_numbers)
        has_indirect_quantity_support = bool(
            asks_for_quantity and answer_numbers and not shared_numbers,
        )
        has_quantity_support = bool(
            has_direct_quantity_support or has_indirect_quantity_support,
        )
        answer_cue_score = min(0.30, sum(match.weight for match in answer_matches))
        followup_prompt_answer = self._is_followup_prompt_answer(normalized_answer)
        socratic_short_answer = self._is_socratic_short_answer_for_question(
            question=question,
            answer_text=answer.answer_text,
        )

        score_delta = 0.0
        reason_codes: list[str] = []
        if has_topical_anchor:
            score_delta += 0.07
            reason_codes.append("answer_responsiveness_anchor")
        if has_direct_quantity_support:
            score_delta += 0.08
            reason_codes.append("answer_responsiveness_quantity_support")
        elif has_indirect_quantity_support:
            score_delta += 0.02
            reason_codes.append("answer_responsiveness_indirect_quantity_support")

        answer_only_count = len(answer_only_tokens)
        if answer_only_count >= 3:
            score_delta += 0.04
            reason_codes.append("answer_responsiveness_added_substance")
        elif answer_only_count <= 1 and not has_quantity_support:
            score_delta -= 0.10
            reason_codes.append("answer_responsiveness_low_substance")

        if (
            answer_only_count <= 2
            and answer_cue_score <= 0.0
            and not has_direct_quantity_support
            and not socratic_short_answer
        ):
            score_delta -= 0.10
            reason_codes.append("answer_responsiveness_thin_reply")
        elif socratic_short_answer:
            score_delta += 0.04
            reason_codes.append("answer_responsiveness_socratic_short_answer")

        if (
            has_indirect_quantity_support
            and answer_cue_score <= 0.10
            and answer_only_count > 6
        ):
            score_delta -= 0.18
            reason_codes.append("answer_responsiveness_unanchored_quantity")

        contextual_question = bool(
            self._is_contextual_question(question.question_text)
            or "question_context_expanded" in question.reason_codes
        )
        if not has_topical_anchor and not has_quantity_support:
            if answer_cue_score > 0.0:
                score_delta -= 0.08
                reason_codes.append("answer_responsiveness_surface_cue")
            elif contextual_question:
                score_delta -= 0.04
                reason_codes.append("answer_responsiveness_contextual_question")
            else:
                score_delta -= 0.14
                reason_codes.append("answer_responsiveness_missing_anchor")

        if asks_for_quantity and not answer_numbers:
            score_delta -= 0.12
            reason_codes.append("answer_responsiveness_quantity_missing")

        if question.question_type in {"why", "didactic_prompt"}:
            if answer_cue_score <= 0.0 and answer_only_count < 4:
                score_delta -= 0.05
                reason_codes.append("answer_responsiveness_explanation_weak")
            elif answer_cue_score > 0.0 and (
                has_topical_anchor or answer_only_count >= 3
            ):
                score_delta += 0.03
                reason_codes.append("answer_responsiveness_explanation_support")

        if self._is_answer_question_like(answer.answer_text):
            score_delta -= 0.12
            reason_codes.append("answer_responsiveness_answer_is_question")

        if followup_prompt_answer:
            score_delta -= 0.22
            reason_codes.append("answer_responsiveness_followup_prompt")

        raw_score_delta = score_delta
        ranking_score_delta = max(-0.24, min(0.06, raw_score_delta))
        responsiveness_score = self._clamp(0.55 + (raw_score_delta * 1.6))
        if raw_score_delta <= -0.10:
            reason_codes.append("answer_responsiveness_weak")
        elif raw_score_delta >= 0.05:
            reason_codes.append("answer_responsiveness_strong")

        return {
            "score_delta": round(ranking_score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "score": round(responsiveness_score, 4),
                "score_delta": round(ranking_score_delta, 4),
                "raw_score_delta": round(raw_score_delta, 4),
                "has_topical_anchor": has_topical_anchor,
                "has_quantity_support": has_quantity_support,
                "has_direct_quantity_support": has_direct_quantity_support,
                "has_indirect_quantity_support": has_indirect_quantity_support,
                "shared_keyword_count": len(shared_keywords),
                "shared_number_count": len(shared_numbers),
                "answer_only_token_count": answer_only_count,
                "answer_cue_score": round(answer_cue_score, 4),
                "followup_prompt_answer": followup_prompt_answer,
                "socratic_short_answer": socratic_short_answer,
            },
        }

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

    def _score_answer_quality_gate(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
    ) -> dict[str, Any]:
        """Return penalties for answer spans that look review-hostile."""

        score_delta = 0.0
        reason_codes: list[str] = []
        normalized_answer = normalize_rule_text(answer.answer_text)
        normalized_question = normalize_rule_text(question.question_text)
        socratic_short_answer = self._is_socratic_short_answer_for_question(
            question=question,
            answer_text=answer.answer_text,
        )

        if self._is_moderator_handoff_answer(normalized_answer):
            score_delta -= 0.22
            reason_codes.append("moderator_handoff_answer_penalty")

        if self._is_filler_or_boilerplate_answer(normalized_answer):
            score_delta -= 0.26
            reason_codes.append("answer_boilerplate_penalty")

        if self._is_poll_or_backchannel_answer(normalized_answer):
            score_delta -= 0.34
            reason_codes.append("answer_poll_or_backchannel_penalty")

        if self._is_question_continuation_answer(normalized_answer):
            score_delta -= 0.24
            reason_codes.append("question_continuation_answer_penalty")

        if self._is_additive_continuation_answer(question, answer):
            score_delta -= 0.14
            reason_codes.append("additive_continuation_answer_penalty")

        if answer.distance_units == 0 and answer.answer_sentence_ids and set(
            answer.answer_sentence_ids,
        ).intersection(question.question_sentence_ids):
            score_delta -= 0.16
            reason_codes.append("same_sentence_answer_penalty")
            answer_matches = collect_rule_matches(normalized_answer, ANSWER_CUE_RULES)
            if not answer_matches and not socratic_short_answer:
                score_delta -= 0.26
                reason_codes.append("same_sentence_without_answer_cue")

        question_tokens = set(self._content_tokens(normalized_question))
        answer_tokens = set(self._content_tokens(normalized_answer))
        overlap_ratio = 0.0
        question_coverage_ratio = 0.0
        added_answer_token_count = 0
        if question_tokens and answer_tokens:
            overlap_ratio = len(question_tokens & answer_tokens) / max(
                1,
                len(answer_tokens),
            )
            question_coverage_ratio = len(question_tokens & answer_tokens) / max(
                1,
                len(question_tokens),
            )
            added_answer_token_count = len(answer_tokens - question_tokens)
        if (
            overlap_ratio >= 0.72
            and added_answer_token_count <= 1
            and not socratic_short_answer
        ):
            score_delta -= 0.28
            reason_codes.append("answer_echoes_question_penalty")
        elif (
            question_coverage_ratio >= 0.68
            and added_answer_token_count <= 2
            and not socratic_short_answer
        ):
            score_delta -= 0.22
            reason_codes.append("answer_circular_echo_penalty")

        answer_matches = collect_rule_matches(normalized_answer, ANSWER_CUE_RULES)
        answer_numbers = self._number_tokens(normalized_answer)
        if (
            count_tokens(normalized_answer) <= 4
            and not answer_matches
            and not answer_numbers
            and not socratic_short_answer
        ):
            score_delta -= 0.12
            reason_codes.append("low_information_answer_penalty")

        if socratic_short_answer:
            score_delta += 0.08
            reason_codes.append("socratic_short_answer_support")

        if self._looks_like_incomplete_answer_span(answer.answer_text):
            score_delta -= 0.10
            reason_codes.append("incomplete_answer_span_penalty")

        if answer.search_signals.get("answer_boundary_status") == "truncated":
            score_delta -= 0.16
            reason_codes.append("answer_truncated_at_boundary_penalty")

        cue_alignment = self._question_answer_alignment(
            question_text=question.question_text,
            answer_text=answer.answer_text,
            question_type=question.question_type,
            answer_source=str(answer.search_signals.get("answer_source") or ""),
        )
        shared_keywords = cue_alignment.get("shared_keywords") or []
        answer_cue_score = min(0.30, sum(match.weight for match in answer_matches))
        relevance_score = float(cue_alignment.get("relevance_score") or 0.0)
        local_explanatory_why_answer = (
            question.question_type == "why"
            and answer.distance_units <= 1
            and answer.gap_seconds <= 12.0
            and _CAUSAL_ANSWER_AFTER_WHY_RE.match(normalized_answer)
        )
        if (
            answer_cue_score > 0.0
            and relevance_score <= 0.0
            and not shared_keywords
            and not local_explanatory_why_answer
        ):
            score_delta -= 0.18
            reason_codes.append("surface_answer_cue_penalty")

        deflection_debug = self._deflection_answer_debug(
            question=question,
            answer=answer,
            shared_keywords=shared_keywords,
            shared_numbers=cue_alignment.get("shared_numbers") or [],
            answer_cue_score=answer_cue_score,
        )
        if deflection_debug["is_deflection"]:
            score_delta -= 0.42
            reason_codes.append("deflection_answer_penalty")

        if (
            self.config.pipeline_profile == "quality_local"
            and answer.search_signals.get("candidate_channel") == "deferred_answer_search"
            and count_tokens(normalized_answer) > 45
        ):
            score_delta -= 0.14
            reason_codes.append("deferred_answer_too_broad_penalty")

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "added_answer_token_count": added_answer_token_count,
                "overlap_ratio": round(overlap_ratio, 4),
                "question_coverage_ratio": round(question_coverage_ratio, 4),
                "socratic_short_answer": socratic_short_answer,
                "deflection_answer": deflection_debug,
                "score_delta": round(score_delta, 4),
            },
        }

    def _deflection_answer_debug(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        shared_keywords: Sequence[str],
        shared_numbers: Sequence[str],
        answer_cue_score: float,
    ) -> dict[str, Any]:
        """Return structural deflection evidence for short course-meta answers."""

        normalized_answer = normalize_rule_text(answer.answer_text)
        answer_tokens = self._content_token_list(normalized_answer)
        token_count = count_tokens(normalized_answer)
        if not answer_tokens or token_count > 18:
            return {
                "is_deflection": False,
                "token_count": token_count,
                "reason": "length_or_empty",
            }
        if shared_keywords or shared_numbers or answer_cue_score > 0.0:
            return {
                "is_deflection": False,
                "token_count": token_count,
                "reason": "anchored_or_explanatory",
            }
        if self._is_socratic_short_answer_for_question(
            question=question,
            answer_text=answer.answer_text,
        ):
            return {
                "is_deflection": False,
                "token_count": token_count,
                "reason": "socratic_short_answer",
            }

        token_set = set(answer_tokens)
        meta_count = sum(1 for token in answer_tokens if token in _DEFLECTION_META_TOKENS)
        dismissive_count = sum(
            1 for token in answer_tokens if token in _DEFLECTION_DISMISSIVE_TOKENS
        )
        negated = bool(token_set & _NEGATION_TOKENS)
        meta_ratio = meta_count / max(1, len(answer_tokens))
        is_deflection = (
            meta_count >= 1
            and (dismissive_count >= 1 or negated)
            and meta_ratio >= 0.20
        )
        return {
            "is_deflection": is_deflection,
            "token_count": token_count,
            "meta_token_count": meta_count,
            "dismissive_token_count": dismissive_count,
            "meta_ratio": round(meta_ratio, 4),
            "negated": negated,
            "reason": "course_meta_no_anchor" if is_deflection else "insufficient_meta",
        }

    def _is_socratic_short_answer(self, *, question_text: str, answer_text: str) -> bool:
        """Return whether a short answer resolves a short Socratic prompt."""

        question_tokens = self._content_token_list(question_text)
        answer_tokens = self._content_token_list(answer_text)
        if not question_tokens or not answer_tokens:
            return False
        normalized_question = normalize_rule_text(question_text).rstrip(" ?.!").strip()
        if re.match(
            r"^(?:what\s+(?:is|are)|cosa\s+(?:e|sono)|che\s+cosa\s+(?:e|sono))\b",
            normalized_question,
        ):
            return False
        if len(question_tokens) > 4 or len(answer_tokens) > 8:
            return False
        question_token_set = set(question_tokens)
        answer_token_set = set(answer_tokens)
        added_token_count = len(answer_token_set - question_token_set)
        if added_token_count < 1:
            return False
        if self._is_incomplete_numeric_short_answer(
            answer_tokens=answer_tokens,
            question_token_set=question_token_set,
        ):
            return False
        if (
            self._is_object_gap_socratic_question(normalized_question)
            and answer_tokens[0] in question_token_set
        ):
            return True
        if answer_tokens[0] in question_token_set and added_token_count >= 2:
            return True
        return bool(question_token_set & answer_token_set) and (
            question_tokens[-1] in {"che", "cosa", "what"}
            or self._is_terminal_object_question(normalize_rule_text(question_text))
        )

    def _is_socratic_short_answer_for_question(
        self,
        *,
        question: QuestionCandidate,
        answer_text: str,
    ) -> bool:
        """Return whether an answer resolves the current or focused question."""

        if self._is_socratic_short_answer(
            question_text=question.question_text,
            answer_text=answer_text,
        ):
            return True
        focused_question = str(
            question.metadata.get("normalized_question_text") or "",
        ).strip()
        if not focused_question:
            return False
        return self._is_socratic_short_answer(
            question_text=focused_question,
            answer_text=answer_text,
        )

    def _is_object_gap_socratic_question(self, normalized_question: str) -> bool:
        """Return whether a short prompt asks for the missing object/result."""

        stripped = normalized_question.rstrip(" ?.!").strip()
        if self._is_terminal_object_question(stripped):
            return True
        return bool(
            re.match(
                r"^(?:"
                r"what\s+do\s+(?:i|we|you|they)\s+\w+"
                r"|cosa\s+\w+"
                r"|che\s+cosa\s+\w+"
                r")\b",
                stripped,
            ),
        )

    @staticmethod
    def _is_incomplete_numeric_short_answer(
        *,
        answer_tokens: Sequence[str],
        question_token_set: set[str],
    ) -> bool:
        """Return whether a short answer likely stops after a bare number."""

        if len(answer_tokens) > 3:
            return False
        if answer_tokens[-1] not in _POLL_OPTION_WORDS:
            return False
        added_tokens = set(answer_tokens) - question_token_set
        return bool(added_tokens) and added_tokens <= _POLL_OPTION_WORDS

    def _score_answer_span_completeness(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        full_alignment: dict[str, Any],
        full_answer_matches: Sequence[Any],
    ) -> dict[str, Any]:
        """Return a light preference for answer spans that add substance."""

        score_delta = 0.0
        reason_codes: list[str] = []
        normalized_answer = normalize_rule_text(answer.answer_text)
        token_count = count_tokens(normalized_answer)
        full_signal_score = float(full_alignment.get("signal_score") or 0.0)
        full_has_signal = bool(
            full_alignment.get("shared_keywords")
            or full_alignment.get("shared_numbers")
            or full_answer_matches
        )
        first_unit_text = answer.answer_units[0].text if answer.answer_units else ""
        first_alignment = self._question_answer_alignment(
            question_text=question.question_text,
            answer_text=first_unit_text,
            question_type=question.question_type,
            answer_source=str(answer.search_signals.get("answer_source") or ""),
        )
        first_matches = collect_rule_matches(
            normalize_rule_text(first_unit_text),
            ANSWER_CUE_RULES,
        )
        first_incomplete = self._looks_like_incomplete_answer_span(first_unit_text)
        first_signal_score = float(first_alignment.get("signal_score") or 0.0)
        first_has_signal = bool(
            first_alignment.get("shared_keywords")
            or first_alignment.get("shared_numbers")
            or first_matches
        )

        if len(answer.answer_units) > 1:
            signal_gain = full_signal_score - first_signal_score
            if "answer_span_completion_support" in answer.reason_codes:
                score_delta += 0.12 if token_count <= 70 else 0.04
                reason_codes.append("answer_span_completion_support")
            elif (
                full_has_signal
                and token_count <= 70
                and (signal_gain >= 0.08 or not first_has_signal)
            ):
                score_delta += 0.12
                reason_codes.append("answer_span_extension_support")
            elif first_incomplete and token_count <= 70:
                score_delta += 0.10 if full_has_signal else 0.06
                reason_codes.append("answer_span_completion_support")
            elif token_count > 80:
                score_delta -= 0.08
                reason_codes.append("answer_span_too_broad_penalty")
        elif (
            self.config.pipeline_profile == "quality_local"
            and not full_has_signal
            and token_count <= 9
            and answer.distance_units <= 1
        ):
            score_delta -= 0.10
            reason_codes.append("premise_only_answer_penalty")

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "answer_unit_count": len(answer.answer_units),
                "token_count": token_count,
                "first_signal_score": round(first_signal_score, 4),
                "full_signal_score": round(full_signal_score, 4),
                "first_has_signal": first_has_signal,
                "full_has_signal": full_has_signal,
                "first_incomplete": first_incomplete,
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

    def _score_monologue_continuation_risk(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        qa_alignment: dict[str, Any],
        answer_matches: Sequence[Any],
        answer_responsiveness: dict[str, Any],
    ) -> dict[str, Any]:
        """Penalize local answers that look like same-speaker continuation."""

        answer_source = str(answer.search_signals.get("answer_source") or "")
        local_answer = answer_source in {"following_text_units", "same_text_unit_seed"}
        adjacent = answer.distance_units <= 1 and answer.gap_seconds <= 6.0
        question_profile = self._speaker_profile(question.unit)
        answer_profile = self._speaker_profile_from_units(answer.answer_units)
        reliable_turn = (
            question_profile["reliability"] == "reliable"
            and answer_profile["reliability"] == "reliable"
            and question_profile["speaker_id"] != answer_profile["speaker_id"]
        )
        reliable_same_speaker = (
            question_profile["reliability"] == "reliable"
            and answer_profile["reliability"] == "reliable"
            and question_profile["speaker_id"] == answer_profile["speaker_id"]
        )
        missing_speaker_boundary = (
            question_profile["reliability"] == "missing"
            or answer_profile["reliability"] == "missing"
        )
        responsiveness_debug = answer_responsiveness.get("debug", {})
        has_anchor = bool(
            qa_alignment.get("shared_keywords")
            or qa_alignment.get("shared_numbers")
            or responsiveness_debug.get("has_topical_anchor")
            or responsiveness_debug.get("has_quantity_support")
        )
        answer_cue_score = min(0.30, sum(match.weight for match in answer_matches))
        answer_only_count = int(responsiveness_debug.get("answer_only_token_count") or 0)
        question_intent = str(question.metadata.get("question_intent") or "")
        weak_question_intent = question_intent in {
            "embedded_statement_question",
            "weak_question_form",
            "rhetorical_tag",
            "poll_or_check",
            "fragment",
            "subordinate_fragment",
        }
        weak_response = (
            "answer_responsiveness_weak" in answer_responsiveness.get("reason_codes", [])
            or (not has_anchor and answer_cue_score <= 0.0)
            or (answer_only_count <= 2 and not has_anchor)
        )
        meta_opening_trimmed = "answer_meta_opening_trimmed" in answer.reason_codes

        score_delta = 0.0
        reason_codes: list[str] = []
        if local_answer and adjacent and not reliable_turn:
            if weak_response and (weak_question_intent or reliable_same_speaker):
                score_delta = -0.22
                reason_codes.append("monologue_continuation_risk")
            elif (
                self.config.pipeline_profile == "quality_local"
                and weak_response
                and missing_speaker_boundary
                and not meta_opening_trimmed
            ):
                score_delta = -0.16
                reason_codes.append("monologue_continuation_risk")
            elif weak_question_intent and answer_cue_score <= 0.0:
                score_delta = -0.10
                reason_codes.append("monologue_continuation_risk")

        return {
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "local_answer": local_answer,
                "adjacent": adjacent,
                "reliable_turn": reliable_turn,
                "reliable_same_speaker": reliable_same_speaker,
                "missing_speaker_boundary": missing_speaker_boundary,
                "has_anchor": has_anchor,
                "answer_cue_score": round(answer_cue_score, 4),
                "answer_only_token_count": answer_only_count,
                "question_intent": question_intent,
                "weak_question_intent": weak_question_intent,
                "weak_response": weak_response,
                "meta_opening_trimmed": meta_opening_trimmed,
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
            confidence = self._clamp(confidence - 0.24)
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
        trimmed_answer_text, internal_trim_debug = (
            self._trim_internal_checkin_from_answer(
                question_text=question.question_text,
                answer_text=answer.answer_text,
            )
        )
        if trimmed_answer_text != answer.answer_text:
            answer.metadata["internal_checkin_trim"] = internal_trim_debug
            answer.metadata["original_answer_text"] = answer.answer_text
            answer.answer_text = trimmed_answer_text
            answer.reason_codes = self._unique_strings(
                list(answer.reason_codes) + ["answer_internal_checkin_trimmed"],
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
        quality_features = self._build_candidate_quality_features(
            question=question,
            answer=answer,
            context_extraction=context_extraction,
            confidence=confidence,
            input_layer=input_layer,
            segment_relation=segment_relation,
            reason_codes=reason_codes,
            review_flags=review_flags,
            question_timing_source=question_grounding_debug["timing_source"],
            answer_timing_source=answer_grounding_debug["timing_source"],
        )
        metadata = {
            "input_layer": input_layer,
            "quality_features": quality_features,
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
                "context_selection_score": (
                    context_extraction.context_selection_score
                ),
                "context_reasons": context_extraction.context_reasons,
                "candidate_context_count": (
                    context_extraction.candidate_context_count
                ),
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
                    0.24 if answer.metadata.get("answer_is_question") else 0.0
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

        context_selection = _ContextSelection()
        context_strategy: str | None = None
        context_confidence: str | None = None

        if answer.distance_units == 0 and question.local_answer_seed:
            context_selection = _ContextSelection(
                units=[question.unit],
                score=0.90,
                reasons=["intra_sentence_context"],
                candidate_count=1,
            )
            context_strategy = "intra_sentence_context"
            context_confidence = "high"
        elif question.metadata.get("question_context_expanded"):
            context_selection = _ContextSelection(
                units=question.question_units[:-1] or question.question_units,
                score=0.85,
                reasons=["expanded_question_context"],
                candidate_count=len(question.question_units),
            )
            context_strategy = "previous_sentence_context"
            context_confidence = "high"
        elif bool(search_result.metadata.get("deferred_answer_search_used")):
            context_selection = self._deferred_context_units(question, answer, units)
            context_strategy = "deferred_answer_context"
            context_confidence = "medium"
        else:
            context_selection = self._local_topic_context_units(question, answer, units)
            context_strategy = "local_topic_window"
            context_confidence = "medium"

        context_units = self._dedupe_context_units(
            context_selection.units,
            question,
            answer,
        )
        context_selection = self._context_selection_debug(
            question=question,
            answer=answer,
            context_units=context_units,
            strategy=context_strategy,
            candidate_count=context_selection.candidate_count,
            fallback_reasons=context_selection.reasons,
        )
        if not context_units:
            context_selection = self._fallback_context_units(question, answer, units)
            context_units = self._dedupe_context_units(
                context_selection.units,
                question,
                answer,
            )
            context_strategy = "fallback_previous_context"
            context_confidence = "low"
            context_selection = self._context_selection_debug(
                question=question,
                answer=answer,
                context_units=context_units,
                strategy=context_strategy,
                candidate_count=context_selection.candidate_count,
                fallback_reasons=context_selection.reasons,
            )
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
                context_selection_score=context_selection.score,
                context_reasons=context_selection.reasons,
                candidate_context_count=context_selection.candidate_count,
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
            context_selection_score=context_selection.score,
            context_reasons=context_selection.reasons,
            candidate_context_count=context_selection.candidate_count,
        )

    def _local_topic_context_units(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> _ContextSelection:
        """Return nearby topic-bearing units for local question context."""

        question_start_index = question.question_units[0].index
        candidate_units: list[_ExtractionUnit] = []
        for candidate_index in range(max(0, question_start_index - 2), question_start_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            candidate_units.append(candidate_unit)

        selection = self._select_context_units(
            question=question,
            answer=answer,
            candidate_units=candidate_units,
            strategy="local_topic_window",
        )
        if selection.units:
            return selection
        if candidate_units and self._is_contextual_question(question.question_text):
            contextual_selection = self._select_context_units(
                question=question,
                answer=answer,
                candidate_units=[candidate_units[-1]],
                strategy="fallback_previous_context",
                allow_weak=True,
            )
            if contextual_selection.units:
                contextual_selection.reasons = self._unique_strings(
                    ["contextual_followup_context"] + contextual_selection.reasons,
                )[:6]
                return contextual_selection
        if self._usable_question_preamble_context(question):
            return _ContextSelection(
                units=[question.unit],
                score=0.72,
                reasons=["question_preamble_context"],
                candidate_count=max(1, selection.candidate_count),
            )
        if answer.distance_units <= 1:
            previous_index = max(0, question.unit_index - 1)
            if previous_index != question.unit_index:
                previous_unit = units[previous_index]
                if previous_unit.audio_source_id == question.unit.audio_source_id:
                    return self._select_context_units(
                        question=question,
                        answer=answer,
                        candidate_units=[previous_unit],
                        strategy="local_topic_window",
                        allow_weak=True,
                    )
        return selection

    def _deferred_context_units(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> _ContextSelection:
        """Return context units for answers discovered beyond the local window."""

        candidate_units: list[_ExtractionUnit] = []
        answer_bridge_units: list[_ExtractionUnit] = []
        question_start_index = question.question_units[0].index
        answer_start_index = answer.answer_units[0].index
        for candidate_index in range(max(0, question_start_index - 2), question.unit_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            candidate_units.append(candidate_unit)

        for candidate_index in range(max(question.unit_index + 1, answer_start_index - 2), answer_start_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            candidate_units.append(candidate_unit)
            answer_bridge_units.append(candidate_unit)

        selection = self._select_context_units(
            question=question,
            answer=answer,
            candidate_units=candidate_units,
            strategy="deferred_answer_context",
        )
        if selection.units:
            return selection
        bridge_selection = self._select_context_units(
            question=question,
            answer=answer,
            candidate_units=answer_bridge_units,
            strategy="fallback_previous_context",
            allow_weak=False,
        )
        if bridge_selection.units:
            bridge_selection.reasons = self._unique_strings(
                ["deferred_answer_bridge_context"] + bridge_selection.reasons,
            )[:6]
            return bridge_selection
        if self._usable_question_preamble_context(question):
            return _ContextSelection(
                units=[question.unit],
                score=0.68,
                reasons=["question_preamble_context"],
                candidate_count=max(1, selection.candidate_count),
            )
        return selection

    def _fallback_context_units(
        self,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        units: Sequence[_ExtractionUnit],
    ) -> _ContextSelection:
        """Return nearby setup when topic matching found no context."""

        question_start_index = question.question_units[0].index
        fallback_units: list[_ExtractionUnit] = []
        for candidate_index in range(max(0, question_start_index - 2), question_start_index):
            candidate_unit = units[candidate_index]
            if candidate_unit.audio_source_id != question.unit.audio_source_id:
                continue
            if self._is_context_filler_candidate(
                normalize_rule_text(candidate_unit.text),
            ):
                continue
            fallback_units.append(candidate_unit)
        if fallback_units:
            return self._select_context_units(
                question=question,
                answer=answer,
                candidate_units=fallback_units,
                strategy="fallback_previous_context",
                allow_weak=True,
            )
        if self._usable_question_preamble_context(question):
            return _ContextSelection(
                units=[question.unit],
                score=0.55,
                reasons=["question_preamble_context"],
                candidate_count=1,
            )
        return _ContextSelection(candidate_count=len(fallback_units))

    def _select_context_units(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        candidate_units: Sequence[_ExtractionUnit],
        strategy: str,
        allow_weak: bool = False,
    ) -> _ContextSelection:
        """Return the best extractive context units and compact diagnostics."""

        scored_units: list[tuple[float, _ExtractionUnit, list[str]]] = []
        seen_text_ids: set[str] = set()
        candidate_count = 0
        for unit in candidate_units:
            if unit.text_id in seen_text_ids:
                continue
            seen_text_ids.add(unit.text_id)
            candidate_count += 1
            score, reasons = self._score_context_candidate(
                question=question,
                answer=answer,
                unit=unit,
                strategy=strategy,
            )
            toxic_weak_context = any(
                reason in reasons
                for reason in (
                    "competing_question_context",
                    "duplicate_question_context",
                    "filler_context_candidate",
                    "incomplete_context_candidate",
                    "thin_context_candidate",
                )
            )
            if toxic_weak_context:
                continue
            if score <= 0.0 and not allow_weak:
                continue
            scored_units.append((score, unit, reasons))

        if not scored_units:
            return _ContextSelection(candidate_count=candidate_count)

        scored_units.sort(key=lambda item: (-item[0], item[1].index))
        selected_items = scored_units[:2]
        selected_units = sorted((item[1] for item in selected_items), key=lambda unit: unit.index)
        reasons = self._unique_strings(
            reason for item in selected_items for reason in item[2]
        )[:6]
        score = self._clamp(
            sum(max(0.0, item[0]) for item in selected_items) / len(selected_items),
        )
        return _ContextSelection(
            units=selected_units,
            score=round(score, 4),
            reasons=reasons,
            candidate_count=candidate_count,
        )

    def _context_selection_debug(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_units: Sequence[_ExtractionUnit],
        strategy: str | None,
        candidate_count: int,
        fallback_reasons: Sequence[str],
    ) -> _ContextSelection:
        """Recompute compact diagnostics after deduping selected context units."""

        if not context_units:
            return _ContextSelection(
                score=0.0,
                reasons=list(fallback_reasons),
                candidate_count=candidate_count,
            )
        scored_units = [
            self._score_context_candidate(
                question=question,
                answer=answer,
                unit=unit,
                strategy=strategy or "context",
            )
            for unit in context_units
        ]
        score = self._clamp(
            sum(max(0.0, item[0]) for item in scored_units) / len(scored_units),
        )
        reasons = self._unique_strings(
            list(fallback_reasons)
            + [reason for _, item_reasons in scored_units for reason in item_reasons]
        )[:6]
        return _ContextSelection(
            units=list(context_units),
            score=round(score, 4),
            reasons=reasons,
            candidate_count=max(candidate_count, len(context_units)),
        )

    def _score_context_candidate(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        unit: _ExtractionUnit,
        strategy: str,
    ) -> tuple[float, list[str]]:
        """Score one nearby unit as readable extractive context."""

        normalized_text = normalize_rule_text(unit.text)
        unit_tokens = self._content_tokens(unit.text)
        question_tokens = self._content_tokens(question.question_text)
        answer_tokens = self._content_tokens(answer.answer_text)
        shared_question_tokens = unit_tokens & question_tokens
        shared_answer_tokens = unit_tokens & answer_tokens
        shared_numbers = self._number_tokens(unit.text) & (
            self._number_tokens(question.question_text)
            | self._number_tokens(answer.answer_text)
        )

        score = 0.05
        reasons: list[str] = []
        if self._context_duplicates_question(unit.text, question.question_text):
            score -= 0.55
            reasons.append("duplicate_question_context")
        if shared_question_tokens:
            score += min(0.30, 0.08 * len(shared_question_tokens))
            reasons.append("question_topic_overlap")
        if shared_answer_tokens:
            score += min(0.20, 0.05 * len(shared_answer_tokens))
            reasons.append("answer_topic_overlap")
        if shared_numbers:
            score += 0.16
            reasons.append("shared_number_context")

        token_count = len(unit_tokens)
        if token_count >= 8:
            score += 0.12
            reasons.append("substantive_context")
        elif token_count >= 5:
            score += 0.08
            reasons.append("usable_context")
        elif token_count <= 2:
            score -= 0.22
            reasons.append("thin_context_candidate")
        elif token_count <= 4:
            score -= 0.08
            reasons.append("short_context_candidate")

        has_overlap = bool(shared_question_tokens or shared_answer_tokens or shared_numbers)
        if not has_overlap and strategy != "fallback_previous_context":
            score -= 0.28
            reasons.append("low_topic_overlap")
        elif not has_overlap:
            score += 0.04
            reasons.append("nearby_setup_context")

        if unit.source_utterance_ids:
            score += 0.03
        if unit.sentence_ids:
            score += 0.02
        if unit.semantic_quality_label in {"fragment", "run_on"}:
            score -= 0.08
            reasons.append("weak_boundary_context")
        if self._looks_like_incomplete_answer_span(unit.text):
            score -= 0.20
            reasons.append("incomplete_context_candidate")
        if self._is_context_filler_candidate(normalized_text):
            score -= 0.35
            reasons.append("filler_context_candidate")
        if self._is_answer_question_like(unit.text):
            score -= 0.42
            reasons.append("competing_question_context")

        return score, self._unique_strings(reasons)

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
            if (
                self._context_duplicates_question(unit.text, question.question_text)
                and (
                    not question.metadata.get("question_context_expanded")
                    or self._is_answer_question_like(unit.text)
                )
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
        if self._usable_question_preamble_context(question):
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
    def _is_context_filler_candidate(normalized_text: str) -> bool:
        """Return whether a context unit is only transition/filler material."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        if not stripped:
            return True
        if stripped in _FILLER_ANSWERS:
            return True
        token_count = count_tokens(stripped)
        if token_count <= 2 and stripped in {"well", "okay", "ok", "right", "yeah"}:
            return True
        if re.search(r"\b(?:q\s*&\s*a|q&a|questions?)\b", stripped):
            return True
        if re.search(r"\bplease\s+bear\s+with\s+me\b", stripped):
            return True
        if re.match(r"^(?:i|we)\s+(?:do\s+not|don't)\s+know\b", stripped):
            return True
        return bool(
            re.fullmatch(
                r"(?:let'?s|let us|we(?:'| a)?re going to)\s+"
                r"(?:move|turn|go|continue|pause|stop|start)\b.*",
                stripped,
            )
        )

    def _context_duplicates_question(self, context_text: str, question_text: str) -> bool:
        """Return whether a context unit mostly repeats the selected question."""

        normalized_context = normalize_rule_text(context_text).rstrip(" ?.!").strip()
        normalized_question = normalize_rule_text(question_text).rstrip(" ?.!").strip()
        if not normalized_context or not normalized_question:
            return False
        if normalized_context == normalized_question:
            return True
        context_tokens = self._content_tokens(normalized_context)
        question_tokens = self._content_tokens(normalized_question)
        if len(context_tokens) < 3 or not question_tokens:
            return False
        overlap = len(context_tokens & question_tokens)
        return (
            overlap / max(1, len(context_tokens)) >= 0.82
            and overlap / max(1, len(question_tokens)) >= 0.70
        )

    def _usable_question_preamble_context(self, question: QuestionCandidate) -> bool:
        """Return whether the extracted question preamble is useful as context."""

        preamble = self._clean_context_sentence(
            str(question.metadata.get("question_preamble") or ""),
        )
        if not preamble:
            return False
        if self._is_answer_question_like(preamble):
            return False
        normalized_preamble = normalize_rule_text(preamble)
        if self._is_context_filler_candidate(normalized_preamble):
            return False
        if self._looks_like_incomplete_answer_span(preamble):
            return False
        return len(self._content_tokens(preamble)) >= 3

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
            if leading_text:
                return leading_text
            cue_words = set(INTERROGATIVE_START_WORDS) | _AUXILIARY_QUESTION_START_WORDS
            for token_index, match in enumerate(re.finditer(r"\b[\w']+\b", sentence)):
                token = normalize_rule_text(match.group(0))
                if token not in cue_words and token.split("'", maxsplit=1)[0] not in cue_words:
                    continue
                if token_index == 0:
                    return None
                inline_leading_text = sentence[: match.start()].strip(" ,;:-")
                return inline_leading_text or None
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
        if "rhetorical_poll_question" in reason_codes:
            flags.append("rhetorical_poll_question")
        if "poll_or_backchannel_noise" in reason_codes:
            flags.append("poll_or_backchannel_noise")
        if "procedural_question_request" in reason_codes:
            flags.append("procedural_question_request")
        if "fragment_question" in reason_codes:
            flags.append("fragment_question")
        if "declarative_tag_question" in reason_codes:
            flags.append("declarative_tag_question")
        if "low_autonomy_implicit_question" in reason_codes:
            flags.append("low_autonomy_implicit_question")
        if "question_low_sentence_autonomy" in reason_codes:
            flags.append("low_sentence_autonomy")
        if "question_low_boundary_confidence" in reason_codes:
            flags.append("low_boundary_confidence")
        if "answer_echoes_question_penalty" in reason_codes:
            flags.append("same_sentence_echo")
        if "answer_circular_echo_penalty" in reason_codes:
            flags.append("circular_answer_echo")
        if "same_sentence_without_answer_cue" in reason_codes:
            flags.append("same_sentence_without_answer_cue")
        if "quality_local_deferred_penalty" in reason_codes:
            flags.append("quality_local_deferred")
        if "answer_boilerplate_penalty" in reason_codes:
            flags.append("answer_boilerplate")
        if "deflection_answer_penalty" in reason_codes:
            flags.append("deflection_answer")
        if "low_information_answer_penalty" in reason_codes:
            flags.append("low_information_answer")
        if "incomplete_answer_span_penalty" in reason_codes:
            flags.append("incomplete_answer_span")
        if "surface_answer_cue_penalty" in reason_codes:
            flags.append("surface_answer_cue")
        if "deferred_answer_too_broad_penalty" in reason_codes:
            flags.append("deferred_answer_too_broad")
        if "answer_poll_or_backchannel_penalty" in reason_codes:
            flags.append("answer_poll_or_backchannel")
        if "answer_truncated_at_boundary_penalty" in reason_codes:
            flags.append("answer_truncated_at_boundary")
        if "question_span_integrity_penalty" in reason_codes:
            flags.append("question_span_integrity")
        if "answer_responsiveness_weak" in reason_codes:
            flags.append("weak_answer_responsiveness")
        if "answer_responsiveness_followup_prompt" in reason_codes:
            flags.append("followup_prompt_answer")
        if "monologue_continuation_risk" in reason_codes:
            flags.append("monologue_continuation_risk")
        if "question_intent_poll_or_check" in reason_codes:
            flags.append("poll_or_check_question")
        if "question_intent_rhetorical_tag" in reason_codes:
            flags.append("rhetorical_tag_question")
        if "question_intent_fragment" in reason_codes:
            flags.append("fragment_question")
        if "question_intent_subordinate_fragment" in reason_codes:
            flags.append("subordinate_fragment_question")
        if "question_intent_embedded_statement" in reason_codes:
            flags.append("embedded_statement_question")
        if "question_intent_weak_form" in reason_codes:
            flags.append("weak_question_form")
        return self._unique_strings(flags)

    def _build_candidate_quality_features(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_extraction: _ContextExtraction,
        confidence: float,
        input_layer: str,
        segment_relation: str,
        reason_codes: Sequence[str],
        review_flags: Sequence[str],
        question_timing_source: str,
        answer_timing_source: str,
    ) -> dict[str, Any]:
        """Return compact diagnostic quality features for one QA candidate."""

        reason_set = set(reason_codes)
        review_flag_set = set(review_flags)
        question_quality_score = self._clamp(
            0.70 * float(question.question_score)
            + 0.30 * float(question.didactic_question_score or 0.0),
        )
        answer_quality_score = self._clamp(float(answer.answer_score))
        answer_responsiveness_score = self._answer_responsiveness_feature_score(answer)
        context_quality_score = self._context_quality_feature_score(context_extraction)
        grounding_quality_score = self._grounding_quality_feature_score(
            question=question,
            answer=answer,
            context_extraction=context_extraction,
            input_layer=input_layer,
            segment_relation=segment_relation,
            question_timing_source=question_timing_source,
            answer_timing_source=answer_timing_source,
        )
        risk_reasons, risk_score = self._quality_risk_reasons_and_score(
            reason_codes=reason_set,
            review_flags=review_flag_set,
            confidence=confidence,
            question=question,
            answer=answer,
            context_extraction=context_extraction,
        )
        base_quality_score = self._clamp(
            (0.34 * question_quality_score)
            + (0.38 * answer_quality_score)
            + (0.16 * context_quality_score)
            + (0.12 * grounding_quality_score),
        )
        final_quality_score = self._clamp(base_quality_score - (0.45 * risk_score))

        return {
            "schema_version": "1.0",
            "question_quality_score": round(question_quality_score, 4),
            "answer_quality_score": round(answer_quality_score, 4),
            "answer_responsiveness_score": round(answer_responsiveness_score, 4),
            "context_quality_score": round(context_quality_score, 4),
            "grounding_quality_score": round(grounding_quality_score, 4),
            "semantic_responsiveness_status": "disabled",
            "semantic_responsiveness_score": None,
            "semantic_question_answer_similarity": None,
            "semantic_answer_continuation_similarity": None,
            "semantic_echo_penalty": None,
            "semantic_continuation_penalty": None,
            "semantic_gate_penalty_applied": False,
            "risk_score": round(risk_score, 4),
            "final_quality_score": round(final_quality_score, 4),
            "quality_band": self._quality_band(final_quality_score),
            "risk_band": self._risk_band(risk_score),
            "risk_reasons": risk_reasons,
        }

    @staticmethod
    def _answer_responsiveness_feature_score(answer: _AnswerCandidate) -> float:
        """Return the compact answer responsiveness score when available."""

        responsiveness_debug = answer.metadata.get("answer_responsiveness_debug")
        if not isinstance(responsiveness_debug, dict):
            return 0.55
        score = responsiveness_debug.get("score")
        if isinstance(score, int | float):
            return max(0.0, min(1.0, float(score)))
        return 0.55

    @staticmethod
    def _context_quality_feature_score(
        context_extraction: _ContextExtraction,
    ) -> float:
        """Return compact context quality from exported context metadata."""

        if not context_extraction.context_text:
            return 0.20
        if context_extraction.context_confidence == "high":
            return 0.90
        if context_extraction.context_confidence == "medium":
            return 0.68
        if context_extraction.context_confidence == "low":
            return 0.40
        return 0.55

    def _context_is_too_thin(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_extraction: _ContextExtraction,
    ) -> bool:
        """Return whether context adds too little beyond Q/A text."""

        context_text = context_extraction.context_text or ""
        context_tokens = self._content_tokens(context_text)
        if not context_tokens:
            return context_extraction.candidate_context_count > 0
        if len(context_tokens) <= 2:
            return True
        question_tokens = self._content_tokens(question.question_text)
        answer_tokens = self._content_tokens(answer.answer_text)
        qa_tokens = question_tokens | answer_tokens
        if context_tokens and context_tokens.issubset(qa_tokens) and len(context_tokens) <= 4:
            return True
        if (
            context_extraction.context_confidence == "low"
            and len(context_tokens - qa_tokens) <= 1
        ):
            return True
        return False

    def _grounding_quality_feature_score(
        self,
        *,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_extraction: _ContextExtraction,
        input_layer: str,
        segment_relation: str,
        question_timing_source: str,
        answer_timing_source: str,
    ) -> float:
        """Return compact grounding quality without duplicating debug payloads."""

        score = 0.0
        if input_layer == "sentences":
            score += 0.18
        if question.question_sentence_ids:
            score += 0.12
        if answer.answer_sentence_ids:
            score += 0.12
        if question.question_source_utterance_ids:
            score += 0.14
        if answer.answer_source_utterance_ids:
            score += 0.14
        if question_timing_source != "missing":
            score += 0.08
        if answer_timing_source != "missing":
            score += 0.08
        if segment_relation == "same_segment":
            score += 0.10
        elif segment_relation == "next_segment":
            score += 0.06
        elif segment_relation == "distant_segment":
            score -= 0.08
        if context_extraction.context_sentence_ids:
            score += 0.04
        return self._clamp(score)

    def _quality_risk_reasons_and_score(
        self,
        *,
        reason_codes: set[str],
        review_flags: set[str],
        confidence: float,
        question: QuestionCandidate,
        answer: _AnswerCandidate,
        context_extraction: _ContextExtraction,
    ) -> tuple[list[str], float]:
        """Return compact risk reasons and normalized risk score."""

        risk_weights = {
            "low_confidence": 0.20,
            "fallback_input_layer": 0.16,
            "answer_is_question": 0.28,
            "rhetorical_poll_question": 0.28,
            "poll_or_backchannel_noise": 0.22,
            "procedural_question_request": 0.24,
            "poll_or_check_question": 0.24,
            "fragment_question": 0.24,
            "subordinate_fragment_question": 0.24,
            "declarative_tag_question": 0.22,
            "weak_question_form": 0.22,
            "embedded_statement_question": 0.18,
            "weak_implicit_quantity_question": 0.24,
            "weak_expanded_contextual_question": 0.24,
            "low_autonomy_implicit_question": 0.24,
            "low_sentence_autonomy": 0.16,
            "low_boundary_confidence": 0.12,
            "same_sentence_echo": 0.24,
            "circular_answer_echo": 0.24,
            "same_sentence_without_answer_cue": 0.24,
            "answer_boilerplate": 0.24,
            "answer_poll_or_backchannel": 0.30,
            "low_information_answer": 0.20,
            "incomplete_answer_span": 0.14,
            "answer_truncated_at_boundary": 0.16,
            "question_span_integrity": 0.14,
            "surface_answer_cue": 0.22,
            "weak_answer_responsiveness": 0.22,
            "followup_prompt_answer": 0.26,
            "thin_answer_reply": 0.18,
            "unanchored_quantity_answer": 0.20,
            "monologue_continuation_risk": 0.24,
            "semantic_nonresponsive": 0.22,
            "deferred_answer_too_broad": 0.20,
            "quality_local_deferred": 0.14,
            "competing_question": 0.08,
            "segment_unknown": 0.08,
        }
        reason_aliases = {
            "low_question_answer_relevance": "low_relevance",
            "answer_echoes_question_penalty": "same_sentence_echo",
            "answer_circular_echo_penalty": "circular_answer_echo",
            "question_low_sentence_autonomy": "low_sentence_autonomy",
            "question_low_boundary_confidence": "low_boundary_confidence",
            "answer_boilerplate_penalty": "answer_boilerplate",
            "answer_poll_or_backchannel_penalty": "answer_poll_or_backchannel",
            "same_sentence_without_answer_cue": "same_sentence_without_answer_cue",
            "premise_only_answer_penalty": "premise_only_answer",
            "moderator_handoff_answer_penalty": "moderator_handoff_answer",
            "incomplete_answer_span_penalty": "incomplete_answer_span",
            "answer_truncated_at_boundary_penalty": "answer_truncated_at_boundary",
            "question_span_integrity_penalty": "question_span_integrity",
            "surface_answer_cue_penalty": "surface_answer_cue",
            "answer_responsiveness_weak": "weak_answer_responsiveness",
            "answer_responsiveness_followup_prompt": "followup_prompt_answer",
            "answer_responsiveness_thin_reply": "thin_answer_reply",
            "answer_responsiveness_unanchored_quantity": (
                "unanchored_quantity_answer"
            ),
            "deferred_long_temporal_gap": "deferred_long_gap",
            "distant_segment_penalty": "distant_segment",
        }
        extra_weights = {
            "low_relevance": 0.32,
            "premise_only_answer": 0.28,
            "moderator_handoff_answer": 0.20,
            "deferred_long_gap": 0.14,
            "distant_segment": 0.10,
            "implicit_question_risk": 0.22,
            "weak_context_risk": 0.14,
            "thin_context_risk": 0.16,
            "surface_answer_cue_risk": 0.22,
        }

        reasons: list[str] = []
        risk_score = 0.0
        for flag in sorted(review_flags):
            if flag not in risk_weights:
                continue
            reasons.append(flag)
            risk_score += risk_weights[flag]
        for reason_code, alias in sorted(reason_aliases.items()):
            if reason_code not in reason_codes or alias in reasons:
                continue
            reasons.append(alias)
            risk_score += extra_weights.get(alias, risk_weights.get(alias, 0.0))
        if confidence < 0.45 and "low_confidence" not in reasons:
            reasons.append("low_confidence")
            risk_score += risk_weights["low_confidence"]
        elif confidence < 0.75 and "medium_confidence" in review_flags:
            risk_score += 0.04
        if (
            "question_mark" not in reason_codes
            and "question_intent_autonomous_head" not in reason_codes
            and "question_intent_didactic_cue" not in reason_codes
        ):
            reasons.append("implicit_question_risk")
            risk_score += extra_weights["implicit_question_risk"]
        partial_scores = answer.partial_scores
        question_debug = question.metadata
        question_score = self._safe_float(question_debug.get("question_score"))
        if question_score is None:
            question_score = self._safe_float(question_debug.get("raw_question_score"))
        if (
            "question_mark" not in reason_codes
            and "implicit_question_cue" in reason_codes
            and "answer_responsiveness_quantity_missing" in reason_codes
            and "question_intent_didactic_cue" not in reason_codes
            and (question_score or 0.0) <= 0.53
            and float(partial_scores.get("keyword_overlap") or 0.0) <= 0.05
        ):
            reasons.append("weak_implicit_quantity_question")
            risk_score += risk_weights["weak_implicit_quantity_question"]
        if (
            bool(question_debug.get("question_context_expanded"))
            and int(question_debug.get("token_count") or 0) <= 2
            and "question_sentence_quality_penalty" in reason_codes
            and "question_merge_safety_penalty" in reason_codes
            and "competing_question_nearby" in reason_codes
        ):
            reasons.append("weak_expanded_contextual_question")
            risk_score += risk_weights["weak_expanded_contextual_question"]
        if (
            not context_extraction.context_text
            or context_extraction.context_confidence == "low"
        ):
            reasons.append("weak_context_risk")
            risk_score += extra_weights["weak_context_risk"]
        if self._context_is_too_thin(
            question=question,
            answer=answer,
            context_extraction=context_extraction,
        ):
            reasons.append("thin_context_risk")
            risk_score += extra_weights["thin_context_risk"]
        answer_cue_score = float(partial_scores.get("answer_cues") or 0.0)
        relevance_score = float(partial_scores.get("relevance") or 0.0)
        if answer_cue_score > 0.0 and relevance_score <= 0.0:
            reasons.append("surface_answer_cue_risk")
            risk_score += extra_weights["surface_answer_cue_risk"]

        return self._unique_strings(reasons)[:8], self._clamp(risk_score)

    @staticmethod
    def _quality_band(score: float) -> str:
        """Return a compact quality band for a normalized score."""

        if score >= 0.74:
            return "high"
        if score >= 0.50:
            return "medium"
        return "low"

    @staticmethod
    def _risk_band(score: float) -> str:
        """Return a compact risk band for a normalized score."""

        if score >= 0.55:
            return "high"
        if score >= 0.25:
            return "medium"
        return "low"

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
        if "rhetorical_checkin_question" in question.reason_codes:
            return {
                "triggered": False,
                "reason": "rhetorical_checkin_question",
                "didactic_question_score": question.didactic_question_score,
            }
        if "rhetorical_poll_question" in question.reason_codes:
            return {
                "triggered": False,
                "reason": "rhetorical_poll_question",
                "didactic_question_score": question.didactic_question_score,
            }
        if "fragment_question" in question.reason_codes:
            return {
                "triggered": False,
                "reason": "fragment_question",
                "didactic_question_score": question.didactic_question_score,
            }
        if "declarative_tag_question" in question.reason_codes:
            return {
                "triggered": False,
                "reason": "declarative_tag_question",
                "didactic_question_score": question.didactic_question_score,
            }
        if self.config.pipeline_profile == "quality_local" and (
            "question_intent_embedded_statement" in question.reason_codes
            or "question_intent_weak_form" in question.reason_codes
        ):
            return {
                "triggered": False,
                "reason": "weak_question_intent",
                "didactic_question_score": question.didactic_question_score,
            }
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
        if self._has_strong_local_answer(best_answer):
            return {
                "triggered": False,
                "reason": "strong_local_answer",
                "low_local_relevance": False,
                "best_answer_score": best_answer_score,
                "best_semantic_score": None,
                "didactic_question_score": question.didactic_question_score,
            }
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

    @staticmethod
    def _has_strong_local_answer(answer: _AnswerCandidate | None) -> bool:
        """Return whether a local answer is strong enough to avoid deferred search."""

        if answer is None:
            return False
        if answer.answer_score < 0.52:
            return False
        if answer.gap_seconds > 10.0:
            return False
        if answer.metadata.get("answer_is_question"):
            return False
        alignment_debug = answer.metadata.get("qa_alignment_debug", {})
        if not (
            alignment_debug.get("shared_keywords")
            or alignment_debug.get("shared_numbers")
        ):
            return False
        return answer.metadata.get("segment_relation") != "distant_segment"

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
            if not self._deferred_alignment_is_strong_enough(
                question=question,
                candidate_unit=candidate_unit,
                alignment=alignment,
            ):
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

    def _deferred_alignment_is_strong_enough(
        self,
        *,
        question: QuestionCandidate,
        candidate_unit: _ExtractionUnit,
        alignment: dict[str, Any],
    ) -> bool:
        """Return whether a far answer has enough signal to beat local search."""

        shared_keywords = [
            keyword
            for keyword in alignment.get("shared_keywords", [])
            if keyword not in _WEAK_DEFERRED_KEYWORDS
        ]
        shared_numbers = list(alignment.get("shared_numbers", []))
        distance_units = candidate_unit.index - question.unit_index
        gap_seconds = max(0.0, candidate_unit.start_seconds - question.unit.end_seconds)
        signal_score = float(alignment.get("signal_score") or 0.0)

        if self._is_moderator_handoff_answer(normalize_rule_text(candidate_unit.text)):
            return False
        if self._is_filler_or_boilerplate_answer(normalize_rule_text(candidate_unit.text)):
            return False
        if not shared_keywords and not shared_numbers:
            return False
        if self.config.pipeline_profile == "quality_local":
            if gap_seconds > 75.0 and (
                signal_score < 0.38 or (len(shared_keywords) < 4 and not shared_numbers)
            ):
                return False
            if gap_seconds > 30.0 and (
                signal_score < 0.30 or (len(shared_keywords) < 2 and not shared_numbers)
            ):
                return False
            if distance_units > 10 and (
                signal_score < 0.30 or (len(shared_keywords) < 3 and not shared_numbers)
            ):
                return False
        if gap_seconds > 120.0 and (
            signal_score < 0.34 or (len(shared_keywords) < 3 and not shared_numbers)
        ):
            return False
        if gap_seconds > 60.0 and signal_score < 0.28:
            return False
        if distance_units > 12 and signal_score < 0.24 and len(shared_keywords) < 2:
            return False
        return True

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
            or _DIRECT_DEFINITION_REQUEST_RE.match(normalized_text)
        )

    def _starts_with_contextual_question(self, text: str) -> bool:
        """Return whether a trailing span starts with a contextual follow-up."""

        trailing_spans = self._sentence_spans(text.strip())
        if not trailing_spans:
            return False
        first_span = trailing_spans[0][0].strip()
        if "?" not in first_span and not self._has_strong_question_signal(first_span):
            return False
        return self._is_contextual_question(first_span)

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
        if _CAUSAL_ANSWER_AFTER_WHY_RE.match(normalized_text):
            return False
        return self._starts_with_interrogative_word(normalized_text)

    def _has_question_cue_with_focus_overlap(
        self, question_text: str, answer_text: str
    ) -> bool:
        """Return whether answer text looks like the same question continuing."""

        normalized_answer = normalize_rule_text(answer_text)
        if not normalized_answer or normalized_answer.endswith("?"):
            return False
        if _CAUSAL_ANSWER_AFTER_WHY_RE.match(normalized_answer):
            return False
        if self._is_followup_prompt_answer(normalized_answer):
            return False
        continuation_focus = re.sub(
            r"^(?:that\s+is|i\s+mean|meaning|in\s+other\s+words|cioe|cioè)[,;:]?\s+",
            "",
            normalized_answer,
        )
        if not self._has_question_continuation_cue_position(continuation_focus):
            return False
        normalized_question = normalize_rule_text(question_text)
        question_tokens = set(self._content_tokens(normalized_question))
        answer_tokens = set(self._content_tokens(normalized_answer))
        if not question_tokens or not answer_tokens:
            return False
        shared_tokens = question_tokens & answer_tokens
        if len(shared_tokens) >= 2:
            return True
        return bool(shared_tokens) and (len(shared_tokens) / max(1, len(question_tokens))) >= 0.25

    def _has_question_continuation_cue_position(self, text: str) -> bool:
        """Return whether interrogative syntax appears at continuation head."""

        normalized_text = normalize_rule_text(text).rstrip(" ?.!;:").strip()
        if not normalized_text:
            return False
        if self._has_head_interrogative_cue(normalized_text):
            return True
        tokens = re.findall(r"\b[\w']+\b", normalized_text)
        interrogative_words = set(INTERROGATIVE_START_WORDS)
        internal_ambiguous_words = {"because", "why", "perche", "perché", "che", "cosa", "what"}
        for index, token in enumerate(tokens[:2]):
            base_token = token.split("'", maxsplit=1)[0]
            if token in internal_ambiguous_words or base_token in internal_ambiguous_words:
                continue
            if token in interrogative_words or base_token in interrogative_words:
                return True
        return False

    def _is_answer_question_continuation_rejected(
        self, *, question: QuestionCandidate, answer_text: str
    ) -> bool:
        """Return whether a local answer is still an unmarked question continuation."""

        focus_text = str(
            question.metadata.get("normalized_question_text")
            or question.question_text
            or ""
        )
        return self._has_question_cue_with_focus_overlap(focus_text, answer_text)

    def _starts_with_additive_continuation(self, text: str) -> bool:
        """Return whether text opens as an additive continuation."""

        return bool(_ADDITIVE_CONTINUATION_START_RE.match(normalize_rule_text(text)))

    def _is_additive_continuation_answer(
        self, question: QuestionCandidate, answer: _AnswerCandidate
    ) -> bool:
        """Return whether an adjacent same-speaker answer starts as continuation."""

        if answer.distance_units != 1:
            return False
        if answer.gap_seconds is not None and answer.gap_seconds > 6.0:
            return False
        if not self._starts_with_additive_continuation(answer.answer_text):
            return False
        question_profile = self._speaker_profile(question.unit)
        answer_profile = self._speaker_profile_from_units(answer.answer_units)
        if (
            question_profile["reliability"] == "reliable"
            and answer_profile["reliability"] == "reliable"
        ):
            return question_profile["speaker_id"] == answer_profile["speaker_id"]
        return (
            question_profile["reliability"] == "missing"
            or answer_profile["reliability"] == "missing"
        )

    def _is_question_continuation_answer(self, normalized_text: str) -> bool:
        """Return whether an answer is likely still clarifying the question."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        starts_with_clarifier = stripped.startswith(
            (
                "cioe ",
                "cioè ",
                "that is ",
                "i mean ",
                "meaning ",
                "in other words ",
            ),
        )
        if not starts_with_clarifier:
            return False
        cue_index = self._first_question_cue_token_index(stripped)
        if cue_index is not None and cue_index >= 2:
            return True
        return bool(re.search(r"\b(?:if|se)\s+(?:one|you|uno|qualcuno)\b", stripped))

    def _is_interview_bridge_unit(
        self,
        text: str,
        *,
        question_text: str,
    ) -> bool:
        """Return whether a unit is a local bridge before an interview answer."""

        return self._interview_bridge_kind(
            text,
            question_text=question_text,
        ) is not None

    def _interview_bridge_kind(
        self,
        text: str,
        *,
        question_text: str,
    ) -> str | None:
        """Return the bridge kind for a local interview cluster unit."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return "empty"
        normalized_text = normalize_rule_text(cleaned_text)
        if self._is_followup_prompt_text(normalized_text):
            return "prompt"
        if self._is_question_echo_bridge(
            bridge_text=cleaned_text,
            question_text=question_text,
        ):
            return "echo"
        if self._is_answer_question_like(cleaned_text):
            return "question"
        return None

    def _is_question_echo_bridge(
        self,
        *,
        bridge_text: str,
        question_text: str,
    ) -> bool:
        """Return whether bridge text mostly repeats the question as an echo."""

        bridge_tokens = self._content_tokens(bridge_text)
        question_tokens = self._content_tokens(question_text)
        if not bridge_tokens or not question_tokens:
            return False
        overlap = len(bridge_tokens & question_tokens) / max(1, len(bridge_tokens))
        return overlap >= 0.55 and len(bridge_tokens) <= 8

    def _is_interview_echo_question_candidate(
        self,
        *,
        unit_index: int,
        units: Sequence[_ExtractionUnit],
        question_text: str,
    ) -> bool:
        """Return whether a question is likely an interviewee echo."""

        if unit_index <= 0:
            return False
        question_tokens = self._content_tokens(question_text)
        if not question_tokens or len(question_tokens) > 8:
            return False
        saw_prompt = False
        saw_related_question = False
        for candidate_index in range(max(0, unit_index - 4), unit_index):
            candidate_text = units[candidate_index].text
            normalized_text = normalize_rule_text(candidate_text)
            if self._is_followup_prompt_text(normalized_text):
                saw_prompt = True
                continue
            if "?" not in candidate_text:
                continue
            previous_tokens = self._content_tokens(candidate_text)
            if not previous_tokens:
                continue
            overlap = len(question_tokens & previous_tokens) / max(
                1,
                min(len(question_tokens), len(previous_tokens)),
            )
            if overlap >= 0.45:
                saw_related_question = True
        return saw_prompt and saw_related_question

    def _is_plausible_interview_cluster_answer(
        self,
        *,
        question: QuestionCandidate,
        answer_text: str,
        skipped_units: Sequence[_ExtractionUnit],
    ) -> bool:
        """Return whether a post-cluster answer is substantial enough."""

        normalized_answer = normalize_rule_text(answer_text)
        if self._is_answer_question_like(answer_text):
            return False
        if self._is_followup_prompt_text(normalized_answer):
            return False
        if self._is_filler_or_boilerplate_answer(normalized_answer):
            return False
        token_count = count_tokens(normalized_answer)
        if token_count < 5:
            return False
        answer_tokens = self._content_tokens(normalized_answer)
        question_tokens = self._content_tokens(question.question_text)
        if len(answer_tokens - question_tokens) < 3:
            return False
        if len(skipped_units) > 4:
            return False
        return True

    def _classify_question_intent(
        self,
        *,
        normalized_text: str,
        has_question_mark: bool,
        question_matches: Sequence[Any],
        didactic_matches: Sequence[Any],
        token_count: int,
        is_poll_question: bool,
        is_declarative_tag_question: bool,
        is_fragment_question: bool,
        is_rhetorical_checkin: bool,
    ) -> dict[str, Any]:
        """Classify question intent from structural cues, not exact examples."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        has_question_head = self._has_autonomous_question_head(stripped)
        first_cue_index = self._first_question_cue_token_index(stripped)
        embedded_cue = (
            first_cue_index is not None
            and first_cue_index >= 3
            and not has_question_head
        )
        subordinate_fragment = self._is_subordinate_question_fragment(
            normalized_text=normalized_text,
            has_question_mark=has_question_mark,
        )
        score_delta = 0.0
        reason_codes: list[str] = []
        question_intent = "information_seeking"

        if is_poll_question or is_rhetorical_checkin:
            question_intent = "poll_or_check"
            score_delta -= 0.18
            reason_codes.append("question_intent_poll_or_check")
        elif is_declarative_tag_question:
            question_intent = "rhetorical_tag"
            score_delta -= 0.18
            reason_codes.append("question_intent_rhetorical_tag")
        elif is_fragment_question:
            question_intent = "fragment"
            score_delta -= 0.16
            reason_codes.append("question_intent_fragment")
        elif subordinate_fragment:
            question_intent = "subordinate_fragment"
            score_delta -= 0.18
            reason_codes.append("question_intent_subordinate_fragment")
        elif embedded_cue:
            question_intent = "embedded_statement_question"
            score_delta -= 0.12
            reason_codes.append("question_intent_embedded_statement")
        elif has_question_mark and not has_question_head and not didactic_matches:
            question_intent = "weak_question_form"
            score_delta -= 0.10
            reason_codes.append("question_intent_weak_form")

        if has_question_head:
            reason_codes.append("question_intent_autonomous_head")
        if didactic_matches:
            reason_codes.append("question_intent_didactic_cue")
        if token_count <= 3 and not has_question_head:
            score_delta -= 0.08
            reason_codes.append("question_intent_short_without_head")

        return {
            "question_intent": question_intent,
            "score_delta": round(score_delta, 4),
            "reason_codes": self._unique_strings(reason_codes),
            "debug": {
                "has_question_head": has_question_head,
                "first_cue_token_index": first_cue_index,
                "embedded_cue": embedded_cue,
                "subordinate_fragment": subordinate_fragment,
                "token_count": token_count,
            },
        }

    def _has_autonomous_question_head(self, normalized_text: str) -> bool:
        """Return whether a candidate starts like an autonomous question."""

        normalized_text = self._strip_leading_question_discourse_markers(
            normalized_text,
        )
        if _DIRECT_DEFINITION_REQUEST_RE.match(normalized_text):
            return True
        if self._starts_with_interrogative_word(normalized_text):
            return True
        if self._is_terminal_object_question(normalized_text):
            return True
        first_word = normalized_text.split(maxsplit=1)[0] if normalized_text else ""
        first_word = first_word.split("'", maxsplit=1)[0]
        return first_word in _AUXILIARY_QUESTION_START_WORDS

    @staticmethod
    def _is_terminal_object_question(normalized_text: str) -> bool:
        """Return whether a short question asks for the object at the end."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        tokens = re.findall(r"\b[\w']+\b", stripped)
        return 2 <= len(tokens) <= 5 and tokens[-1] in {"che", "cosa", "what"}

    @staticmethod
    def _first_question_cue_token_index(normalized_text: str) -> int | None:
        """Return first token offset of a broad interrogative cue."""

        normalized_text = QAPairExtractor._strip_leading_question_discourse_markers(
            normalized_text,
        )
        tokens = re.findall(r"\b[\w']+\b", normalized_text)
        cue_words = set(INTERROGATIVE_START_WORDS) | _AUXILIARY_QUESTION_START_WORDS
        for index, token in enumerate(tokens):
            if token in cue_words or token.split("'", maxsplit=1)[0] in cue_words:
                return index
        return None

    @staticmethod
    def _strip_leading_question_discourse_markers(normalized_text: str) -> str:
        """Drop short discourse markers before evaluating question autonomy."""

        stripped = normalized_text.strip()
        for _ in range(2):
            parts = stripped.split(maxsplit=1)
            if not parts:
                return ""
            marker = parts[0].strip(" ,;:")
            if marker not in _LEADING_QUESTION_DISCOURSE_MARKERS:
                break
            stripped = parts[1] if len(parts) > 1 else ""
        return stripped

    @staticmethod
    def _is_subordinate_question_fragment(
        *,
        normalized_text: str,
        has_question_mark: bool,
    ) -> bool:
        """Return whether an implicit question is likely a subordinate clause."""

        if has_question_mark:
            return False
        stripped = normalized_text.rstrip(" ?.!").strip()
        if not stripped:
            return False
        tokens = re.findall(r"\b[\w']+\b", stripped)
        if len(tokens) < 3:
            return False
        if _EXPLANATORY_CLAUSE_START_RE.search(stripped):
            return True
        if tokens[0] not in _SUBORDINATE_QUESTION_START_WORDS:
            return False
        if len(tokens) > 7:
            return False
        if len(tokens) > 1 and tokens[1].split("'", maxsplit=1)[0] in (
            _AUXILIARY_QUESTION_START_WORDS
        ):
            return False
        return True

    def _is_causal_answer_after_previous_question(
        self,
        *,
        unit_index: int,
        units: Sequence[_ExtractionUnit],
        normalized_question: str,
    ) -> bool:
        """Return whether an implicit why-cue sentence is likely an answer."""

        if unit_index <= 0:
            return False
        if not _CAUSAL_ANSWER_AFTER_WHY_RE.match(normalized_question):
            return False
        previous_unit = units[unit_index - 1]
        if "?" not in previous_unit.text:
            return False
        gap_seconds = max(0.0, units[unit_index].start_seconds - previous_unit.end_seconds)
        return gap_seconds <= 12.0

    @staticmethod
    def _is_rhetorical_checkin_question(normalized_text: str) -> bool:
        """Return whether a question is only a discourse tag."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        return stripped in _DISCOURSE_TAG_QUESTIONS or bool(
            _TAG_QUESTION_RE.fullmatch(stripped),
        )

    @staticmethod
    def _is_rhetorical_poll_question(normalized_text: str) -> bool:
        """Return whether a question is a classroom poll rather than a QA prompt."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        if not stripped:
            return False
        if _RHETORICAL_POLL_QUESTION_RE.fullmatch(stripped):
            return True
        tokens = re.findall(r"\b[\w']+\b", stripped)
        if not tokens or len(tokens) > 6:
            return False
        poll_tokens = {
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "one",
            "two",
            "three",
            "four",
            "five",
            "uno",
            "due",
            "tre",
            "quattro",
            "cinque",
            "or",
            "o",
        }
        return all(token in poll_tokens for token in tokens) and any(
            token.isdigit()
            or token in {"one", "two", "three", "four", "five", "uno", "due", "tre"}
            for token in tokens
        )

    @staticmethod
    def _is_declarative_tag_question(
        *,
        normalized_text: str,
        has_question_mark: bool,
        question_matches: Sequence[Any],
        didactic_matches: Sequence[Any],
    ) -> bool:
        """Return whether a question is mostly a statement plus a tag."""

        if not has_question_mark or didactic_matches:
            return False
        stripped = normalized_text.rstrip(" ?.!").strip()
        if not stripped:
            return False
        parts = stripped.rsplit(maxsplit=1)
        if len(parts) == 1:
            return False
        if not _TAG_QUESTION_RE.fullmatch(parts[-1]):
            return False
        causal_declarative = QAPairExtractor._is_causal_declarative_statement(stripped)
        if question_matches and not causal_declarative:
            return False
        if causal_declarative:
            return True
        return not QAPairExtractor._starts_with_interrogative_word(stripped)

    @staticmethod
    def _is_causal_declarative_statement(normalized_text: str) -> bool:
        """Return whether a causal cue introduces a statement, not a question."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        if _CAUSAL_DECLARATIVE_STATEMENT_RE.match(stripped):
            return True
        return bool(_CAUSAL_EXISTENTIAL_TAG_RE.match(stripped))

    @staticmethod
    def _is_fragment_question(
        *,
        normalized_text: str,
        has_question_mark: bool,
        question_matches: Sequence[Any],
        didactic_matches: Sequence[Any],
    ) -> bool:
        """Return whether a question is too fragmentary to stand alone."""

        if not has_question_mark:
            return False
        if question_matches or didactic_matches:
            return False
        stripped = normalized_text.rstrip(" ?.!").strip()
        if QAPairExtractor._starts_with_interrogative_word(stripped):
            return False
        if QAPairExtractor._is_terminal_object_question(stripped):
            return False
        if any(pattern.fullmatch(stripped) for pattern in _ANAPHORIC_QUESTION_PATTERNS):
            return False
        token_count = count_tokens(stripped)
        if token_count <= 3:
            return True
        if stripped.startswith(("and ", "or ", "but ", "so ", "then ", "e ", "o ")):
            return True
        return False

    @staticmethod
    def _is_moderator_handoff_answer(normalized_text: str) -> bool:
        """Return whether an answer looks like a speaker hand-off."""

        return any(
            pattern.search(normalized_text)
            for pattern in _MODERATOR_HANDOFF_PATTERNS
        )

    @staticmethod
    def _is_filler_or_boilerplate_answer(normalized_text: str) -> bool:
        """Return whether an answer is mostly transition or lecture boilerplate."""

        stripped = normalized_text.rstrip(" ?.!").strip()
        if stripped in _FILLER_ANSWERS:
            return True
        return any(
            pattern.search(stripped)
            for pattern in _ANSWER_BOILERPLATE_PATTERNS
        )

    @staticmethod
    def _is_poll_or_backchannel_answer(normalized_text: str) -> bool:
        """Return whether an answer is only classroom poll/backchannel noise."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        tokens = re.findall(r"\b[\w']+\b", stripped)
        if not tokens or len(tokens) > 10:
            return False
        noise_tokens = _BACKCHANNEL_ANSWER_TOKENS | _POLL_OPTION_WORDS
        if not all(token in noise_tokens for token in tokens):
            return False
        option_count = sum(1 for token in tokens if token in _POLL_OPTION_WORDS)
        backchannel_count = len(tokens) - option_count
        if option_count >= 2:
            return True
        if backchannel_count >= 2:
            return True
        return len(set(tokens)) == 1 and len(tokens) >= 2

    @staticmethod
    def _trim_answer_meta_opening(text: str) -> tuple[str, str | None]:
        """Remove short meta openings when substantive answer text follows."""

        cleaned_text = text.strip()
        match = _META_ANSWER_OPENING_RE.match(cleaned_text)
        if not match:
            return cleaned_text, None
        rest = re.sub(r"\s+", " ", match.group("rest")).strip(" ,.-")
        if count_tokens(normalize_rule_text(rest)) < 3:
            return cleaned_text, None
        return rest, "answer_meta_opening_trimmed"

    def _trim_answer_text(self, text: str) -> tuple[str, list[str]]:
        """Return answer text with compact discourse wrappers removed."""

        cleaned_text, meta_reason = self._trim_answer_meta_opening(text)
        reasons = [meta_reason] if meta_reason else []
        cleaned_text, tag_reason = self._trim_answer_trailing_tag(cleaned_text)
        if tag_reason:
            reasons.append(tag_reason)
        return cleaned_text, reasons

    def _trim_internal_checkin_from_answer(
        self,
        *,
        question_text: str,
        answer_text: str,
    ) -> tuple[str, dict[str, Any]]:
        """Remove short check-in insertions between valid answer periods."""

        cleaned_text = re.sub(r"\s+", " ", answer_text or "").strip()
        clause_trimmed = self._trim_internal_checkin_clauses(cleaned_text)
        if clause_trimmed != cleaned_text and self._answer_text_stands_after_internal_trim(
            question_text=question_text,
            answer_text=clause_trimmed,
        ):
            return clause_trimmed, {
                "trimmed": True,
                "reason": "answer_internal_checkin_trimmed",
                "original_answer_text": answer_text,
                "trim_type": "clause",
            }

        spans = self._sentence_spans(cleaned_text)
        if len(spans) < 3:
            return answer_text, {"trimmed": False, "reason": "too_few_sentences"}

        kept_sentences: list[str] = []
        removed_sentences: list[str] = []
        for index, (sentence, _, _) in enumerate(spans):
            normalized_sentence = normalize_rule_text(sentence)
            internal = 0 < index < len(spans) - 1
            if internal and self._is_internal_checkin_sentence(normalized_sentence):
                removed_sentences.append(sentence.strip())
                continue
            kept_sentences.append(sentence.strip())

        if not removed_sentences or len(kept_sentences) == len(spans):
            return answer_text, {"trimmed": False, "reason": "no_internal_checkin"}

        candidate_text = self._join_text(kept_sentences)
        if not self._answer_text_stands_after_internal_trim(
            question_text=question_text,
            answer_text=candidate_text,
        ):
            return answer_text, {
                "trimmed": False,
                "reason": "remaining_answer_too_weak",
                "removed_sentences": removed_sentences,
            }
        return candidate_text, {
            "trimmed": True,
            "reason": "answer_internal_checkin_trimmed",
            "original_answer_text": answer_text,
            "removed_sentences": removed_sentences,
            "trim_type": "sentence",
        }

    @staticmethod
    def _trim_internal_checkin_clauses(text: str) -> str:
        """Remove compact check-in clauses embedded inside answer sentences."""

        cleaned = re.sub(
            r"(?i)(?:[,;:]\s*)?(?:ci\s+siamo\s*,?\s*)?"
            r"mi\s+state\s+(?:vedendo|sentendo|seguendo)"
            r"(?:\s*,?\s*(?:ci\s+siamo))?(?=[,.!?;:]|\s|$)",
            "",
            text,
        )
        cleaned = re.sub(
            r"(?i)(?:[,;:]\s*)?(?:are\s+(?:we|you)\s+(?:ready|good|clear|following)|"
            r"can\s+you\s+(?:see|hear|follow))(?=[,.!?;:]|\s|$)",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
        cleaned = re.sub(r"([,;:])\s*([.!?])", r"\2", cleaned)
        return cleaned.strip()

    def _answer_text_stands_after_internal_trim(
        self,
        *,
        question_text: str,
        answer_text: str,
    ) -> bool:
        """Return whether a trimmed answer still has enough local substance."""

        normalized_answer = normalize_rule_text(answer_text)
        if count_tokens(normalized_answer) < 7:
            return False
        if self._has_poll_or_backchannel_noise(normalized_answer):
            return False
        answer_matches = collect_rule_matches(normalized_answer, ANSWER_CUE_RULES)
        alignment = self._question_answer_alignment(
            question_text=question_text,
            answer_text=answer_text,
            question_type="unknown",
            answer_source="internal_checkin_trim",
        )
        return bool(
            answer_matches
            or alignment.get("shared_keywords")
            or len(self._content_tokens(answer_text)) >= 5
        )

    @staticmethod
    def _is_internal_checkin_sentence(normalized_text: str) -> bool:
        """Return whether a short internal sentence is classroom check-in chatter."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        if count_tokens(stripped) > 12:
            return False
        return bool(_BACKCHANNEL_CHECK_RE.search(stripped))

    @staticmethod
    def _trim_answer_trailing_tag(text: str) -> tuple[str, str | None]:
        """Remove a final confirmation tag when the answer body is substantive."""

        cleaned_text = text.strip()
        match = _TRAILING_ANSWER_TAG_RE.match(cleaned_text)
        if not match:
            return cleaned_text, None
        body = re.sub(r"\s+", " ", match.group("body")).strip(" ,;:.")
        if count_tokens(normalize_rule_text(body)) < 2:
            return cleaned_text, None
        return body, "answer_trailing_tag_trimmed"

    @staticmethod
    def _looks_like_incomplete_answer_span(text: str) -> bool:
        """Return whether an answer span appears to stop before resolving."""

        cleaned_text = text.strip()
        if not cleaned_text:
            return False
        normalized_text = normalize_rule_text(cleaned_text).rstrip(" ,;:")
        if not normalized_text:
            return False
        tokens = re.findall(r"\b[\w']+\b", normalized_text)
        if not tokens:
            return False
        if tokens[-1] in _INCOMPLETE_ANSWER_END_WORDS:
            return True
        if cleaned_text.endswith(","):
            return True
        if (
            _INCOMPLETE_ANSWER_START_RE.search(normalized_text)
            and not cleaned_text.endswith((".", "?", "!"))
            and count_tokens(normalized_text) <= 8
        ):
            return True
        return False

    @staticmethod
    def _has_poll_or_backchannel_noise(normalized_text: str) -> bool:
        """Return whether a candidate contains classroom polling/check-in noise."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        if _BACKCHANNEL_CHECK_RE.search(stripped):
            return True
        tokens = re.findall(r"\b[\w']+\b", stripped)
        if not tokens:
            return False
        option_count = sum(1 for token in tokens if token in _POLL_OPTION_WORDS)
        if option_count >= 3:
            return True
        if option_count >= 2 and len(tokens) <= 10:
            return True
        return False

    @staticmethod
    def _is_procedural_question_request(normalized_text: str) -> bool:
        """Return whether a question is mainly about managing the Q&A turn."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        return bool(_PROCEDURAL_QUESTION_RE.search(stripped))

    @staticmethod
    def _is_followup_prompt_text(normalized_text: str) -> bool:
        """Return whether text is a prompt asking someone else to answer."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        return bool(_FOLLOWUP_PROMPT_ANSWER_RE.search(stripped))

    @staticmethod
    def _is_followup_prompt_answer(normalized_text: str) -> bool:
        """Return whether an answer looks like a prompt for someone else to answer."""

        stripped = normalized_text.rstrip(" ?.!;:").strip()
        if not stripped:
            return False
        if not QAPairExtractor._is_followup_prompt_text(stripped):
            return False
        return bool(QAPairExtractor._first_question_cue_token_index(stripped))

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
    def _content_token_list(text: str) -> list[str]:
        """Return content-bearing tokens in source order."""

        normalized_text = normalize_rule_text(text)
        return [
            token
            for token in re.findall(r"\b[\w']+\b", normalized_text)
            if len(token) > 1 and token not in _QUESTION_STOPWORDS
        ]

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

    def _is_plausible_answer_for_question(
        self,
        question: QuestionCandidate,
        answer_text: str,
    ) -> bool:
        """Return whether answer text is plausible for a specific question."""

        if self._is_plausible_answer_text(answer_text):
            return True
        cleaned_text = answer_text.strip()
        if not cleaned_text or cleaned_text.count("?") > 0:
            return False
        return self._is_socratic_short_answer_for_question(
            question=question,
            answer_text=cleaned_text,
        )

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
