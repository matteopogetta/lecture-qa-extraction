"""Build traceable utterance units from aligned transcript artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
import re

from core.config import PipelineConfig
from core.models import (
    AlignedTranscript,
    AlignedTranscriptSegment,
    AlignedWord,
    AudioSource,
    LectureSession,
    Utterance,
    UtteranceCollection,
)
from transcription.cache_store import (
    CachedUtterances,
    TranscriptionCacheStore,
    UtterancePaths,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _UtteranceDraft:
    """Mutable utterance draft accumulated from one aligned segment."""

    word_ids: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    start_word_index: int | None = None
    end_word_index: int | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None

    def append(self, word: AlignedWord, word_index: int) -> None:
        """Add one aligned word to the current utterance draft."""

        text = word.text.strip()
        if not text:
            return

        self.word_ids.append(word.word_id)
        self.texts.append(text)
        if self.start_word_index is None:
            self.start_word_index = word_index
        self.end_word_index = word_index

        candidate_start = (
            word.start_seconds
            if word.start_seconds is not None
            else word.end_seconds
        )
        candidate_end = (
            word.end_seconds
            if word.end_seconds is not None
            else word.start_seconds
        )
        if self.start_seconds is None and candidate_start is not None:
            self.start_seconds = candidate_start
        if candidate_end is not None:
            self.end_seconds = candidate_end


class UtteranceBuilder:
    """Build a stable utterance layer from aligned transcript segments."""

    _PUNCTUATION_SPACING_RE = re.compile(r"\s+([,.;:?!])")
    _OPENING_BRACKET_SPACING_RE = re.compile(r"([\(\[\{])\s+")
    _CLOSING_BRACKET_SPACING_RE = re.compile(r"\s+([\)\]\}])")

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.cache_store = TranscriptionCacheStore(config)

    def build_session(self, session: LectureSession) -> list[Utterance]:
        """Build and persist utterances for the aligned sources in a session."""

        if not session.aligned_transcripts:
            session.utterances = []
            session.metadata["utterance_build_status"] = "skipped"
            session.metadata["utterance_build_reason"] = (
                "aligned_transcripts_unavailable"
            )
            session.metadata["utterance_source_count"] = 0
            session.metadata["utterance_artifact_count"] = 0
            session.metadata["utterance_count"] = 0
            for audio_source in session.audio_sources:
                self._apply_source_metadata(
                    audio_source=audio_source,
                    utterance_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="skipped",
                    reason="no_aligned_transcript",
                )
            return []

        aligned_by_source = {
            transcript.audio_source_id: transcript
            for transcript in session.aligned_transcripts
        }
        utterances: list[Utterance] = []
        built_sources = 0
        failed_sources: list[str] = []

        for audio_source in self._ordered_audio_sources(session.audio_sources):
            aligned_transcript = aligned_by_source.get(audio_source.audio_source_id)
            if aligned_transcript is None:
                self._apply_source_metadata(
                    audio_source=audio_source,
                    utterance_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="skipped",
                    reason="no_aligned_transcript",
                )
                continue

            try:
                utterance_collection, cache_record, artifact_paths = (
                    self._load_or_build_source(
                        audio_source=audio_source,
                        aligned_transcript=aligned_transcript,
                    )
                )
            except Exception as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.exception(
                    "Unexpected utterance build failure for %s.",
                    audio_source.audio_source_id,
                )
                self._apply_source_metadata(
                    audio_source=audio_source,
                    utterance_collection=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error) or "utterance_build_failed",
                )
                continue

            utterances.extend(utterance_collection.utterances)
            built_sources += 1
            self._apply_source_metadata(
                audio_source=audio_source,
                utterance_collection=utterance_collection,
                cache_record=cache_record,
                artifact_paths=artifact_paths,
                status="available",
                reason=None,
            )

        session.utterances = utterances
        session.metadata["utterance_builder_max_gap_seconds"] = (
            self.config.utterance_max_gap_seconds
        )
        session.metadata["utterance_source_count"] = built_sources
        session.metadata["utterance_artifact_count"] = built_sources
        session.metadata["utterance_count"] = len(utterances)
        session.metadata["utterance_failed_sources"] = failed_sources
        session.metadata["utterance_build_status"] = self._resolve_session_status(
            total_sources=len(session.audio_sources),
            built_sources=built_sources,
            failed_sources=failed_sources,
        )
        if failed_sources:
            session.metadata["utterance_build_reason"] = (
                "partial_or_failed_source_build"
            )
        else:
            session.metadata.pop("utterance_build_reason", None)
        return utterances

    def build_source(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
    ) -> UtteranceCollection:
        """Build utterances from one source-local aligned transcript."""

        utterances: list[Utterance] = []
        for segment_index, segment in enumerate(aligned_transcript.segments, start=1):
            utterances.extend(
                self._build_segment_utterances(
                    audio_source=audio_source,
                    aligned_transcript=aligned_transcript,
                    segment=segment,
                    segment_index=segment_index,
                ),
            )

        return UtteranceCollection(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=aligned_transcript.detected_language,
            source_segment_count=len(aligned_transcript.segments),
            source_word_count=sum(
                len(segment.words) for segment in aligned_transcript.segments
            ),
            utterances=utterances,
            metadata={
                "builder": "aligned_word_gap",
                "max_gap_seconds": self.config.utterance_max_gap_seconds,
                "segment_boundary_policy": "hard_boundary",
            },
        )

    def _load_or_build_source(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
    ) -> tuple[UtteranceCollection, CachedUtterances | None, UtterancePaths]:
        """Load cached utterances when possible, otherwise build and persist them."""

        artifact_found = self.cache_store.has_utterance_artifact(audio_source)
        cached_utterances = self.cache_store.load_utterances(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
        )
        if cached_utterances is not None:
            cached_utterances.utterance_collection.metadata["cache_hit"] = True
            cached_utterances.utterance_collection.metadata["used_cache"] = False
            cached_utterances.utterance_collection.metadata["used_existing_artifact"] = True
            cached_utterances.utterance_collection.metadata["artifact_reuse_enabled"] = True
            cached_utterances.utterance_collection.metadata["artifact_found"] = True
            cached_utterances.utterance_collection.metadata["artifact_reused"] = True
            cached_utterances.utterance_collection.metadata[
                "artifact_ignored_due_to_force_recompute"
            ] = False
            cached_utterances.utterance_collection.metadata["recomputed"] = False
            cached_utterances.utterance_collection.metadata["forced_recompute"] = False
            cached_utterances.utterance_collection.metadata["artifact_manifest_path"] = (
                str(cached_utterances.paths.manifest_path)
            )
            return (
                cached_utterances.utterance_collection,
                cached_utterances,
                cached_utterances.paths,
            )

        if self.config.force_recompute and artifact_found:
            LOGGER.info(
                "Ignoring utterance artifact for %s because run mode is from scratch.",
                audio_source.audio_source_id,
            )

        utterance_collection = self.build_source(audio_source, aligned_transcript)
        utterance_collection.metadata["cache_hit"] = False
        utterance_collection.metadata["used_cache"] = False
        utterance_collection.metadata["used_existing_artifact"] = False
        utterance_collection.metadata["artifact_reuse_enabled"] = (
            self.config.intermediate_artifact_reuse_enabled
        )
        utterance_collection.metadata["artifact_found"] = artifact_found
        utterance_collection.metadata["artifact_reused"] = False
        utterance_collection.metadata["artifact_ignored_due_to_force_recompute"] = (
            artifact_found and self.config.force_recompute
        )
        utterance_collection.metadata["recomputed"] = True
        utterance_collection.metadata["forced_recompute"] = self.config.force_recompute
        artifact_paths = self.cache_store.save_utterances(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
            utterance_collection=utterance_collection,
        )
        utterance_collection.metadata["artifact_manifest_path"] = str(
            artifact_paths.manifest_path,
        )
        return utterance_collection, None, artifact_paths

    def _build_segment_utterances(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        segment: AlignedTranscriptSegment,
        segment_index: int,
    ) -> list[Utterance]:
        """Build one or more utterances from a single aligned segment."""

        drafts = self._build_word_drafts(segment)
        utterances = [
            utterance
            for draft in drafts
            if (
                utterance := self._materialize_draft(
                    audio_source=audio_source,
                    aligned_transcript=aligned_transcript,
                    segment=segment,
                    segment_index=segment_index,
                    draft=draft,
                )
            )
            is not None
        ]
        if utterances:
            return utterances

        fallback_utterance = self._build_segment_fallback_utterance(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
            segment=segment,
            segment_index=segment_index,
        )
        return [fallback_utterance] if fallback_utterance is not None else []

    def _build_word_drafts(
        self,
        segment: AlignedTranscriptSegment,
    ) -> list[_UtteranceDraft]:
        """Split one aligned segment into utterance drafts using word gaps."""

        non_empty_words = [
            (word_index, word)
            for word_index, word in enumerate(segment.words, start=1)
            if word.text.strip()
        ]
        if not non_empty_words:
            return []

        drafts: list[_UtteranceDraft] = []
        current = _UtteranceDraft()
        for word_index, word in non_empty_words:
            if current.word_ids and self._should_split_before_word(current, word):
                drafts.append(current)
                current = _UtteranceDraft()
            current.append(word, word_index)

        if current.word_ids:
            drafts.append(current)
        return drafts

    def _should_split_before_word(
        self,
        draft: _UtteranceDraft,
        next_word: AlignedWord,
    ) -> bool:
        """Return whether a new utterance should start before the next word."""

        if draft.end_seconds is None or next_word.start_seconds is None:
            return False
        gap_seconds = next_word.start_seconds - draft.end_seconds
        return gap_seconds > self.config.utterance_max_gap_seconds

    def _materialize_draft(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        segment: AlignedTranscriptSegment,
        segment_index: int,
        draft: _UtteranceDraft,
    ) -> Utterance | None:
        """Convert one word draft into a persisted utterance model."""

        text = self._join_word_texts(draft.texts)
        if not text:
            return None

        start_seconds = (
            draft.start_seconds
            if draft.start_seconds is not None
            else segment.start_seconds
        )
        end_seconds = (
            draft.end_seconds if draft.end_seconds is not None else segment.end_seconds
        )
        if start_seconds is None or end_seconds is None:
            return None
        if end_seconds < start_seconds:
            end_seconds = start_seconds

        return self._build_utterance(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
            segment=segment,
            segment_index=segment_index,
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            start_word_index=draft.start_word_index,
            end_word_index=draft.end_word_index,
            source_word_ids=draft.word_ids,
            metadata={
                "timing_source": "aligned_words",
                "text_source": "aligned_words",
                "build_strategy": "segment_boundary_plus_gap",
            },
        )

    def _build_segment_fallback_utterance(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        segment: AlignedTranscriptSegment,
        segment_index: int,
    ) -> Utterance | None:
        """Build a coarse utterance directly from segment timing and text."""

        text = segment.text.strip()
        if not text:
            return None
        if segment.start_seconds is None or segment.end_seconds is None:
            return None

        return self._build_utterance(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
            segment=segment,
            segment_index=segment_index,
            text=text,
            start_seconds=segment.start_seconds,
            end_seconds=max(segment.start_seconds, segment.end_seconds),
            start_word_index=None,
            end_word_index=None,
            source_word_ids=[],
            metadata={
                "timing_source": "aligned_segment",
                "text_source": "aligned_segment",
                "build_strategy": "segment_fallback",
            },
        )

    def _build_utterance(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        segment: AlignedTranscriptSegment,
        segment_index: int,
        text: str,
        start_seconds: float,
        end_seconds: float,
        start_word_index: int | None,
        end_word_index: int | None,
        source_word_ids: list[str],
        metadata: dict[str, object],
    ) -> Utterance:
        """Return a traceable utterance model from one segment-local span."""

        utterance_id = self._build_utterance_id(
            audio_source_id=audio_source.audio_source_id,
            segment_index=segment_index,
            start_word_index=start_word_index,
            end_word_index=end_word_index,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            text=text,
        )
        return Utterance(
            utterance_id=utterance_id,
            audio_source_id=audio_source.audio_source_id,
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            aligned_segment_id=segment.segment_id,
            aligned_segment_index=segment_index,
            transcript_chunk_id=segment.transcript_chunk_id,
            start_word_index=start_word_index,
            end_word_index=end_word_index,
            source_word_ids=source_word_ids,
            detected_language=(
                segment.detected_language or aligned_transcript.detected_language
            ),
            speaker_id=None,
            session_start_seconds=self._build_session_time(
                audio_source.session_offset_seconds,
                start_seconds,
            ),
            session_end_seconds=self._build_session_time(
                audio_source.session_offset_seconds,
                end_seconds,
            ),
            metadata=dict(metadata),
        )

    @classmethod
    def _join_word_texts(cls, texts: list[str]) -> str:
        """Return a compact utterance string from token-like word texts."""

        raw_text = " ".join(text.strip() for text in texts if text.strip())
        normalized = cls._PUNCTUATION_SPACING_RE.sub(r"\1", raw_text)
        normalized = cls._OPENING_BRACKET_SPACING_RE.sub(r"\1", normalized)
        normalized = cls._CLOSING_BRACKET_SPACING_RE.sub(r"\1", normalized)
        return normalized.strip()

    @staticmethod
    def _build_utterance_id(
        audio_source_id: str,
        segment_index: int,
        start_word_index: int | None,
        end_word_index: int | None,
        start_seconds: float,
        end_seconds: float,
        text: str,
    ) -> str:
        """Return a deterministic utterance identifier."""

        payload = {
            "segment_index": segment_index,
            "start_word_index": start_word_index,
            "end_word_index": end_word_index,
            "start_seconds": round(start_seconds, 3),
            "end_seconds": round(end_seconds, 3),
            "text": text,
        }
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()[:16]
        return f"{audio_source_id}_utterance_{digest}"

    @staticmethod
    def _build_session_time(
        session_offset_seconds: float | None,
        source_time_seconds: float,
    ) -> float | None:
        """Translate source-local timing into session timing when possible."""

        if session_offset_seconds is None:
            return None
        return float(session_offset_seconds) + source_time_seconds

    @staticmethod
    def _ordered_audio_sources(audio_sources: list[AudioSource]) -> list[AudioSource]:
        """Return audio sources in deterministic session order."""

        return sorted(
            audio_sources,
            key=lambda source: (
                source.order_index if source.order_index is not None else 10**9,
                source.audio_source_id,
            ),
        )

    @staticmethod
    def _resolve_session_status(
        total_sources: int,
        built_sources: int,
        failed_sources: list[str],
    ) -> str:
        """Return a compact session-level utterance build status."""

        if total_sources == 0 or built_sources == 0:
            return "skipped" if not failed_sources else "failed"
        if built_sources == total_sources and not failed_sources:
            return "available"
        return "partial"

    def _apply_source_metadata(
        self,
        audio_source: AudioSource,
        utterance_collection: UtteranceCollection | None,
        cache_record: CachedUtterances | None,
        artifact_paths: UtterancePaths | None,
        status: str,
        reason: str | None,
    ) -> None:
        """Store utterance-layer metadata on the source for traceability."""

        metadata = {
            "status": status,
            "reason": reason,
            "cache_hit": cache_record is not None,
            "used_cache": False,
            "used_existing_artifact": bool(
                utterance_collection is not None
                and utterance_collection.metadata.get("artifact_reused")
            ),
            "artifact_reuse_enabled": self.config.intermediate_artifact_reuse_enabled,
            "forced_recompute": self.config.force_recompute,
            "cache_format": cache_record.cache_format if cache_record else None,
            "artifact_manifest_path": (
                str(artifact_paths.manifest_path) if artifact_paths is not None else None
            ),
            "utterance_count": (
                len(utterance_collection.utterances)
                if utterance_collection is not None
                else 0
            ),
            "source_segment_count": (
                utterance_collection.source_segment_count
                if utterance_collection is not None
                else 0
            ),
            "artifact_found": (
                utterance_collection.metadata.get("artifact_found")
                if utterance_collection is not None
                else False
            ),
            "artifact_reused": (
                utterance_collection.metadata.get("artifact_reused")
                if utterance_collection is not None
                else False
            ),
            "artifact_ignored_due_to_force_recompute": (
                utterance_collection.metadata.get(
                    "artifact_ignored_due_to_force_recompute",
                )
                if utterance_collection is not None
                else False
            ),
            "recomputed": (
                utterance_collection.metadata.get("recomputed")
                if utterance_collection is not None
                else False
            ),
        }
        audio_source.metadata["utterances"] = metadata
