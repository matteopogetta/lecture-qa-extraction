"""WhisperX-based forced alignment layered on top of existing ASR chunks."""

from __future__ import annotations

from collections import Counter
import logging
from pathlib import Path
from typing import Any, Sequence

from core.config import PipelineConfig
from core.errors import IngestionError
from core.models import (
    AlignedTranscript,
    AlignedTranscriptSegment,
    AlignedWord,
    AudioSource,
    LectureSession,
    TranscriptChunk,
)
from transcription.cache_store import (
    AlignmentPaths,
    CachedAlignment,
    TranscriptionCacheStore,
)


LOGGER = logging.getLogger(__name__)


class AlignmentError(IngestionError):
    """Base exception for alignment-layer failures."""


class AlignmentUnavailableError(AlignmentError):
    """Raised when WhisperX or one of its runtime dependencies is unavailable."""


class MissingAlignmentLanguageError(AlignmentError):
    """Raised when no stable language can be resolved for alignment."""


class WhisperXAligner:
    """Refine ASR chunks with WhisperX word-level alignment."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.cache_store = TranscriptionCacheStore(config)
        self._align_model_cache: dict[tuple[str, str, str | None], tuple[Any, Any]] = {}

    def align_session(self, session: LectureSession) -> list[AlignedTranscript]:
        """Align transcript chunks per audio source without mutating raw ASR data."""

        if not self.config.transcript_alignment_enabled:
            session.aligned_transcripts = []
            session.metadata["transcript_alignment_enabled"] = False
            session.metadata["transcript_alignment_status"] = "disabled"
            for audio_source in session.audio_sources:
                self._apply_alignment_metadata(
                    audio_source=audio_source,
                    aligned_transcript=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="disabled",
                    reason="alignment_disabled",
                )
            return []

        chunks_by_source = self._group_chunks_by_audio_source(session.transcript_chunks)
        aligned_transcripts: list[AlignedTranscript] = []
        failed_sources: list[str] = []

        for audio_source in self._ordered_audio_sources(session.audio_sources):
            source_chunks = chunks_by_source.get(audio_source.audio_source_id, [])
            if not source_chunks:
                self._apply_alignment_metadata(
                    audio_source=audio_source,
                    aligned_transcript=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="skipped",
                    reason="no_transcript_chunks",
                )
                continue

            try:
                aligned_transcript = self.align_source(audio_source, source_chunks)
            except AlignmentError as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.warning(
                    "Alignment skipped for %s: %s",
                    audio_source.audio_source_id,
                    error,
                )
                self._apply_alignment_metadata(
                    audio_source=audio_source,
                    aligned_transcript=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error),
                )
                continue
            except Exception as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.exception(
                    "Unexpected WhisperX alignment failure for %s.",
                    audio_source.audio_source_id,
                )
                self._apply_alignment_metadata(
                    audio_source=audio_source,
                    aligned_transcript=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error) or "unexpected_alignment_error",
                )
                continue

            aligned_transcripts.append(aligned_transcript)

        session.aligned_transcripts = aligned_transcripts
        session.metadata["transcript_alignment_enabled"] = True
        session.metadata["transcript_alignment_status"] = self._resolve_session_status(
            total_sources=len(session.audio_sources),
            aligned_sources=len(aligned_transcripts),
            failed_sources=failed_sources,
        )
        session.metadata["transcript_alignment_source_count"] = len(session.audio_sources)
        session.metadata["transcript_alignment_aligned_source_count"] = len(
            aligned_transcripts,
        )
        session.metadata["transcript_alignment_failed_sources"] = failed_sources
        session.metadata["transcript_alignment_word_count"] = sum(
            len(segment.words)
            for aligned_transcript in aligned_transcripts
            for segment in aligned_transcript.segments
        )
        return aligned_transcripts

    def align_source(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> AlignedTranscript:
        """Align one source-local transcript and persist the resulting artifact."""

        audio_path = audio_source.audio_path.expanduser().resolve()
        if not audio_path.exists():
            raise AlignmentError(
                f"Normalized audio source not found for alignment: '{audio_path}'.",
            )

        artifact_found = self.cache_store.has_alignment_artifact(audio_source)
        cached_alignment = self.cache_store.load_alignment(
            audio_source=audio_source,
            transcript_chunks=transcript_chunks,
        )
        if cached_alignment is not None:
            cached_alignment.aligned_transcript.metadata["cache_hit"] = True
            cached_alignment.aligned_transcript.metadata["used_cache"] = False
            cached_alignment.aligned_transcript.metadata["used_existing_artifact"] = True
            cached_alignment.aligned_transcript.metadata["artifact_reuse_enabled"] = True
            cached_alignment.aligned_transcript.metadata["artifact_found"] = True
            cached_alignment.aligned_transcript.metadata["artifact_reused"] = True
            cached_alignment.aligned_transcript.metadata[
                "artifact_ignored_due_to_force_recompute"
            ] = False
            cached_alignment.aligned_transcript.metadata["recomputed"] = False
            cached_alignment.aligned_transcript.metadata["forced_recompute"] = False
            cached_alignment.aligned_transcript.metadata["artifact_manifest_path"] = str(
                cached_alignment.paths.manifest_path,
            )
            self._apply_alignment_metadata(
                audio_source=audio_source,
                aligned_transcript=cached_alignment.aligned_transcript,
                cache_record=cached_alignment,
                artifact_paths=cached_alignment.paths,
                status="available",
                reason=None,
            )
            return cached_alignment.aligned_transcript

        if self.config.force_recompute and artifact_found:
            LOGGER.info(
                "Ignoring alignment artifact for %s because run mode is from scratch.",
                audio_source.audio_source_id,
            )

        language = self._resolve_alignment_language(audio_source, transcript_chunks)
        aligned_payload = self._align_with_whisperx(
            audio_path=audio_path,
            transcript_chunks=transcript_chunks,
            language=language,
        )
        aligned_transcript = self._build_aligned_transcript(
            audio_source=audio_source,
            transcript_chunks=transcript_chunks,
            aligned_payload=aligned_payload,
            language=language,
            cache_hit=False,
        )
        aligned_transcript.metadata["used_cache"] = False
        aligned_transcript.metadata["used_existing_artifact"] = False
        aligned_transcript.metadata["artifact_reuse_enabled"] = (
            self.config.intermediate_artifact_reuse_enabled
        )
        aligned_transcript.metadata["artifact_found"] = artifact_found
        aligned_transcript.metadata["artifact_reused"] = False
        aligned_transcript.metadata["artifact_ignored_due_to_force_recompute"] = (
            artifact_found and self.config.force_recompute
        )
        aligned_transcript.metadata["recomputed"] = True
        aligned_transcript.metadata["forced_recompute"] = self.config.force_recompute
        artifact_paths = self.cache_store.save_alignment(
            audio_source=audio_source,
            transcript_chunks=transcript_chunks,
            aligned_transcript=aligned_transcript,
        )
        aligned_transcript.metadata["artifact_manifest_path"] = str(
            artifact_paths.manifest_path,
        )
        self._apply_alignment_metadata(
            audio_source=audio_source,
            aligned_transcript=aligned_transcript,
            cache_record=None,
            artifact_paths=artifact_paths,
            status="available",
            reason=None,
        )
        return aligned_transcript

    def _align_with_whisperx(
        self,
        audio_path: Path,
        transcript_chunks: Sequence[TranscriptChunk],
        language: str,
    ) -> dict[str, Any]:
        """Call WhisperX alignment primitives with the current transcript."""

        whisperx = self._import_whisperx()
        audio = whisperx.load_audio(str(audio_path))
        model, metadata = self._get_align_model(whisperx, language)
        result = whisperx.align(
            self._build_whisperx_segments(transcript_chunks),
            model,
            metadata,
            audio,
            self.config.transcript_alignment_device,
            return_char_alignments=False,
        )
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"segments": result}
        raise AlignmentError("WhisperX returned an unsupported alignment payload.")

    def _get_align_model(
        self,
        whisperx: Any,
        language: str,
    ) -> tuple[Any, Any]:
        """Load and cache the WhisperX alignment model for one language."""

        cache_key = (
            language,
            self.config.transcript_alignment_device,
            self.config.transcript_alignment_model_name,
        )
        if cache_key in self._align_model_cache:
            return self._align_model_cache[cache_key]

        load_kwargs: dict[str, Any] = {
            "language_code": language,
            "device": self.config.transcript_alignment_device,
        }
        if self.config.transcript_alignment_model_name is not None:
            load_kwargs["model_name"] = self.config.transcript_alignment_model_name

        try:
            model_and_metadata = whisperx.load_align_model(**load_kwargs)
        except TypeError:
            load_kwargs.pop("model_name", None)
            model_and_metadata = whisperx.load_align_model(**load_kwargs)

        self._align_model_cache[cache_key] = model_and_metadata
        return model_and_metadata

    @staticmethod
    def _build_whisperx_segments(
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> list[dict[str, Any]]:
        """Convert transcript chunks into the segment shape WhisperX expects."""

        return [
            {
                "id": index,
                "start": float(chunk.start_seconds),
                "end": float(chunk.end_seconds),
                "text": chunk.text,
            }
            for index, chunk in enumerate(transcript_chunks, start=1)
            if chunk.text.strip()
        ]

    def _build_aligned_transcript(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
        aligned_payload: dict[str, Any],
        language: str,
        cache_hit: bool,
    ) -> AlignedTranscript:
        """Normalize WhisperX output into the project's alignment models."""

        raw_segments = aligned_payload.get("segments")
        if not isinstance(raw_segments, list):
            raw_segments = []

        segments: list[AlignedTranscriptSegment] = []
        for segment_index, raw_segment in enumerate(raw_segments, start=1):
            if not isinstance(raw_segment, dict):
                continue

            linked_chunk = (
                transcript_chunks[segment_index - 1]
                if segment_index - 1 < len(transcript_chunks)
                else None
            )
            words = self._build_aligned_words(
                audio_source=audio_source,
                segment_index=segment_index,
                raw_words=raw_segment.get("words"),
            )
            segment_text = str(
                raw_segment.get("text", linked_chunk.text if linked_chunk else ""),
            ).strip()

            segments.append(
                AlignedTranscriptSegment(
                    segment_id=(
                        f"{audio_source.audio_source_id}"
                        f"_aligned_segment_{segment_index:04d}"
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    transcript_chunk_id=(
                        linked_chunk.chunk_id if linked_chunk is not None else None
                    ),
                    start_seconds=self._coalesce_float(
                        raw_segment.get("start"),
                        raw_segment.get("start_seconds"),
                    ),
                    end_seconds=self._coalesce_float(
                        raw_segment.get("end"),
                        raw_segment.get("end_seconds"),
                    ),
                    text=segment_text,
                    detected_language=linked_chunk.detected_language if linked_chunk else language,
                    words=words,
                    metadata=self._extract_metadata(
                        raw_segment,
                        excluded_keys={
                            "id",
                            "start",
                            "end",
                            "start_seconds",
                            "end_seconds",
                            "text",
                            "words",
                        },
                    ),
                ),
            )

        return AlignedTranscript(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=language,
            source_chunk_count=len(transcript_chunks),
            segments=segments,
            metadata={
                "backend": "whisperx",
                "model_name": self.config.transcript_alignment_model_name,
                "device": self.config.transcript_alignment_device,
                "cache_hit": cache_hit,
            },
        )

    def _build_aligned_words(
        self,
        audio_source: AudioSource,
        segment_index: int,
        raw_words: Any,
    ) -> list[AlignedWord]:
        """Convert WhisperX word output into stable project word objects."""

        if not isinstance(raw_words, list):
            return []

        words: list[AlignedWord] = []
        for word_index, raw_word in enumerate(raw_words, start=1):
            if not isinstance(raw_word, dict):
                continue

            text = str(
                raw_word.get("word")
                or raw_word.get("text")
                or raw_word.get("token")
                or "",
            ).strip()
            if not text:
                continue

            start_seconds = self._coalesce_float(
                raw_word.get("start"),
                raw_word.get("start_seconds"),
            )
            end_seconds = self._coalesce_float(
                raw_word.get("end"),
                raw_word.get("end_seconds"),
            )
            words.append(
                AlignedWord(
                    word_id=(
                        f"{audio_source.audio_source_id}"
                        f"_aligned_word_{segment_index:04d}_{word_index:04d}"
                    ),
                    text=text,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    confidence=self._coalesce_float(
                        raw_word.get("score"),
                        raw_word.get("confidence"),
                    ),
                    session_start_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        start_seconds,
                    ),
                    session_end_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        end_seconds,
                    ),
                    metadata=self._extract_metadata(
                        raw_word,
                        excluded_keys={
                            "word",
                            "text",
                            "token",
                            "start",
                            "end",
                            "start_seconds",
                            "end_seconds",
                            "score",
                            "confidence",
                        },
                    ),
                ),
            )
        return words

    def _resolve_alignment_language(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> str:
        """Choose one language code for WhisperX alignment."""

        detected_languages = [
            chunk.detected_language.strip().lower()
            for chunk in transcript_chunks
            if chunk.detected_language is not None and chunk.detected_language.strip()
        ]
        if detected_languages:
            return Counter(detected_languages).most_common(1)[0][0]

        transcription_metadata = audio_source.metadata.get("transcription")
        if isinstance(transcription_metadata, dict):
            detected_language = transcription_metadata.get("detected_language")
            if isinstance(detected_language, str) and detected_language.strip():
                return detected_language.strip().lower()

        if self.config.transcription_language is not None:
            return self.config.transcription_language

        raise MissingAlignmentLanguageError(
            "No language was available for WhisperX alignment.",
        )

    def _apply_alignment_metadata(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript | None,
        cache_record: CachedAlignment | None,
        artifact_paths: AlignmentPaths | None,
        status: str,
        reason: str | None,
    ) -> None:
        """Attach alignment state to the source metadata for exported traceability."""

        metadata: dict[str, Any] = {
            "enabled": self.config.transcript_alignment_enabled,
            "attempted": status not in {"disabled", "skipped"},
            "status": status,
            "cache_hit": cache_record is not None,
            "used_cache": False,
            "used_existing_artifact": cache_record is not None,
            "artifact_reuse_enabled": self.config.intermediate_artifact_reuse_enabled,
            "forced_recompute": self.config.force_recompute,
        }
        if reason is not None:
            metadata["reason"] = reason
        if aligned_transcript is not None:
            metadata["detected_language"] = aligned_transcript.detected_language
            metadata["aligned_segment_count"] = len(aligned_transcript.segments)
            metadata["aligned_word_count"] = sum(
                len(segment.words) for segment in aligned_transcript.segments
            )
            metadata["artifact_found"] = aligned_transcript.metadata.get("artifact_found")
            metadata["artifact_reused"] = aligned_transcript.metadata.get(
                "artifact_reused",
            )
            metadata["artifact_ignored_due_to_force_recompute"] = (
                aligned_transcript.metadata.get(
                    "artifact_ignored_due_to_force_recompute",
                )
            )
            metadata["recomputed"] = aligned_transcript.metadata.get("recomputed")
        if artifact_paths is not None:
            metadata["artifact_manifest_path"] = str(artifact_paths.manifest_path)
        audio_source.metadata["alignment"] = metadata

    @staticmethod
    def _group_chunks_by_audio_source(
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> dict[str, list[TranscriptChunk]]:
        """Return transcript chunks grouped by source while preserving order."""

        chunks_by_source: dict[str, list[TranscriptChunk]] = {}
        for chunk in transcript_chunks:
            chunks_by_source.setdefault(chunk.audio_source_id, []).append(chunk)
        return chunks_by_source

    @staticmethod
    def _ordered_audio_sources(
        audio_sources: Sequence[AudioSource],
    ) -> list[AudioSource]:
        """Return audio sources in deterministic processing order."""

        return sorted(
            audio_sources,
            key=lambda source: (
                source.order_index is None,
                source.order_index or 0,
                source.audio_source_id,
            ),
        )

    @staticmethod
    def _resolve_session_status(
        total_sources: int,
        aligned_sources: int,
        failed_sources: Sequence[str],
    ) -> str:
        """Return a compact session-level alignment status label."""

        if total_sources == 0:
            return "empty"
        if aligned_sources == 0 and failed_sources:
            return "failed"
        if aligned_sources == total_sources:
            return "ready"
        if aligned_sources > 0:
            return "partial"
        return "skipped"

    @staticmethod
    def _extract_metadata(
        payload: dict[str, Any],
        excluded_keys: set[str],
    ) -> dict[str, Any]:
        """Return the non-core keys of a WhisperX object as metadata."""

        return {
            key: value
            for key, value in payload.items()
            if key not in excluded_keys and value is not None
        }

    @staticmethod
    def _build_session_time(
        session_offset_seconds: float | None,
        source_time_seconds: float | None,
    ) -> float | None:
        """Translate source-local timing into session timing when possible."""

        if session_offset_seconds is None or source_time_seconds is None:
            return None
        return float(session_offset_seconds) + float(source_time_seconds)

    @staticmethod
    def _coalesce_float(*values: Any) -> float | None:
        """Return the first value that can be interpreted as a float."""

        for value in values:
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _import_whisperx() -> Any:
        """Import WhisperX lazily so the rest of the pipeline stays optional."""

        try:
            import whisperx
        except ImportError as error:
            raise AlignmentUnavailableError(
                "WhisperX is not installed. Install the `whisperx` package to "
                "enable alignment refinement.",
            ) from error
        return whisperx
