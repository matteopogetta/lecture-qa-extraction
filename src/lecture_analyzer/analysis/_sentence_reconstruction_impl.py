"""Reconstruct traceable sentences from speaker-aware utterances."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import re
from typing import Any, Sequence
import warnings

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AudioSource,
    LectureSession,
    Sentence,
    SentenceCollection,
    Utterance,
)
from lecture_analyzer.analysis.sentence_provenance import validate_sentence_structure
from lecture_analyzer.transcription.cache_store import (
    CachedSentences,
    SentencePaths,
    TranscriptionCacheStore,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _UtteranceGroup:
    """A contiguous group of utterances that can be sentence-split together."""

    utterances: list[Utterance] = field(default_factory=list)


@dataclass(slots=True)
class _RenderedGroup:
    """One text block rendered from a group of utterances."""

    text: str
    utterance_end_offsets: list[int]


@dataclass(slots=True)
class _SentenceSpan:
    """A half-open utterance span used during sentence refinement."""

    start_index: int
    end_index: int


@dataclass(slots=True)
class _SpeakerEvidenceBucket:
    """Aggregate weighted evidence for one candidate speaker."""

    speaker_id: str
    utterance_count: int = 0
    total_duration_seconds: float = 0.0
    total_word_count: int = 0
    stable_utterance_count: int = 0
    uncertain_utterance_count: int = 0
    short_fragment_count: int = 0
    opening_utterance_count: int = 0
    middle_utterance_count: int = 0
    closing_utterance_count: int = 0
    closing_score: float = 0.0
    stable_score: float = 0.0
    uncertain_score: float = 0.0
    total_score: float = 0.0


@dataclass(slots=True)
class _SentenceSpeakerEvidence:
    """Sentence-level speaker evidence derived from mapped utterances."""

    utterance_count: int
    total_duration_seconds: float
    total_word_count: int
    assigned_utterance_count: int
    uncertain_utterance_count: int
    unassigned_utterance_count: int
    short_fragment_utterance_count: int
    noise_score: float
    candidates: list[_SpeakerEvidenceBucket] = field(default_factory=list)
    dominant_speaker_id: str | None = None
    dominant_weight: float = 0.0
    second_speaker_id: str | None = None
    second_weight: float = 0.0
    dominant_share: float = 0.0
    second_share: float = 0.0
    dominance_margin: float = 0.0


@dataclass(slots=True)
class _SentenceSpeakerDecision:
    """Resolved speaker outcome for one sentence."""

    speaker_id: str | None
    resolution_status: str
    assignment_method: str


class SentenceReconstructor:
    """Build a sentence layer on top of existing utterances."""

    _FALLBACK_BOUNDARY_RE = re.compile(r"(?<=[.!?;:])\s+")
    _WORD_RE = re.compile(r"\b[\w']+\b", flags=re.UNICODE)
    _STRONG_ENDING_PUNCTUATION = (".", "?", "!")
    _WEAK_ENDING_PUNCTUATION = (",", ";", ":", "-", "(", "[", "{", "/")
    _WTPSPLIT_WARNING_PATTERN = (
        r"Torchaudio's I/O functions now support per-call backend dispatch\..*"
    )

    def __init__(
        self,
        config: PipelineConfig,
        splitter: object | None = None,
    ) -> None:
        self.config = config
        self.cache_store = TranscriptionCacheStore(config)
        self._override_splitter = splitter
        self._resolved_splitter: object | None = None
        self._splitter_resolution_attempted = False

    def reconstruct_session(self, session: LectureSession) -> list[Sentence]:
        """Reconstruct and persist sentences for every audio source in a session."""

        if not self.config.sentence_reconstruction_enabled:
            session.sentences = []
            session.metadata["sentence_reconstruction_status"] = "disabled"
            session.metadata["sentence_reconstruction_reason"] = (
                "sentence_reconstruction_disabled"
            )
            session.metadata["sentence_count"] = 0
            session.metadata["sentence_source_count"] = 0
            session.metadata["sentence_artifact_count"] = 0
            for audio_source in session.audio_sources:
                self._apply_source_metadata(
                    audio_source=audio_source,
                    sentence_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="disabled",
                    reason="sentence_reconstruction_disabled",
                )
            return []

        if not session.utterances:
            session.sentences = []
            session.metadata["sentence_reconstruction_status"] = "skipped"
            session.metadata["sentence_reconstruction_reason"] = "utterances_unavailable"
            session.metadata["sentence_count"] = 0
            session.metadata["sentence_source_count"] = 0
            session.metadata["sentence_artifact_count"] = 0
            for audio_source in session.audio_sources:
                self._apply_source_metadata(
                    audio_source=audio_source,
                    sentence_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="skipped",
                    reason="no_utterances",
                )
            return []

        utterances_by_source: dict[str, list[Utterance]] = {}
        for utterance in self._ordered_utterances(session.utterances):
            utterances_by_source.setdefault(utterance.audio_source_id, []).append(
                utterance,
            )

        sentences: list[Sentence] = []
        built_sources = 0
        failed_sources: list[str] = []
        fallback_sources = 0
        requested_backend = self.config.sentence_splitter_backend

        for audio_source in self._ordered_audio_sources(session.audio_sources):
            source_utterances = utterances_by_source.get(audio_source.audio_source_id, [])
            if not source_utterances:
                self._apply_source_metadata(
                    audio_source=audio_source,
                    sentence_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="skipped",
                    reason="no_utterances",
                )
                continue

            try:
                sentence_collection, cache_record, artifact_paths = (
                    self._load_or_build_source(
                        audio_source=audio_source,
                        utterances=source_utterances,
                    )
                )
            except Exception as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.exception(
                    "Unexpected sentence reconstruction failure for %s.",
                    audio_source.audio_source_id,
                )
                self._apply_source_metadata(
                    audio_source=audio_source,
                    sentence_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error) or "sentence_reconstruction_failed",
                )
                if requested_backend == "wtpsplit":
                    raise RuntimeError(
                        "wtpsplit sentence reconstruction failed; install and "
                        "configure wtpsplit or explicitly select "
                        "sentence_splitter_backend='fallback_rules'."
                    ) from error
                continue

            if sentence_collection.metadata.get("splitter_backend") != "wtpsplit_sat":
                fallback_sources += 1
            sentences.extend(sentence_collection.sentences)
            built_sources += 1
            self._apply_source_metadata(
                audio_source=audio_source,
                sentence_collection=sentence_collection,
                cache_record=cache_record,
                artifact_paths=artifact_paths,
                status="available",
                reason=None,
            )

        session.sentences = sentences
        session.metadata["sentence_count"] = len(sentences)
        session.metadata["sentence_source_count"] = built_sources
        session.metadata["sentence_artifact_count"] = built_sources
        session.metadata["sentence_failed_sources"] = failed_sources
        session.metadata["sentence_reconstruction_backend"] = requested_backend
        session.metadata["sentence_reconstruction_max_gap_seconds"] = (
            self.config.sentence_reconstruction_max_gap_seconds
        )
        session.metadata["sentence_reconstruction_fallback_source_count"] = (
            fallback_sources
        )
        session.metadata["sentence_high_priority_count"] = sum(
            1 for sentence in sentences if sentence.review_priority == "high"
        )
        session.metadata["sentence_unassigned_count"] = sum(
            1
            for sentence in sentences
            if sentence.speaker_resolution_status == "unassigned"
        )
        session.metadata["sentence_mixed_count"] = sum(
            1 for sentence in sentences if sentence.speaker_resolution_status == "mixed"
        )
        session.metadata["sentence_shared_source_utterance_count"] = sum(
            _safe_int(
                audio_source.metadata.get("sentences", {}).get(
                    "shared_source_utterance_count",
                ),
            )
            for audio_source in session.audio_sources
        )
        session.metadata["sentence_provenance_anomaly_count"] = sum(
            _safe_int(
                audio_source.metadata.get("sentences", {}).get(
                    "provenance_anomaly_count",
                ),
            )
            for audio_source in session.audio_sources
        )
        session.metadata["sentence_reconstruction_status"] = (
            self._resolve_session_status(
                total_sources=len(session.audio_sources),
                built_sources=built_sources,
                failed_sources=failed_sources,
            )
        )
        if failed_sources:
            session.metadata["sentence_reconstruction_reason"] = (
                "partial_or_failed_source_build"
            )
        elif fallback_sources and requested_backend == "wtpsplit":
            session.metadata["sentence_reconstruction_reason"] = (
                "wtpsplit_unavailable_or_failed_fallback_used"
            )
        else:
            session.metadata.pop("sentence_reconstruction_reason", None)
        return sentences

    def build_source(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
    ) -> SentenceCollection:
        """Build reconstructed sentences from one source-local utterance stream."""

        ordered_utterances = self._ordered_utterances(utterances)
        groups = self._group_utterances(ordered_utterances)
        sentences: list[Sentence] = []
        splitter_backends: list[str] = []
        fallback_reasons: list[str] = []
        source_utterance_offset = 0

        for group in groups:
            sentences_for_group, split_metadata = self._build_group_sentences(
                audio_source=audio_source,
                utterances=group.utterances,
                sentence_offset=len(sentences),
                source_utterance_offset=source_utterance_offset,
            )
            sentences.extend(sentences_for_group)
            splitter_backends.append(split_metadata["splitter_backend"])
            fallback_reason = split_metadata.get("fallback_reason")
            if fallback_reason:
                fallback_reasons.append(str(fallback_reason))
            source_utterance_offset += len(group.utterances)

        detected_language = self._resolve_group_language(ordered_utterances)
        collection_backend = (
            "wtpsplit_sat"
            if splitter_backends and all(
                backend == "wtpsplit_sat" for backend in splitter_backends
            )
            else "fallback_rules"
        )
        sentence_collection = SentenceCollection(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=detected_language,
            source_utterance_count=len(ordered_utterances),
            sentences=sentences,
        )
        sentence_collection = self._consolidate_sentence_collection(
            audio_source=audio_source,
            utterances=ordered_utterances,
            sentence_collection=sentence_collection,
        )
        metadata: dict[str, Any] = {
            "builder": "speaker_aware_sentence_reconstruction",
            "splitter_backend": collection_backend,
            "requested_splitter_backend": self.config.sentence_splitter_backend,
            "splitter_model_name": (
                self.config.sentence_splitter_model_name
                if self.config.sentence_splitter_backend == "wtpsplit"
                else None
            ),
            "group_count": len(groups),
            "respect_speaker_boundaries": (
                self.config.sentence_reconstruction_respect_speaker_boundaries
            ),
            "max_gap_seconds": self.config.sentence_reconstruction_max_gap_seconds,
            "high_priority_sentence_count": sum(
                1
                for sentence in sentence_collection.sentences
                if sentence.review_priority == "high"
            ),
        }
        metadata.update(sentence_collection.metadata)
        if fallback_reasons:
            metadata["fallback_reasons"] = sorted(set(fallback_reasons))

        sentence_collection.metadata = metadata
        return sentence_collection

    def _load_or_build_source(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
    ) -> tuple[SentenceCollection, CachedSentences | None, SentencePaths]:
        """Load cached sentences when possible, otherwise build and persist them."""

        artifact_found = self.cache_store.has_sentence_artifact(audio_source)
        cached_sentences = self.cache_store.load_sentences(
            audio_source=audio_source,
            utterances=utterances,
        )
        if cached_sentences is not None:
            sentence_collection = self._consolidate_sentence_collection(
                audio_source=audio_source,
                utterances=utterances,
                sentence_collection=cached_sentences.sentence_collection,
            )
            sentence_collection.metadata["cache_hit"] = True
            sentence_collection.metadata["used_cache"] = False
            sentence_collection.metadata["used_existing_artifact"] = True
            sentence_collection.metadata["artifact_reuse_enabled"] = True
            sentence_collection.metadata["artifact_found"] = True
            sentence_collection.metadata["artifact_reused"] = True
            sentence_collection.metadata["artifact_ignored_due_to_force_recompute"] = (
                False
            )
            sentence_collection.metadata["recomputed"] = False
            sentence_collection.metadata["forced_recompute"] = False
            sentence_collection.metadata["artifact_manifest_path"] = str(
                cached_sentences.paths.manifest_path,
            )
            return (
                sentence_collection,
                cached_sentences,
                cached_sentences.paths,
            )

        if self.config.force_recompute and artifact_found:
            LOGGER.info(
                "Ignoring sentence artifact for %s because run mode is from scratch.",
                audio_source.audio_source_id,
            )

        sentence_collection = self.build_source(audio_source, utterances)
        sentence_collection.metadata["cache_hit"] = False
        sentence_collection.metadata["used_cache"] = False
        sentence_collection.metadata["used_existing_artifact"] = False
        sentence_collection.metadata["artifact_reuse_enabled"] = (
            self.config.intermediate_artifact_reuse_enabled
        )
        sentence_collection.metadata["artifact_found"] = artifact_found
        sentence_collection.metadata["artifact_reused"] = False
        sentence_collection.metadata["artifact_ignored_due_to_force_recompute"] = (
            artifact_found and self.config.force_recompute
        )
        sentence_collection.metadata["recomputed"] = True
        sentence_collection.metadata["forced_recompute"] = self.config.force_recompute
        artifact_paths = self.cache_store.save_sentences(
            audio_source=audio_source,
            utterances=utterances,
            sentence_collection=sentence_collection,
        )
        sentence_collection.metadata["artifact_manifest_path"] = str(
            artifact_paths.manifest_path,
        )
        return sentence_collection, None, artifact_paths

    def _build_group_sentences(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        sentence_offset: int,
        source_utterance_offset: int,
    ) -> tuple[list[Sentence], dict[str, Any]]:
        """Build sentences for one contiguous utterance group."""

        rendered_group = self._render_group(utterances)
        if not rendered_group.text:
            return [], {
                "splitter_backend": "fallback_rules",
                "fallback_reason": "empty_group_text",
            }

        split_texts, split_metadata = self._split_text(
            text=rendered_group.text,
            language_code=self._resolve_group_language(utterances),
        )
        boundary_indexes = self._resolve_boundary_indexes(
            text=rendered_group.text,
            split_texts=split_texts,
            utterance_end_offsets=rendered_group.utterance_end_offsets,
        )
        spans = self._boundary_indexes_to_spans(boundary_indexes, len(utterances))
        refined_spans = self._refine_spans(spans, utterances)
        sentences = self._materialize_sentences(
            audio_source=audio_source,
            utterances=utterances,
            spans=refined_spans,
            sentence_offset=sentence_offset,
            source_utterance_offset=source_utterance_offset,
        )
        return sentences, split_metadata

    def _consolidate_sentence_collection(
        self,
        *,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        sentence_collection: SentenceCollection,
    ) -> SentenceCollection:
        """Apply deterministic speaker and semantic consolidation to sentences."""

        utterance_lookup = {
            utterance.utterance_id: utterance
            for utterance in utterances
        }
        consolidated_sentences = []
        for sentence in sentence_collection.sentences:
            bound_utterances = [
                utterance_lookup[utterance_id]
                for utterance_id in sentence.source_utterance_ids
                if utterance_id in utterance_lookup
            ]
            consolidated_sentences.append(
                self._consolidate_sentence(
                    audio_source=audio_source,
                    sentence=sentence,
                    utterances=bound_utterances,
                ),
            )

        self._recover_unassigned_sentence_speakers(consolidated_sentences)
        sentence_collection.sentences = consolidated_sentences
        provenance_diagnostics = self._provenance_diagnostics(
            utterances=utterances,
            sentences=consolidated_sentences,
        )
        sentence_collection.metadata["semantic_fragment_count"] = sum(
            1
            for sentence in consolidated_sentences
            if sentence.semantic_quality_label == "fragment"
        )
        sentence_collection.metadata["semantic_run_on_count"] = sum(
            1
            for sentence in consolidated_sentences
            if sentence.semantic_quality_label == "run_on"
        )
        sentence_collection.metadata["speaker_mixed_count"] = sum(
            1
            for sentence in consolidated_sentences
            if sentence.speaker_resolution_status == "mixed"
        )
        sentence_collection.metadata["speaker_unassigned_count"] = sum(
            1
            for sentence in consolidated_sentences
            if sentence.speaker_resolution_status == "unassigned"
        )
        sentence_collection.metadata.update(provenance_diagnostics)
        return sentence_collection

    def _consolidate_sentence(
        self,
        *,
        audio_source: AudioSource,
        sentence: Sentence,
        utterances: Sequence[Utterance],
    ) -> Sentence:
        """Enrich one sentence with deterministic speaker and quality metadata."""

        del audio_source
        sentence.source_utterance_ids = self._resolve_final_source_utterance_ids(
            utterances=utterances,
            fallback_utterance_ids=sentence.source_utterance_ids,
        )
        sentence.sentence_review_flags = list(sentence.sentence_review_flags)
        text = sentence.text.strip()
        word_count = self._word_count(text)
        duration_seconds = self._sentence_duration_seconds(sentence)
        speaker_evidence = self._build_sentence_speaker_evidence(utterances)
        assigned_speakers = [
            candidate.speaker_id
            for candidate in speaker_evidence.candidates
        ]
        uncertain_count = speaker_evidence.uncertain_utterance_count
        unassigned_count = speaker_evidence.unassigned_utterance_count
        has_speaker_change_inside = self._has_speaker_change_inside(utterances)
        total_utterances = len(utterances)
        speaker_decision = self._resolve_direct_sentence_speaker(speaker_evidence)

        fragment_signals = self._semantic_fragment_signals(text, word_count, duration_seconds)
        run_on_signals = self._semantic_run_on_signals(text, word_count, duration_seconds)
        semantic_quality_label = self._semantic_quality_label(
            fragment_signals=fragment_signals,
            run_on_signals=run_on_signals,
        )
        semantic_cleanup = self._sentence_semantic_cleanup_features(
            text=text,
            word_count=word_count,
            fragment_signals=fragment_signals,
            run_on_signals=run_on_signals,
            semantic_quality_label=semantic_quality_label,
        )
        sentence.semantic_quality_label = semantic_quality_label
        sentence.length_bucket = self._length_bucket(word_count)
        sentence.duration_bucket = self._duration_bucket(duration_seconds)

        metadata = dict(sentence.metadata)
        metadata["source_utterance_count"] = total_utterances
        metadata["speaker_boundary_respected"] = not has_speaker_change_inside
        metadata["has_uncertain_source"] = uncertain_count > 0
        metadata["has_unassigned_source"] = unassigned_count > 0
        metadata["has_speaker_change_inside"] = has_speaker_change_inside
        metadata["is_multi_utterance"] = total_utterances > 1
        metadata["is_semantic_fragment"] = semantic_quality_label == "fragment"
        metadata["is_semantic_run_on"] = semantic_quality_label == "run_on"
        metadata["is_merge_risky"] = False
        metadata["word_count"] = word_count
        metadata["duration_seconds"] = round(duration_seconds, 3)
        metadata["speaker_evidence"] = self._serialize_speaker_evidence(speaker_evidence)
        metadata["source_speaker_ids"] = assigned_speakers
        metadata["distinct_source_speaker_count"] = len(assigned_speakers)
        metadata["assigned_source_utterance_count"] = (
            speaker_evidence.assigned_utterance_count - uncertain_count
        )
        metadata["uncertain_source_utterance_count"] = uncertain_count
        metadata["unassigned_source_utterance_count"] = unassigned_count
        metadata["short_fragment_source_utterance_count"] = (
            speaker_evidence.short_fragment_utterance_count
        )
        metadata["semantic_fragment_signals"] = fragment_signals
        metadata["semantic_run_on_signals"] = run_on_signals
        metadata["semantic_cleanup"] = semantic_cleanup
        sentence.metadata = metadata
        self._apply_sentence_speaker_decision(
            sentence=sentence,
            speaker_evidence=speaker_evidence,
            speaker_decision=speaker_decision,
        )
        self._refresh_sentence_review_fields(sentence)
        return sentence

    def _build_sentence_speaker_evidence(
        self,
        utterances: Sequence[Utterance],
    ) -> _SentenceSpeakerEvidence:
        """Return weighted speaker evidence for one mapped sentence."""

        total_duration_seconds = round(
            sum(self._utterance_duration_seconds(utterance) for utterance in utterances),
            3,
        )
        total_word_count = sum(self._word_count(utterance.text) for utterance in utterances)
        evidence = _SentenceSpeakerEvidence(
            utterance_count=len(utterances),
            total_duration_seconds=total_duration_seconds,
            total_word_count=total_word_count,
            assigned_utterance_count=0,
            uncertain_utterance_count=0,
            unassigned_utterance_count=0,
            short_fragment_utterance_count=0,
            noise_score=0.0,
        )
        bucket_by_speaker: dict[str, _SpeakerEvidenceBucket] = {}
        last_index = max(len(utterances) - 1, 0)

        for index, utterance in enumerate(utterances):
            speaker_id = (utterance.speaker_id or "").strip() or None
            is_uncertain = bool(utterance.speaker_is_uncertain)
            word_count = self._word_count(utterance.text)
            duration_seconds = self._utterance_duration_seconds(utterance)
            is_short_fragment = self._is_short_fragment_utterance(
                duration_seconds=duration_seconds,
                word_count=word_count,
            )
            if is_short_fragment:
                evidence.short_fragment_utterance_count += 1

            position_label = "middle"
            if index == 0:
                position_label = "opening"
            if index == last_index:
                position_label = "closing"

            contribution_score = self._speaker_contribution_score(
                duration_seconds=duration_seconds,
                word_count=word_count,
                has_assigned_speaker=speaker_id is not None,
                is_uncertain=is_uncertain,
                is_short_fragment=is_short_fragment,
                is_closing=position_label == "closing",
            )

            if speaker_id is None:
                evidence.unassigned_utterance_count += 1
                evidence.noise_score += contribution_score
                continue

            evidence.assigned_utterance_count += 1
            if is_uncertain:
                evidence.uncertain_utterance_count += 1

            speaker_bucket = bucket_by_speaker.setdefault(
                speaker_id,
                _SpeakerEvidenceBucket(speaker_id=speaker_id),
            )
            speaker_bucket.utterance_count += 1
            speaker_bucket.total_duration_seconds = round(
                speaker_bucket.total_duration_seconds + duration_seconds,
                3,
            )
            speaker_bucket.total_word_count += word_count
            if is_uncertain:
                speaker_bucket.uncertain_utterance_count += 1
                speaker_bucket.uncertain_score += contribution_score
            else:
                speaker_bucket.stable_utterance_count += 1
                speaker_bucket.stable_score += contribution_score
            if is_short_fragment:
                speaker_bucket.short_fragment_count += 1
            if position_label == "opening":
                speaker_bucket.opening_utterance_count += 1
            elif position_label == "closing":
                speaker_bucket.closing_utterance_count += 1
                speaker_bucket.closing_score += contribution_score
            else:
                speaker_bucket.middle_utterance_count += 1
            speaker_bucket.total_score += contribution_score

        ranked_candidates = sorted(
            bucket_by_speaker.values(),
            key=lambda candidate: (
                candidate.total_score,
                candidate.stable_score,
                candidate.total_duration_seconds,
                candidate.total_word_count,
                candidate.speaker_id,
            ),
            reverse=True,
        )
        evidence.candidates = ranked_candidates
        if ranked_candidates:
            dominant_candidate = ranked_candidates[0]
            evidence.dominant_speaker_id = dominant_candidate.speaker_id
            evidence.dominant_weight = dominant_candidate.total_score
            total_assigned_weight = sum(
                candidate.total_score for candidate in ranked_candidates
            )
            if total_assigned_weight > 0:
                evidence.dominant_share = dominant_candidate.total_score / total_assigned_weight
            if len(ranked_candidates) > 1:
                second_candidate = ranked_candidates[1]
                evidence.second_speaker_id = second_candidate.speaker_id
                evidence.second_weight = second_candidate.total_score
                if total_assigned_weight > 0:
                    evidence.second_share = second_candidate.total_score / total_assigned_weight
            evidence.dominance_margin = (
                evidence.dominant_weight - evidence.second_weight
            )
        return evidence

    def _speaker_contribution_score(
        self,
        *,
        duration_seconds: float,
        word_count: int,
        has_assigned_speaker: bool,
        is_uncertain: bool,
        is_short_fragment: bool,
        is_closing: bool,
    ) -> float:
        """Return one rule-based evidence contribution for a source utterance."""

        duration_reference = max(
            self.config.sentence_speaker_duration_reference_seconds,
            0.1,
        )
        word_reference = max(self.config.sentence_speaker_word_reference_count, 1)
        duration_component = min(duration_seconds / duration_reference, 1.0)
        word_component = min(word_count / word_reference, 1.0)
        if not has_assigned_speaker:
            score = self.config.sentence_speaker_unassigned_noise_weight
        elif is_uncertain:
            score = self.config.sentence_speaker_uncertain_weight
        else:
            score = 1.0
        score += self.config.sentence_speaker_duration_weight * duration_component
        score += self.config.sentence_speaker_word_weight * word_component
        if is_short_fragment:
            score *= self.config.sentence_speaker_short_fragment_weight
        if is_closing:
            score *= self.config.sentence_speaker_final_utterance_bonus
        return round(score, 4)

    def _is_short_fragment_utterance(
        self,
        *,
        duration_seconds: float,
        word_count: int,
    ) -> bool:
        """Return whether one utterance is too short to be strong evidence."""

        return (
            duration_seconds <= self.config.sentence_speaker_short_fragment_duration_seconds
            or word_count <= self.config.sentence_speaker_short_fragment_word_count
        )

    @staticmethod
    def _utterance_duration_seconds(utterance: Utterance) -> float:
        """Return a non-negative utterance duration."""

        return max(0.0, float(utterance.end_seconds) - float(utterance.start_seconds))

    def _serialize_speaker_evidence(
        self,
        speaker_evidence: _SentenceSpeakerEvidence,
    ) -> dict[str, Any]:
        """Return a JSON-friendly speaker evidence payload for debug export."""

        return {
            "assigned_count": speaker_evidence.assigned_utterance_count,
            "stable_assigned_count": (
                speaker_evidence.assigned_utterance_count
                - speaker_evidence.uncertain_utterance_count
            ),
            "uncertain_count": speaker_evidence.uncertain_utterance_count,
            "unassigned_count": speaker_evidence.unassigned_utterance_count,
            "short_fragment_count": speaker_evidence.short_fragment_utterance_count,
            "distinct_source_speaker_count": len(speaker_evidence.candidates),
            "assigned_speakers": [
                candidate.speaker_id for candidate in speaker_evidence.candidates
            ],
            "dominant_speaker": speaker_evidence.dominant_speaker_id,
            "dominant_weight": round(speaker_evidence.dominant_weight, 4),
            "second_speaker": speaker_evidence.second_speaker_id,
            "second_weight": round(speaker_evidence.second_weight, 4),
            "dominant_share": round(speaker_evidence.dominant_share, 4),
            "second_share": round(speaker_evidence.second_share, 4),
            "dominance_margin": round(speaker_evidence.dominance_margin, 4),
            "noise_score": round(speaker_evidence.noise_score, 4),
            "candidates": [
                {
                    "speaker_id": candidate.speaker_id,
                    "utterance_count": candidate.utterance_count,
                    "total_duration_seconds": round(
                        candidate.total_duration_seconds,
                        3,
                    ),
                    "total_word_count": candidate.total_word_count,
                    "stable_utterance_count": candidate.stable_utterance_count,
                    "uncertain_utterance_count": candidate.uncertain_utterance_count,
                    "short_fragment_count": candidate.short_fragment_count,
                    "opening_utterance_count": candidate.opening_utterance_count,
                    "middle_utterance_count": candidate.middle_utterance_count,
                    "closing_utterance_count": candidate.closing_utterance_count,
                    "closing_score": round(candidate.closing_score, 4),
                    "stable_score": round(candidate.stable_score, 4),
                    "uncertain_score": round(candidate.uncertain_score, 4),
                    "total_score": round(candidate.total_score, 4),
                }
                for candidate in speaker_evidence.candidates
            ],
        }

    def _resolve_direct_sentence_speaker(
        self,
        speaker_evidence: _SentenceSpeakerEvidence,
    ) -> _SentenceSpeakerDecision:
        """Return the direct sentence-level speaker decision before recovery."""

        if not speaker_evidence.candidates:
            return _SentenceSpeakerDecision(
                speaker_id=None,
                resolution_status="unassigned",
                assignment_method="insufficient_evidence",
            )

        dominant_candidate = speaker_evidence.candidates[0]
        if (
            speaker_evidence.dominant_share >= self.config.sentence_speaker_dominant_share_threshold
            and speaker_evidence.dominance_margin >= self.config.sentence_speaker_dominance_margin_threshold
            and (
                speaker_evidence.dominant_weight > speaker_evidence.noise_score
                or dominant_candidate.stable_score > dominant_candidate.uncertain_score
            )
            and not (
                dominant_candidate.stable_score <= 0.0
                and speaker_evidence.noise_score
                >= (speaker_evidence.dominant_weight * 0.75)
            )
        ):
            resolution_status = self._classify_direct_speaker_status(
                speaker_evidence=speaker_evidence,
                dominant_candidate=dominant_candidate,
            )
            assignment_method = "direct_weighted_majority"
            if resolution_status == "stable":
                assignment_method = "direct_stable_majority"
            elif resolution_status == "uncertain":
                assignment_method = "direct_uncertain_majority"
            return _SentenceSpeakerDecision(
                speaker_id=dominant_candidate.speaker_id,
                resolution_status=resolution_status,
                assignment_method=assignment_method,
            )

        if (
            speaker_evidence.second_weight > 0.0
            and speaker_evidence.second_share >= self.config.sentence_speaker_conflict_share_threshold
        ):
            return _SentenceSpeakerDecision(
                speaker_id=None,
                resolution_status="mixed",
                assignment_method="mixed_conflict",
            )

        return _SentenceSpeakerDecision(
            speaker_id=None,
            resolution_status="unassigned",
            assignment_method="insufficient_evidence",
        )

    @staticmethod
    def _classify_direct_speaker_status(
        *,
        speaker_evidence: _SentenceSpeakerEvidence,
        dominant_candidate: _SpeakerEvidenceBucket,
    ) -> str:
        """Return a confidence-oriented status for one direct decision."""

        if (
            speaker_evidence.second_weight <= 0.0
            and speaker_evidence.uncertain_utterance_count <= 0
            and speaker_evidence.unassigned_utterance_count <= 0
            and dominant_candidate.uncertain_utterance_count <= 0
        ):
            return "stable"

        if (
            dominant_candidate.stable_utterance_count > 0
            and dominant_candidate.stable_score >= dominant_candidate.uncertain_score
            and speaker_evidence.dominant_share >= 0.66
        ):
            return "mostly_stable"

        return "uncertain"

    def _recover_unassigned_sentence_speakers(
        self,
        sentences: Sequence[Sentence],
    ) -> None:
        """Apply controlled recovery only to sentences that still lack a speaker."""

        for index, sentence in enumerate(sentences):
            if (sentence.speaker_id or "").strip():
                continue
            if sentence.speaker_resolution_status != "unassigned":
                continue

            speaker_evidence = self._speaker_evidence_from_sentence(sentence)
            internal_recovery = self._try_internal_speaker_recovery(speaker_evidence)
            if internal_recovery is not None:
                self._apply_sentence_speaker_decision(
                    sentence=sentence,
                    speaker_evidence=speaker_evidence,
                    speaker_decision=internal_recovery,
                )
                self._refresh_sentence_review_fields(sentence)
                continue

            neighbor_recovery = self._try_neighbor_context_recovery(
                sentence_index=index,
                sentences=sentences,
                speaker_evidence=speaker_evidence,
            )
            if neighbor_recovery is not None:
                self._apply_sentence_speaker_decision(
                    sentence=sentence,
                    speaker_evidence=speaker_evidence,
                    speaker_decision=neighbor_recovery,
                )
                self._refresh_sentence_review_fields(sentence)

    def _try_internal_speaker_recovery(
        self,
        speaker_evidence: _SentenceSpeakerEvidence,
    ) -> _SentenceSpeakerDecision | None:
        """Return a permissive but controlled internal majority recovery."""

        if not speaker_evidence.candidates:
            return None
        if (
            speaker_evidence.second_share >= self.config.sentence_speaker_conflict_share_threshold
            or speaker_evidence.dominant_share < self.config.sentence_speaker_internal_recovery_share_threshold
            or speaker_evidence.dominance_margin < self.config.sentence_speaker_internal_recovery_margin_threshold
        ):
            return None

        dominant_candidate = speaker_evidence.candidates[0]
        if (
            speaker_evidence.noise_score > speaker_evidence.dominant_weight
            and dominant_candidate.stable_score <= dominant_candidate.uncertain_score
        ):
            return None
        if (
            dominant_candidate.stable_score <= 0.0
            and speaker_evidence.noise_score
            >= (speaker_evidence.dominant_weight * 0.75)
        ):
            return None

        return _SentenceSpeakerDecision(
            speaker_id=dominant_candidate.speaker_id,
            resolution_status="uncertain",
            assignment_method="recovered_from_internal_majority",
        )

    def _try_neighbor_context_recovery(
        self,
        *,
        sentence_index: int,
        sentences: Sequence[Sentence],
        speaker_evidence: _SentenceSpeakerEvidence,
    ) -> _SentenceSpeakerDecision | None:
        """Return a conservative context-based recovery when neighbors agree."""

        current_sentence = sentences[sentence_index]
        word_count = self._sentence_word_count(current_sentence)
        duration_seconds = self._sentence_duration_seconds(current_sentence)
        if (
            duration_seconds > self.config.sentence_speaker_context_recovery_max_duration_seconds
            or word_count > self.config.sentence_speaker_context_recovery_max_word_count
            or speaker_evidence.second_share > self.config.sentence_speaker_context_recovery_max_conflict_share
        ):
            return None

        previous_sentence = (
            sentences[sentence_index - 1]
            if sentence_index > 0
            else None
        )
        next_sentence = (
            sentences[sentence_index + 1]
            if sentence_index + 1 < len(sentences)
            else None
        )
        previous_speaker = self._stable_neighbor_speaker(
            candidate=previous_sentence,
            current_sentence=current_sentence,
            direction="previous",
        )
        next_speaker = self._stable_neighbor_speaker(
            candidate=next_sentence,
            current_sentence=current_sentence,
            direction="next",
        )
        if previous_speaker and next_speaker and previous_speaker == next_speaker:
            return _SentenceSpeakerDecision(
                speaker_id=previous_speaker,
                resolution_status="uncertain",
                assignment_method="recovered_from_neighbor_consensus",
            )
        if previous_speaker and next_speaker is None:
            return _SentenceSpeakerDecision(
                speaker_id=previous_speaker,
                resolution_status="uncertain",
                assignment_method="recovered_from_previous_sentence",
            )
        if next_speaker and previous_speaker is None:
            return _SentenceSpeakerDecision(
                speaker_id=next_speaker,
                resolution_status="uncertain",
                assignment_method="recovered_from_next_sentence",
            )
        return None

    def _stable_neighbor_speaker(
        self,
        *,
        candidate: Sentence | None,
        current_sentence: Sentence,
        direction: str,
    ) -> str | None:
        """Return a stable neighboring speaker when continuity is plausible."""

        if candidate is None:
            return None
        speaker_id = (candidate.speaker_id or "").strip() or None
        if speaker_id is None:
            return None
        if candidate.speaker_resolution_status not in {"stable", "mostly_stable"}:
            return None
        if self._sentence_gap_seconds(
            previous_sentence=(candidate if direction == "previous" else current_sentence),
            next_sentence=(current_sentence if direction == "previous" else candidate),
        ) > self.config.sentence_speaker_context_recovery_max_gap_seconds:
            return None
        return speaker_id

    @staticmethod
    def _sentence_gap_seconds(
        *,
        previous_sentence: Sentence,
        next_sentence: Sentence,
    ) -> float:
        """Return the non-negative gap between two ordered sentences."""

        return max(
            0.0,
            float(next_sentence.start_seconds) - float(previous_sentence.end_seconds),
        )

    def _speaker_evidence_from_sentence(
        self,
        sentence: Sentence,
    ) -> _SentenceSpeakerEvidence:
        """Rebuild typed speaker evidence from persisted sentence metadata."""

        metadata = dict(sentence.metadata)
        raw_evidence = dict(metadata.get("speaker_evidence") or {})
        candidates = [
            _SpeakerEvidenceBucket(
                speaker_id=str(candidate.get("speaker_id") or ""),
                utterance_count=_safe_int(candidate.get("utterance_count"), fallback=0),
                total_duration_seconds=_safe_float(
                    candidate.get("total_duration_seconds"),
                    fallback=0.0,
                )
                or 0.0,
                total_word_count=_safe_int(candidate.get("total_word_count"), fallback=0),
                stable_utterance_count=_safe_int(
                    candidate.get("stable_utterance_count"),
                    fallback=0,
                ),
                uncertain_utterance_count=_safe_int(
                    candidate.get("uncertain_utterance_count"),
                    fallback=0,
                ),
                short_fragment_count=_safe_int(
                    candidate.get("short_fragment_count"),
                    fallback=0,
                ),
                opening_utterance_count=_safe_int(
                    candidate.get("opening_utterance_count"),
                    fallback=0,
                ),
                middle_utterance_count=_safe_int(
                    candidate.get("middle_utterance_count"),
                    fallback=0,
                ),
                closing_utterance_count=_safe_int(
                    candidate.get("closing_utterance_count"),
                    fallback=0,
                ),
                closing_score=_safe_float(candidate.get("closing_score"), fallback=0.0)
                or 0.0,
                stable_score=_safe_float(candidate.get("stable_score"), fallback=0.0)
                or 0.0,
                uncertain_score=_safe_float(candidate.get("uncertain_score"), fallback=0.0)
                or 0.0,
                total_score=_safe_float(candidate.get("total_score"), fallback=0.0)
                or 0.0,
            )
            for candidate in raw_evidence.get("candidates", [])
            if str(candidate.get("speaker_id") or "").strip()
        ]
        return _SentenceSpeakerEvidence(
            utterance_count=_safe_int(metadata.get("source_utterance_count"), fallback=0),
            total_duration_seconds=_safe_float(
                metadata.get("duration_seconds"),
                fallback=self._sentence_duration_seconds(sentence),
            )
            or 0.0,
            total_word_count=_safe_int(
                metadata.get("word_count"),
                fallback=self._sentence_word_count(sentence),
            ),
            assigned_utterance_count=_safe_int(raw_evidence.get("assigned_count"), fallback=0),
            uncertain_utterance_count=_safe_int(
                raw_evidence.get("uncertain_count"),
                fallback=0,
            ),
            unassigned_utterance_count=_safe_int(
                raw_evidence.get("unassigned_count"),
                fallback=0,
            ),
            short_fragment_utterance_count=_safe_int(
                raw_evidence.get("short_fragment_count"),
                fallback=0,
            ),
            noise_score=_safe_float(raw_evidence.get("noise_score"), fallback=0.0) or 0.0,
            candidates=candidates,
            dominant_speaker_id=(
                str(raw_evidence.get("dominant_speaker") or "").strip() or None
            ),
            dominant_weight=_safe_float(raw_evidence.get("dominant_weight"), fallback=0.0)
            or 0.0,
            second_speaker_id=(
                str(raw_evidence.get("second_speaker") or "").strip() or None
            ),
            second_weight=_safe_float(raw_evidence.get("second_weight"), fallback=0.0)
            or 0.0,
            dominant_share=_safe_float(raw_evidence.get("dominant_share"), fallback=0.0)
            or 0.0,
            second_share=_safe_float(raw_evidence.get("second_share"), fallback=0.0)
            or 0.0,
            dominance_margin=_safe_float(
                raw_evidence.get("dominance_margin"),
                fallback=0.0,
            )
            or 0.0,
        )

    def _apply_sentence_speaker_decision(
        self,
        *,
        sentence: Sentence,
        speaker_evidence: _SentenceSpeakerEvidence,
        speaker_decision: _SentenceSpeakerDecision,
    ) -> None:
        """Persist one speaker decision and its debug metadata on a sentence."""

        metadata = dict(sentence.metadata)
        sentence.speaker_id = speaker_decision.speaker_id
        sentence.speaker_resolution_status = speaker_decision.resolution_status
        sentence.speaker_confidence_label = self._resolve_speaker_confidence_label(
            speaker_decision.resolution_status,
        )
        sentence.speaker_stability_label = self._resolve_speaker_stability_label(
            speaker_decision.resolution_status,
        )
        sentence.speaker_assignment_method = speaker_decision.assignment_method
        sentence.speaker_evidence_summary = self._speaker_evidence_summary(
            speaker_evidence=speaker_evidence,
            speaker_decision=speaker_decision,
        )

        metadata["speaker_assignment_method"] = speaker_decision.assignment_method
        metadata["speaker_resolution_status"] = speaker_decision.resolution_status
        metadata["speaker_confidence_label"] = sentence.speaker_confidence_label
        metadata["speaker_stability_label"] = sentence.speaker_stability_label
        metadata["dominant_speaker_weight"] = round(
            speaker_evidence.dominant_weight,
            4,
        )
        metadata["second_speaker_weight"] = round(
            speaker_evidence.second_weight,
            4,
        )
        metadata["dominance_margin"] = round(
            speaker_evidence.dominance_margin,
            4,
        )
        metadata["dominant_speaker_share"] = round(
            speaker_evidence.dominant_share,
            4,
        )
        sentence.metadata = metadata

    def _refresh_sentence_review_fields(self, sentence: Sentence) -> None:
        """Recompute review-oriented fields after the final speaker decision."""

        metadata = dict(sentence.metadata)
        semantic_quality_label = str(sentence.semantic_quality_label or "good").strip()
        source_utterance_count = _safe_int(metadata.get("source_utterance_count"), fallback=0)
        has_uncertain_source = bool(metadata.get("has_uncertain_source"))
        has_unassigned_source = bool(metadata.get("has_unassigned_source"))
        has_speaker_change_inside = bool(metadata.get("has_speaker_change_inside"))
        is_multi_utterance = bool(metadata.get("is_multi_utterance"))

        merge_safety_label = self._merge_safety_label(
            semantic_quality_label=semantic_quality_label,
            speaker_resolution_status=str(sentence.speaker_resolution_status or ""),
            has_speaker_change_inside=has_speaker_change_inside,
            has_uncertain_source=has_uncertain_source,
            has_unassigned_source=has_unassigned_source,
            utterance_count=source_utterance_count,
        )
        sentence.merge_safety_label = merge_safety_label
        sentence.sentence_review_flags = self._sentence_review_flags(
            semantic_quality_label=semantic_quality_label,
            merge_safety_label=merge_safety_label,
            has_uncertain_source=has_uncertain_source,
            has_unassigned_source=has_unassigned_source,
            has_speaker_change_inside=has_speaker_change_inside,
            is_multi_utterance=is_multi_utterance,
        )
        semantic_cleanup = metadata.get("semantic_cleanup")
        if isinstance(semantic_cleanup, dict):
            autonomy_score = _safe_float(
                semantic_cleanup.get("sentence_autonomy_score"),
                fallback=1.0,
            )
            boundary_score = _safe_float(
                semantic_cleanup.get("boundary_confidence_score"),
                fallback=1.0,
            )
            if autonomy_score < 0.45:
                sentence.sentence_review_flags.append("low_sentence_autonomy")
            if boundary_score < 0.45:
                sentence.sentence_review_flags.append("low_boundary_confidence")
        sentence.review_priority = self._review_priority(
            speaker_resolution_status=str(sentence.speaker_resolution_status or ""),
            semantic_quality_label=semantic_quality_label,
            merge_safety_label=merge_safety_label,
        )
        metadata["is_merge_risky"] = merge_safety_label == "risky"
        sentence.metadata = metadata

    def _speaker_evidence_summary(
        self,
        *,
        speaker_evidence: _SentenceSpeakerEvidence,
        speaker_decision: _SentenceSpeakerDecision,
    ) -> str:
        """Return a compact speaker decision summary for debug output."""

        dominant_label = speaker_evidence.dominant_speaker_id or "none"
        second_label = speaker_evidence.second_speaker_id or "none"
        return (
            f"method={speaker_decision.assignment_method}; "
            f"dominant={dominant_label}:{speaker_evidence.dominant_weight:.2f}; "
            f"second={second_label}:{speaker_evidence.second_weight:.2f}; "
            f"share={speaker_evidence.dominant_share:.2f}; "
            f"margin={speaker_evidence.dominance_margin:.2f}; "
            f"assigned={speaker_evidence.assigned_utterance_count}; "
            f"uncertain={speaker_evidence.uncertain_utterance_count}; "
            f"unassigned={speaker_evidence.unassigned_utterance_count}; "
            f"short={speaker_evidence.short_fragment_utterance_count}"
        )

    @staticmethod
    def _sentence_word_count(sentence: Sentence) -> int:
        """Return the stored or derived word count for one sentence."""

        metadata = dict(sentence.metadata)
        stored_word_count = _safe_int(metadata.get("word_count"))
        if stored_word_count is not None:
            return stored_word_count
        return len([token for token in sentence.text.split() if token])

    @staticmethod
    def _provenance_diagnostics(
        *,
        utterances: Sequence[Utterance],
        sentences: Sequence[Sentence],
    ) -> dict[str, Any]:
        """Return sentence-level provenance diagnostics for debug visibility."""

        validation = validate_sentence_structure(
            utterances=utterances,
            sentences=sentences,
        )

        return {
            "shared_source_utterance_count": (
                validation.utterances_assigned_to_multiple_sentences
            ),
            "sentences_with_shared_provenance_count": (
                validation.sentences_with_provenance_overlap_count
            ),
            "max_source_utterance_reuse": validation.max_sentence_reuse_per_utterance,
            "duplicate_source_utterance_id_count": (
                validation.sentences_with_duplicate_source_utterance_ids
            ),
            "utterance_without_sentence_count": (
                validation.utterance_without_sentence_count
            ),
            "sentence_assignment_total": validation.sentence_assignment_total,
            "all_sentences_have_provenance_overlap": (
                validation.all_sentences_have_provenance_overlap
            ),
            "provenance_mapping_conflict_examples": validation.mapping_conflict_examples,
            "provenance_overlap_examples": validation.overlap_examples,
            "provenance_duplicate_source_examples": (
                validation.duplicate_source_examples
            ),
            "provenance_empty_source_examples": validation.empty_source_examples,
            "provenance_anomaly_count": validation.provenance_anomaly_count,
        }

    def _group_utterances(
        self,
        utterances: Sequence[Utterance],
    ) -> list[_UtteranceGroup]:
        """Partition utterances using source order, timing, and speaker changes."""

        groups: list[_UtteranceGroup] = []
        current_group = _UtteranceGroup()

        for utterance in utterances:
            if not utterance.text.strip():
                continue
            if not current_group.utterances:
                current_group.utterances.append(utterance)
                continue

            previous_utterance = current_group.utterances[-1]
            if self._should_break_group(previous_utterance, utterance):
                groups.append(current_group)
                current_group = _UtteranceGroup(utterances=[utterance])
                continue

            current_group.utterances.append(utterance)

        if current_group.utterances:
            groups.append(current_group)
        return groups

    def _should_break_group(
        self,
        previous_utterance: Utterance,
        current_utterance: Utterance,
    ) -> bool:
        """Return whether the current utterance must start a new group."""

        gap_seconds = max(
            0.0,
            float(current_utterance.start_seconds) - float(previous_utterance.end_seconds),
        )
        if gap_seconds > self.config.sentence_reconstruction_max_gap_seconds:
            return True

        if not self.config.sentence_reconstruction_respect_speaker_boundaries:
            return False
        return self._is_strong_speaker_boundary(previous_utterance, current_utterance)

    @staticmethod
    def _render_group(utterances: Sequence[Utterance]) -> _RenderedGroup:
        """Join utterance texts while keeping utterance boundary offsets."""

        parts: list[str] = []
        utterance_end_offsets: list[int] = []
        current_length = 0

        for utterance in utterances:
            text = utterance.text.strip()
            if not text:
                continue
            if parts:
                parts.append(" ")
                current_length += 1
            parts.append(text)
            current_length += len(text)
            utterance_end_offsets.append(current_length)

        return _RenderedGroup(
            text="".join(parts),
            utterance_end_offsets=utterance_end_offsets,
        )

    def _split_text(
        self,
        *,
        text: str,
        language_code: str | None,
    ) -> tuple[list[str], dict[str, Any]]:
        """Split one text block with the configured backend."""

        normalized_text = text.strip()
        if not normalized_text:
            return [], {
                "splitter_backend": "fallback_rules",
                "fallback_reason": "empty_text",
            }

        if self.config.sentence_splitter_backend == "fallback_rules":
            return self._fallback_split_text(normalized_text), {
                "splitter_backend": "fallback_rules",
                "fallback_reason": "fallback_backend_forced",
            }

        splitter = self._get_splitter()
        if splitter is None:
            raise RuntimeError(
                "wtpsplit sentence splitter is required but unavailable. "
                "Install wtpsplit or explicitly select fallback_rules."
            )

        try:
            split_texts = self._call_splitter(
                splitter=splitter,
                text=normalized_text,
                language_code=language_code,
            )
        except Exception as error:
            LOGGER.warning("wtpsplit sentence splitting failed: %s", error)
            raise RuntimeError("wtpsplit sentence splitting failed") from error

        cleaned_sentences = self._clean_split_texts(split_texts, normalized_text)
        if not cleaned_sentences:
            raise RuntimeError("wtpsplit returned no sentence boundaries")

        return cleaned_sentences, {
            "splitter_backend": "wtpsplit_sat",
        }

    def _get_splitter(self) -> object | None:
        """Resolve and memoize the optional wtpsplit splitter instance."""

        if self._override_splitter is not None:
            return self._override_splitter
        if self._splitter_resolution_attempted:
            return self._resolved_splitter

        self._splitter_resolution_attempted = True
        try:
            self._install_splitter_warning_filter()
            from wtpsplit import SaT  # type: ignore
        except ImportError:
            self._resolved_splitter = None
            return None

        with self._suppress_splitter_runtime_warnings():
            self._resolved_splitter = SaT(self.config.sentence_splitter_model_name)
        return self._resolved_splitter

    @staticmethod
    def _call_splitter(
        *,
        splitter: object,
        text: str,
        language_code: str | None,
    ) -> list[str]:
        """Invoke the external splitter while tolerating small API differences."""

        candidate_calls = []
        if hasattr(splitter, "split"):
            split_method = getattr(splitter, "split")
            candidate_calls.extend(
                [
                    lambda: split_method(text, lang_code=language_code),
                    lambda: split_method(text, language_code=language_code),
                    lambda: split_method(text, language=language_code),
                    lambda: split_method(text),
                ],
            )
        if callable(splitter):
            candidate_calls.extend(
                [
                    lambda: splitter(text, lang_code=language_code),
                    lambda: splitter(text, language_code=language_code),
                    lambda: splitter(text, language=language_code),
                    lambda: splitter(text),
                ],
            )

        last_error: Exception | None = None
        for candidate in candidate_calls:
            try:
                with SentenceReconstructor._suppress_splitter_runtime_warnings():
                    raw_result = candidate()
            except TypeError as error:
                last_error = error
                continue
            if raw_result is None:
                continue
            if isinstance(raw_result, str):
                return [raw_result]
            if isinstance(raw_result, Sequence):
                flattened_result = list(raw_result)
                if (
                    flattened_result
                    and isinstance(flattened_result[0], Sequence)
                    and not isinstance(flattened_result[0], str)
                ):
                    return [str(item).strip() for item in flattened_result[0]]
                return [str(item).strip() for item in flattened_result]

        if last_error is not None:
            raise last_error
        raise RuntimeError("Unsupported wtpsplit splitter interface.")

    @staticmethod
    @contextmanager
    def _suppress_splitter_runtime_warnings() -> Any:
        """Suppress known noisy third-party warnings emitted by wtpsplit helpers."""

        with warnings.catch_warnings():
            SentenceReconstructor._install_splitter_warning_filter()
            yield

    @classmethod
    def _install_splitter_warning_filter(cls) -> None:
        """Ignore the known torchaudio backend warning emitted via skops/wtpsplit."""

        warnings.filterwarnings(
            "ignore",
            message=cls._WTPSPLIT_WARNING_PATTERN,
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=cls._WTPSPLIT_WARNING_PATTERN,
            category=UserWarning,
            module=r"skops\.io\._utils",
        )

    @classmethod
    def _clean_split_texts(
        cls,
        split_texts: Sequence[str],
        original_text: str,
    ) -> list[str]:
        """Normalize raw sentence fragments into a compact sentence list."""

        cleaned_sentences = [
            str(item).strip() for item in split_texts if str(item).strip()
        ]
        if not cleaned_sentences:
            return []
        if " ".join(cleaned_sentences).strip() == original_text.strip():
            return cleaned_sentences
        return cleaned_sentences

    def _fallback_split_text(self, text: str) -> list[str]:
        """Split text conservatively without external dependencies."""

        fragments = [
            fragment.strip()
            for fragment in self._FALLBACK_BOUNDARY_RE.split(text)
            if fragment.strip()
        ]
        return fragments or [text.strip()]

    @classmethod
    def _resolve_boundary_indexes(
        cls,
        *,
        text: str,
        split_texts: Sequence[str],
        utterance_end_offsets: Sequence[int],
    ) -> list[int]:
        """Map splitter output back to utterance boundaries."""

        if not utterance_end_offsets:
            return []

        boundary_indexes: list[int] = []
        cursor = 0
        previous_boundary_index = 0

        for split_text in split_texts[:-1]:
            normalized_fragment = split_text.strip()
            if not normalized_fragment:
                continue
            match_index = text.find(normalized_fragment, cursor)
            if match_index < 0:
                match_index = cursor
            fragment_end_offset = match_index + len(normalized_fragment)
            cursor = fragment_end_offset

            boundary_index = cls._closest_boundary_index(
                target_offset=fragment_end_offset,
                utterance_end_offsets=utterance_end_offsets,
                minimum_index=previous_boundary_index + 1,
            )
            if boundary_index is None or boundary_index <= previous_boundary_index:
                continue
            boundary_indexes.append(boundary_index)
            previous_boundary_index = boundary_index

        if not boundary_indexes or boundary_indexes[-1] != len(utterance_end_offsets):
            boundary_indexes.append(len(utterance_end_offsets))
        return boundary_indexes

    @staticmethod
    def _closest_boundary_index(
        *,
        target_offset: int,
        utterance_end_offsets: Sequence[int],
        minimum_index: int,
    ) -> int | None:
        """Return the closest valid utterance boundary index for one split point."""

        candidates = [
            (index, abs(offset - target_offset))
            for index, offset in enumerate(utterance_end_offsets, start=1)
            if index >= minimum_index
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[1], item[0]))
        return candidates[0][0]

    @staticmethod
    def _boundary_indexes_to_spans(
        boundary_indexes: Sequence[int],
        utterance_count: int,
    ) -> list[_SentenceSpan]:
        """Convert boundary indexes into utterance spans."""

        spans: list[_SentenceSpan] = []
        start_index = 0
        for boundary_index in boundary_indexes:
            if boundary_index <= start_index or boundary_index > utterance_count:
                continue
            spans.append(_SentenceSpan(start_index=start_index, end_index=boundary_index))
            start_index = boundary_index
        if not spans and utterance_count > 0:
            spans.append(_SentenceSpan(start_index=0, end_index=utterance_count))
        return spans

    def _refine_spans(
        self,
        spans: Sequence[_SentenceSpan],
        utterances: Sequence[Utterance],
    ) -> list[_SentenceSpan]:
        """Apply deterministic semantic post-processing to sentence spans."""

        refined_spans = list(spans)
        refined_spans = self._merge_low_quality_spans(refined_spans, utterances)
        refined_spans = self._split_run_on_spans(refined_spans, utterances)
        refined_spans = self._merge_low_quality_spans(refined_spans, utterances)
        return refined_spans

    def _merge_low_quality_spans(
        self,
        spans: Sequence[_SentenceSpan],
        utterances: Sequence[Utterance],
    ) -> list[_SentenceSpan]:
        """Merge fragment-like spans when the merge looks safer than the split."""

        if not spans:
            return []

        merged_spans = list(spans)
        changed = True
        while changed and len(merged_spans) > 1:
            changed = False
            next_spans: list[_SentenceSpan] = []
            index = 0

            while index < len(merged_spans):
                current_span = merged_spans[index]
                current_utterances = utterances[
                    current_span.start_index : current_span.end_index
                ]
                merge_next = (
                    index + 1 < len(merged_spans)
                    and self._should_merge_with_next(
                        current_span=current_span,
                        current_utterances=current_utterances,
                        next_span=merged_spans[index + 1],
                        utterances=utterances,
                    )
                )
                if merge_next:
                    next_span = merged_spans[index + 1]
                    next_spans.append(
                        _SentenceSpan(
                            start_index=current_span.start_index,
                            end_index=next_span.end_index,
                        ),
                    )
                    index += 2
                    changed = True
                    continue

                if (
                    index > 0
                    and self._should_merge_with_previous(
                        previous_span=next_spans[-1],
                        current_span=current_span,
                        utterances=utterances,
                    )
                ):
                    previous_span = next_spans.pop()
                    next_spans.append(
                        _SentenceSpan(
                            start_index=previous_span.start_index,
                            end_index=current_span.end_index,
                        ),
                    )
                    index += 1
                    changed = True
                    continue

                next_spans.append(current_span)
                index += 1

            merged_spans = next_spans
        return merged_spans

    def _should_merge_with_next(
        self,
        *,
        current_span: _SentenceSpan,
        current_utterances: Sequence[Utterance],
        next_span: _SentenceSpan,
        utterances: Sequence[Utterance],
    ) -> bool:
        """Return whether a low-quality span should merge forward."""

        del current_span
        current_text = self._join_utterance_texts(current_utterances)
        current_word_count = self._word_count(current_text)
        current_duration = self._utterance_slice_duration(current_utterances)
        is_fragment = self._is_fragment_candidate(
            text=current_text,
            word_count=current_word_count,
            duration_seconds=current_duration,
        )
        ends_incomplete = self._ends_with_incomplete_marker(current_text)

        if not is_fragment and not ends_incomplete:
            return False

        next_utterances = utterances[next_span.start_index : next_span.end_index]
        if not next_utterances:
            return False
        if self._has_strong_boundary_between(current_utterances, next_utterances):
            return False

        combined_utterances = list(current_utterances) + list(next_utterances)
        if not self._can_merge_utterance_slices(combined_utterances):
            return False
        return True

    def _should_merge_with_previous(
        self,
        *,
        previous_span: _SentenceSpan,
        current_span: _SentenceSpan,
        utterances: Sequence[Utterance],
    ) -> bool:
        """Return whether a low-quality span should merge backward."""

        previous_utterances = utterances[
            previous_span.start_index : previous_span.end_index
        ]
        current_utterances = utterances[
            current_span.start_index : current_span.end_index
        ]
        current_text = self._join_utterance_texts(current_utterances)
        current_word_count = self._word_count(current_text)
        current_duration = self._utterance_slice_duration(current_utterances)
        starts_incomplete = self._starts_with_incomplete_marker(current_text)
        is_fragment = self._is_fragment_candidate(
            text=current_text,
            word_count=current_word_count,
            duration_seconds=current_duration,
        )

        if not is_fragment and not starts_incomplete:
            return False
        if self._has_strong_boundary_between(previous_utterances, current_utterances):
            return False
        combined_utterances = list(previous_utterances) + list(current_utterances)
        return self._can_merge_utterance_slices(combined_utterances)

    def _split_run_on_spans(
        self,
        spans: Sequence[_SentenceSpan],
        utterances: Sequence[Utterance],
    ) -> list[_SentenceSpan]:
        """Split very long spans conservatively on natural utterance boundaries."""

        refined_spans: list[_SentenceSpan] = []
        for span in spans:
            refined_spans.extend(self._split_run_on_span(span, utterances))
        return refined_spans

    def _split_run_on_span(
        self,
        span: _SentenceSpan,
        utterances: Sequence[Utterance],
    ) -> list[_SentenceSpan]:
        """Split one long span when a conservative split point exists."""

        span_utterances = utterances[span.start_index : span.end_index]
        span_text = self._join_utterance_texts(span_utterances)
        word_count = self._word_count(span_text)
        duration_seconds = self._utterance_slice_duration(span_utterances)
        if not self._is_run_on_candidate(
            text=span_text,
            word_count=word_count,
            duration_seconds=duration_seconds,
        ):
            return [span]

        candidate_index = self._best_run_on_boundary(span, utterances)
        if candidate_index is None:
            return [span]

        left_span = _SentenceSpan(
            start_index=span.start_index,
            end_index=candidate_index,
        )
        right_span = _SentenceSpan(
            start_index=candidate_index,
            end_index=span.end_index,
        )
        return self._split_run_on_span(left_span, utterances) + self._split_run_on_span(
            right_span,
            utterances,
        )

    def _best_run_on_boundary(
        self,
        span: _SentenceSpan,
        utterances: Sequence[Utterance],
    ) -> int | None:
        """Return the best split boundary inside one long span when viable."""

        best_boundary: int | None = None
        best_score = 0.0

        for boundary_index in range(span.start_index + 1, span.end_index):
            left_utterances = utterances[span.start_index:boundary_index]
            right_utterances = utterances[boundary_index:span.end_index]
            if not left_utterances or not right_utterances:
                continue

            left_text = self._join_utterance_texts(left_utterances)
            right_text = self._join_utterance_texts(right_utterances)
            left_word_count = self._word_count(left_text)
            right_word_count = self._word_count(right_text)
            left_duration = self._utterance_slice_duration(left_utterances)
            right_duration = self._utterance_slice_duration(right_utterances)
            if self._is_fragment_candidate(
                text=left_text,
                word_count=left_word_count,
                duration_seconds=left_duration,
            ) or self._is_fragment_candidate(
                text=right_text,
                word_count=right_word_count,
                duration_seconds=right_duration,
            ):
                continue

            previous_utterance = left_utterances[-1]
            next_utterance = right_utterances[0]
            score = 0.0
            if previous_utterance.text.strip().endswith(self._STRONG_ENDING_PUNCTUATION):
                score += 3.0
            elif previous_utterance.text.strip().endswith(self._WEAK_ENDING_PUNCTUATION):
                score += 1.25

            gap_seconds = max(
                0.0,
                next_utterance.start_seconds - previous_utterance.end_seconds,
            )
            if gap_seconds >= 0.75:
                score += 2.0
            elif gap_seconds >= 0.35:
                score += 1.0

            if self._starts_with_transition_marker(next_utterance.text):
                score += 1.0
            if self._starts_with_incomplete_marker(next_utterance.text):
                score -= 1.5
            if self._ends_with_incomplete_marker(previous_utterance.text):
                score -= 2.0
            if self._has_strong_boundary_between([previous_utterance], [next_utterance]):
                score -= 0.5

            if score > best_score:
                best_score = score
                best_boundary = boundary_index

        if best_score < 1.0:
            return None
        return best_boundary

    def _materialize_sentences(
        self,
        *,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        spans: Sequence[_SentenceSpan],
        sentence_offset: int,
        source_utterance_offset: int,
    ) -> list[Sentence]:
        """Create sentence models from refined utterance spans."""

        sentences: list[Sentence] = []
        for span in spans:
            if span.end_index <= span.start_index:
                continue
            sentence_utterances = list(utterances[span.start_index : span.end_index])
            sentence = self._build_sentence(
                audio_source=audio_source,
                utterances=sentence_utterances,
                sentence_index=sentence_offset + len(sentences) + 1,
                start_index=source_utterance_offset + span.start_index + 1,
                end_index=source_utterance_offset + span.end_index,
            )
            if sentence is not None:
                sentences.append(sentence)
        return sentences

    @staticmethod
    def _resolve_final_source_utterance_ids(
        *,
        utterances: Sequence[Utterance],
        fallback_utterance_ids: Sequence[str],
    ) -> list[str]:
        """Return the final local provenance list for one sentence."""

        resolved_ids: list[str] = []
        seen_ids: set[str] = set()

        for utterance in utterances:
            normalized_utterance_id = str(utterance.utterance_id or "").strip()
            if not normalized_utterance_id or normalized_utterance_id in seen_ids:
                continue
            seen_ids.add(normalized_utterance_id)
            resolved_ids.append(normalized_utterance_id)

        if resolved_ids:
            return resolved_ids

        for utterance_id in fallback_utterance_ids:
            normalized_utterance_id = str(utterance_id or "").strip()
            if not normalized_utterance_id or normalized_utterance_id in seen_ids:
                continue
            seen_ids.add(normalized_utterance_id)
            resolved_ids.append(normalized_utterance_id)
        return resolved_ids

    def _build_sentence(
        self,
        *,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        sentence_index: int,
        start_index: int,
        end_index: int,
    ) -> Sentence | None:
        """Build one serializable sentence from a grounded utterance span."""

        if not utterances:
            return None

        text = self._join_utterance_texts(utterances)
        if not text:
            return None

        return Sentence(
            sentence_id=f"{audio_source.audio_source_id}_sentence_{sentence_index:04d}",
            audio_source_id=audio_source.audio_source_id,
            text=text,
            start_seconds=min(utterance.start_seconds for utterance in utterances),
            end_seconds=max(utterance.end_seconds for utterance in utterances),
            source_utterance_ids=[utterance.utterance_id for utterance in utterances],
            source_utterance_start_index=start_index,
            source_utterance_end_index=end_index,
            detected_language=self._resolve_group_language(utterances),
            speaker_id=self._resolve_initial_speaker(utterances),
            session_start_seconds=self._resolve_session_boundary(
                utterances,
                attribute_name="session_start_seconds",
                reducer=min,
            ),
            session_end_seconds=self._resolve_session_boundary(
                utterances,
                attribute_name="session_end_seconds",
                reducer=max,
            ),
            metadata={
                "source_utterance_count": len(utterances),
                "speaker_boundary_respected": True,
            },
        )

    @staticmethod
    def _resolve_group_language(utterances: Sequence[Utterance]) -> str | None:
        """Return the most recent non-empty language observed in the group."""

        for utterance in reversed(utterances):
            if utterance.detected_language:
                return utterance.detected_language
        return None

    @staticmethod
    def _resolve_initial_speaker(utterances: Sequence[Utterance]) -> str | None:
        """Return an initial speaker guess before sentence consolidation."""

        assigned_speakers = [
            utterance.speaker_id
            for utterance in utterances
            if utterance.speaker_id is not None and not utterance.speaker_is_uncertain
        ]
        if not assigned_speakers:
            return None
        if len(set(assigned_speakers)) == 1:
            return assigned_speakers[0]
        return None

    @staticmethod
    def _resolve_session_boundary(
        utterances: Sequence[Utterance],
        *,
        attribute_name: str,
        reducer,
    ) -> float | None:
        """Resolve one session-relative timing boundary from linked utterances."""

        values = [
            value
            for utterance in utterances
            if (value := getattr(utterance, attribute_name)) is not None
        ]
        if not values:
            return None
        return reducer(values)

    @staticmethod
    def _ordered_audio_sources(
        audio_sources: Sequence[AudioSource],
    ) -> list[AudioSource]:
        """Return audio sources in a deterministic processing order."""

        return sorted(
            audio_sources,
            key=lambda source: (
                source.order_index if source.order_index is not None else 10**9,
                source.audio_source_id,
            ),
        )

    @staticmethod
    def _ordered_utterances(utterances: Sequence[Utterance]) -> list[Utterance]:
        """Return utterances in a deterministic processing order."""

        return sorted(
            utterances,
            key=lambda utterance: (
                utterance.start_seconds,
                utterance.end_seconds,
                utterance.utterance_id,
            ),
        )

    def _apply_source_metadata(
        self,
        *,
        audio_source: AudioSource,
        sentence_collection: SentenceCollection | None,
        cache_record: CachedSentences | None,
        artifact_paths: SentencePaths | None,
        status: str,
        reason: str | None,
    ) -> None:
        """Mirror source-level sentence artifact details into source metadata."""

        metadata: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "used_cache": False,
            "used_existing_artifact": False,
            "artifact_reuse_enabled": self.config.intermediate_artifact_reuse_enabled,
            "forced_recompute": self.config.force_recompute,
        }
        if sentence_collection is not None:
            metadata["sentence_count"] = len(sentence_collection.sentences)
            metadata["source_utterance_count"] = sentence_collection.source_utterance_count
            metadata["splitter_backend"] = sentence_collection.metadata.get(
                "splitter_backend",
            )
            metadata["cache_hit"] = bool(sentence_collection.metadata.get("cache_hit"))
            metadata["high_priority_sentence_count"] = sum(
                1
                for sentence in sentence_collection.sentences
                if sentence.review_priority == "high"
            )
            metadata["shared_source_utterance_count"] = sentence_collection.metadata.get(
                "shared_source_utterance_count",
            )
            metadata["sentence_assignment_total"] = sentence_collection.metadata.get(
                "sentence_assignment_total",
            )
            metadata["all_sentences_have_provenance_overlap"] = (
                sentence_collection.metadata.get(
                    "all_sentences_have_provenance_overlap",
                )
            )
            metadata["provenance_anomaly_count"] = sentence_collection.metadata.get(
                "provenance_anomaly_count",
            )
            metadata["used_existing_artifact"] = bool(
                sentence_collection.metadata.get("artifact_reused"),
            )
            metadata["artifact_found"] = sentence_collection.metadata.get(
                "artifact_found",
            )
            metadata["artifact_reused"] = sentence_collection.metadata.get(
                "artifact_reused",
            )
            metadata["artifact_ignored_due_to_force_recompute"] = (
                sentence_collection.metadata.get(
                    "artifact_ignored_due_to_force_recompute",
                )
            )
            metadata["recomputed"] = sentence_collection.metadata.get("recomputed")
        if cache_record is not None:
            metadata["cache_format"] = cache_record.cache_format
        if artifact_paths is not None:
            metadata["artifact_manifest_path"] = str(artifact_paths.manifest_path)
        audio_source.metadata["sentences"] = {
            key: value for key, value in metadata.items() if value is not None
        }

    def _semantic_fragment_signals(
        self,
        text: str,
        word_count: int,
        duration_seconds: float,
    ) -> list[str]:
        """Return lightweight semantic-fragment signals for one sentence."""

        signals: list[str] = []
        if word_count <= self.config.sentence_fragment_max_word_count:
            signals.append("very_short_word_count")
        if len(text.strip()) <= self.config.sentence_fragment_max_text_length:
            signals.append("very_short_text")
        if duration_seconds <= self.config.sentence_fragment_max_duration_seconds:
            signals.append("very_short_duration")
        if self._starts_with_incomplete_marker(text):
            signals.append("starts_with_incomplete_marker")
        if self._ends_with_incomplete_marker(text):
            signals.append("ends_with_incomplete_marker")
        if not text.endswith(self._STRONG_ENDING_PUNCTUATION):
            signals.append("missing_strong_final_punctuation")
        return signals

    def _semantic_run_on_signals(
        self,
        text: str,
        word_count: int,
        duration_seconds: float,
    ) -> list[str]:
        """Return lightweight run-on signals for one sentence."""

        signals: list[str] = []
        if word_count >= self.config.sentence_run_on_max_word_count:
            signals.append("high_word_count")
        if duration_seconds >= self.config.sentence_run_on_max_duration_seconds:
            signals.append("high_duration")
        weak_boundary_count = sum(text.count(marker) for marker in (",", ";", ":"))
        if weak_boundary_count >= 2:
            signals.append("multiple_internal_clauses")
        return signals

    def _sentence_semantic_cleanup_features(
        self,
        *,
        text: str,
        word_count: int,
        fragment_signals: Sequence[str],
        run_on_signals: Sequence[str],
        semantic_quality_label: str,
    ) -> dict[str, Any]:
        """Return compact diagnostics for conservative sentence cleanup."""

        fragment_set = set(fragment_signals)
        run_on_set = set(run_on_signals)
        has_strong_final_punctuation = text.strip().endswith(
            self._STRONG_ENDING_PUNCTUATION,
        )
        sentence_autonomy_score = 0.78
        boundary_confidence_score = 0.76
        continuation_risk_score = 0.08

        if semantic_quality_label == "fragment":
            sentence_autonomy_score -= 0.34
            boundary_confidence_score -= 0.18
            continuation_risk_score += 0.24
        elif semantic_quality_label == "run_on":
            sentence_autonomy_score -= 0.22
            boundary_confidence_score -= 0.24
            continuation_risk_score += 0.20
        elif semantic_quality_label == "borderline":
            sentence_autonomy_score -= 0.12
            boundary_confidence_score -= 0.10
            continuation_risk_score += 0.10

        if "starts_with_incomplete_marker" in fragment_set:
            sentence_autonomy_score -= 0.14
            continuation_risk_score += 0.16
        if "ends_with_incomplete_marker" in fragment_set:
            boundary_confidence_score -= 0.18
            continuation_risk_score += 0.14
        if "missing_strong_final_punctuation" in fragment_set:
            boundary_confidence_score -= 0.16
            continuation_risk_score += 0.08
        if "multiple_internal_clauses" in run_on_set:
            boundary_confidence_score -= 0.10
            continuation_risk_score += 0.08
        if has_strong_final_punctuation:
            boundary_confidence_score += 0.08
        if 4 <= word_count <= 23:
            sentence_autonomy_score += 0.06

        sentence_autonomy_score = max(0.0, min(1.0, sentence_autonomy_score))
        boundary_confidence_score = max(0.0, min(1.0, boundary_confidence_score))
        continuation_risk_score = max(0.0, min(1.0, continuation_risk_score))
        return {
            "schema_version": "1.0",
            "sentence_autonomy_score": round(sentence_autonomy_score, 4),
            "boundary_confidence_score": round(boundary_confidence_score, 4),
            "continuation_risk_score": round(continuation_risk_score, 4),
            "has_strong_final_punctuation": has_strong_final_punctuation,
        }

    def _semantic_quality_label(
        self,
        *,
        fragment_signals: Sequence[str],
        run_on_signals: Sequence[str],
    ) -> str:
        """Return a compact semantic quality label for one sentence."""

        is_fragment = (
            ("starts_with_incomplete_marker" in fragment_signals)
            or ("ends_with_incomplete_marker" in fragment_signals)
            or (
                "very_short_word_count" in fragment_signals
                and "missing_strong_final_punctuation" in fragment_signals
            )
        )
        is_run_on = bool(run_on_signals)
        if is_fragment:
            return "fragment"
        if is_run_on:
            return "run_on"
        if fragment_signals:
            return "borderline"
        return "good"

    @staticmethod
    def _length_bucket(word_count: int) -> str:
        """Return a coarse length bucket for one sentence."""

        if word_count <= 3:
            return "short"
        if word_count >= 24:
            return "long"
        return "normal"

    @staticmethod
    def _duration_bucket(duration_seconds: float) -> str:
        """Return a coarse duration bucket for one sentence."""

        if duration_seconds <= 2.0:
            return "short"
        if duration_seconds >= 12.0:
            return "long"
        return "normal"

    @staticmethod
    def _resolve_speaker_confidence_label(speaker_resolution_status: str) -> str:
        """Return a human-facing confidence label for the sentence speaker."""

        if speaker_resolution_status == "stable":
            return "high"
        if speaker_resolution_status == "mostly_stable":
            return "medium"
        return "low"

    @staticmethod
    def _resolve_speaker_stability_label(speaker_resolution_status: str) -> str:
        """Return a compact speaker-stability label for human review."""

        if speaker_resolution_status in {
            "stable",
            "mostly_stable",
            "uncertain",
            "mixed",
            "unassigned",
        }:
            return speaker_resolution_status
        return "uncertain"

    @staticmethod
    def _merge_safety_label(
        *,
        semantic_quality_label: str,
        speaker_resolution_status: str,
        has_speaker_change_inside: bool,
        has_uncertain_source: bool,
        has_unassigned_source: bool,
        utterance_count: int,
    ) -> str:
        """Return a conservative merge-safety label for one sentence."""

        if utterance_count <= 1:
            return "safe"
        if (
            has_speaker_change_inside
            or has_unassigned_source
            or speaker_resolution_status == "mixed"
            or semantic_quality_label in {"fragment", "run_on"}
        ):
            return "risky"
        if has_uncertain_source or semantic_quality_label == "borderline":
            return "borderline"
        return "safe"

    @staticmethod
    def _sentence_review_flags(
        *,
        semantic_quality_label: str,
        merge_safety_label: str,
        has_uncertain_source: bool,
        has_unassigned_source: bool,
        has_speaker_change_inside: bool,
        is_multi_utterance: bool,
    ) -> list[str]:
        """Return deterministic sentence-level review flags."""

        flags: list[str] = []
        if has_uncertain_source:
            flags.append("uncertain_source")
        if has_unassigned_source:
            flags.append("unassigned_source")
        if has_speaker_change_inside:
            flags.append("speaker_change_inside")
        if is_multi_utterance:
            flags.append("multi_utterance")
        if semantic_quality_label == "fragment":
            flags.append("semantic_fragment")
        elif semantic_quality_label == "run_on":
            flags.append("semantic_run_on")
        elif semantic_quality_label == "borderline":
            flags.append("semantic_borderline")
        if merge_safety_label == "risky":
            flags.append("merge_risky")
        elif merge_safety_label == "borderline":
            flags.append("merge_borderline")
        return flags

    @staticmethod
    def _review_priority(
        *,
        speaker_resolution_status: str,
        semantic_quality_label: str,
        merge_safety_label: str,
    ) -> str:
        """Return a review-priority label for one consolidated sentence."""

        if (
            speaker_resolution_status in {"mixed", "unassigned"}
            or semantic_quality_label in {"fragment", "run_on"}
            or merge_safety_label == "risky"
        ):
            return "high"
        if (
            speaker_resolution_status in {"mostly_stable", "uncertain"}
            or semantic_quality_label == "borderline"
            or merge_safety_label == "borderline"
        ):
            return "medium"
        return "low"

    def _is_fragment_candidate(
        self,
        *,
        text: str,
        word_count: int,
        duration_seconds: float,
    ) -> bool:
        """Return whether one text span looks like a semantic fragment."""

        fragment_signals = self._semantic_fragment_signals(
            text,
            word_count,
            duration_seconds,
        )
        return self._semantic_quality_label(
            fragment_signals=fragment_signals,
            run_on_signals=[],
        ) == "fragment"

    def _is_run_on_candidate(
        self,
        *,
        text: str,
        word_count: int,
        duration_seconds: float,
    ) -> bool:
        """Return whether one text span looks like a semantic run-on."""

        run_on_signals = self._semantic_run_on_signals(
            text,
            word_count,
            duration_seconds,
        )
        return bool(run_on_signals)

    @staticmethod
    def _resolve_session_status(
        *,
        total_sources: int,
        built_sources: int,
        failed_sources: Sequence[str],
    ) -> str:
        """Return a session-level status for the current reconstruction run."""

        if total_sources <= 0:
            return "skipped"
        if built_sources <= 0:
            return "failed" if failed_sources else "skipped"
        if built_sources < total_sources or failed_sources:
            return "partial"
        return "completed"

    @staticmethod
    def _sentence_duration_seconds(sentence: Sentence) -> float:
        """Return the duration in seconds for one sentence."""

        start_seconds = (
            sentence.session_start_seconds
            if sentence.session_start_seconds is not None
            else sentence.start_seconds
        )
        end_seconds = (
            sentence.session_end_seconds
            if sentence.session_end_seconds is not None
            else sentence.end_seconds
        )
        return max(0.0, float(end_seconds) - float(start_seconds))

    @classmethod
    def _word_count(cls, text: str) -> int:
        """Return a lightweight word count for semantic heuristics."""

        return len(cls._WORD_RE.findall(text))

    @staticmethod
    def _join_utterance_texts(utterances: Sequence[Utterance]) -> str:
        """Return a normalized text string from a list of utterances."""

        return " ".join(
            utterance.text.strip()
            for utterance in utterances
            if utterance.text.strip()
        ).strip()

    @staticmethod
    def _utterance_slice_duration(utterances: Sequence[Utterance]) -> float:
        """Return the duration in seconds for one utterance slice."""

        if not utterances:
            return 0.0
        return max(0.0, utterances[-1].end_seconds - utterances[0].start_seconds)

    def _has_strong_boundary_between(
        self,
        left_utterances: Sequence[Utterance],
        right_utterances: Sequence[Utterance],
    ) -> bool:
        """Return whether a strong speaker boundary exists between two slices."""

        if not left_utterances or not right_utterances:
            return False
        return self._is_strong_speaker_boundary(
            left_utterances[-1],
            right_utterances[0],
        )

    @staticmethod
    def _has_speaker_change_inside(utterances: Sequence[Utterance]) -> bool:
        """Return whether a stable speaker change exists inside one sentence."""

        previous_assigned_speaker: str | None = None
        for utterance in utterances:
            speaker_id = (utterance.speaker_id or "").strip() or None
            if speaker_id is None or utterance.speaker_is_uncertain:
                continue
            if previous_assigned_speaker and speaker_id != previous_assigned_speaker:
                return True
            previous_assigned_speaker = speaker_id
        return False

    def _can_merge_utterance_slices(
        self,
        utterances: Sequence[Utterance],
    ) -> bool:
        """Return whether a merged span stays within conservative limits."""

        combined_text = self._join_utterance_texts(utterances)
        combined_word_count = self._word_count(combined_text)
        combined_duration = self._utterance_slice_duration(utterances)
        return (
            combined_word_count <= self.config.sentence_merge_max_word_count
            and combined_duration <= self.config.sentence_merge_max_duration_seconds
        )

    def _is_strong_speaker_boundary(
        self,
        previous_utterance: Utterance,
        current_utterance: Utterance,
    ) -> bool:
        """Return whether a speaker boundary should remain hard."""

        previous_speaker = (previous_utterance.speaker_id or "").strip()
        current_speaker = (current_utterance.speaker_id or "").strip()
        if not previous_speaker or not current_speaker:
            return False
        if previous_utterance.speaker_is_uncertain or current_utterance.speaker_is_uncertain:
            return False
        return previous_speaker != current_speaker

    def _starts_with_incomplete_marker(self, text: str) -> bool:
        """Return whether a text span starts with a strong continuation marker."""

        first_word = self._first_word(text)
        if first_word is None:
            return False
        return first_word in self.config.sentence_semantic_incomplete_markers

    def _ends_with_incomplete_marker(self, text: str) -> bool:
        """Return whether a text span ends with a strong continuation marker."""

        last_word = self._last_word(text)
        if last_word is None:
            return False
        return last_word in self.config.sentence_semantic_incomplete_markers

    def _starts_with_transition_marker(self, text: str) -> bool:
        """Return whether text starts with a configured transition marker."""

        first_word = self._first_word(text)
        if first_word is None:
            return False
        return first_word in self.config.segmentation_adaptive_transition_markers

    def _first_word(self, text: str) -> str | None:
        """Return the first lowercase token in a span when present."""

        words = self._WORD_RE.findall(text.lower())
        if not words:
            return None
        return words[0]

    def _last_word(self, text: str) -> str | None:
        """Return the last lowercase token in a span when present."""

        words = self._WORD_RE.findall(text.lower())
        if not words:
            return None
        return words[-1]


def _safe_float(value: Any, fallback: float | None = None) -> float | None:
    """Return a float-like value or the provided fallback."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: Any, fallback: int | None = 0) -> int | None:
    """Return an integer-like value or the provided fallback."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
