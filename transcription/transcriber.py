"""Transcription orchestration for normalized lecture audio.

The transcriber converts one audio source at a time into `TranscriptChunk`
objects while keeping timestamps in seconds and preserving traceability to the
normalized audio file. Session-level transcription simply iterates through all
audio sources in order and aggregates their chunks without performing any
advanced downstream analysis.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from core.config import PipelineConfig
from core.errors import IngestionError
from core.models import AudioSource, LectureSession, TranscriptChunk
from transcription.backend import BackendSegment, build_transcription_backend
from transcription.cache_store import (
    CachePaths,
    CachedTranscription,
    TranscriptionCacheStore,
)


LOGGER = logging.getLogger(__name__)


class TranscriptionError(IngestionError):
    """Base exception for transcription failures."""


class BackendInitializationError(TranscriptionError):
    """Raised when the configured transcription backend cannot be created."""


class MissingAudioSourceError(TranscriptionError):
    """Raised when a normalized audio source cannot be found on disk."""


class Transcriber:
    """Transcribe normalized lecture audio into timestamped transcript chunks.

    The component is intentionally lightweight:
    one method transcribes a single `AudioSource`, another processes all audio
    sources in a session. Backend-specific logic is delegated to a small helper
    module so the rest of the pipeline remains backend-agnostic.

    Multilingual handling is currently pragmatic rather than perfect. By
    default the configured backend runs in automatic language detection mode,
    which is acceptable for Italian, English, and many mixed lectures, but it
    does not guarantee ideal language boundaries inside every segment.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.cache_store = TranscriptionCacheStore(config)
        try:
            self.backend = build_transcription_backend(config)
        except Exception as error:
            raise BackendInitializationError(
                "Failed to initialize the transcription backend. "
                "Check the configured backend name, model, and installed "
                "dependencies.",
            ) from error

    def transcribe_session(self, session: LectureSession) -> list[TranscriptChunk]:
        """Transcribe every audio source in a lecture session.

        Multi-file sessions are handled by processing normalized audio sources
        in deterministic order and collecting all produced chunks into the
        session object. Source identifiers remain attached to each chunk so a
        later session-level merge step can still trace every transcript span
        back to its original audio file.
        """

        # Session-level metadata records the operational context of the run so
        # exported artifacts remain self-describing.
        chunks = self.transcribe_sources(session.audio_sources)
        session.transcript_chunks = chunks
        session.transcript_text = "\n".join(
            chunk.text for chunk in chunks if chunk.text.strip()
        )
        session.metadata["transcription_backend"] = self.config.transcription_backend
        session.metadata["transcription_model_name"] = (
            self.config.transcription_model_name
        )
        session.metadata["transcription_compute_type"] = (
            self.config.transcription_compute_type
        )
        session.metadata["transcription_language_mode"] = (
            self.config.transcription_language_mode
        )
        session.metadata["transcription_cache_enabled"] = (
            self.config.transcription_cache_enabled
        )
        session.metadata["transcription_cache_lookup_performed"] = any(
            bool(source.metadata.get("transcription", {}).get("cache_lookup_performed"))
            for source in session.audio_sources
        )
        session.metadata["transcription_cache_hit"] = any(
            bool(source.metadata.get("transcription", {}).get("cache_hit"))
            for source in session.audio_sources
        )
        session.metadata["transcription_cache_hit_count"] = sum(
            1
            for source in session.audio_sources
            if source.metadata.get("transcription", {}).get("cache_hit")
        )
        session.metadata["transcription_recomputed"] = any(
            bool(source.metadata.get("transcription", {}).get("transcription_recomputed"))
            for source in session.audio_sources
        )
        session.metadata["transcription_forced_recompute"] = (
            self.config.force_recompute
        )
        return chunks

    def transcribe_sources(
        self,
        audio_sources: Sequence[AudioSource],
    ) -> list[TranscriptChunk]:
        """Transcribe all normalized audio sources in input order."""

        ordered_sources = sorted(
            audio_sources,
            key=lambda source: (
                source.order_index is None,
                source.order_index or 0,
                source.audio_source_id,
            ),
        )

        chunks: list[TranscriptChunk] = []
        for audio_source in ordered_sources:
            chunks.extend(self.transcribe_source(audio_source))
        return chunks

    def transcribe_source(
        self,
        audio_source: AudioSource,
    ) -> list[TranscriptChunk]:
        """Transcribe one normalized audio source into transcript chunks.

        Empty backend output is treated as a valid but empty transcription,
        which keeps the pipeline simple for silent or low-quality recordings.
        """

        audio_path = audio_source.audio_path.expanduser().resolve()
        if not audio_path.exists():
            raise MissingAudioSourceError(
                f"Normalized audio source not found: '{audio_path}'.",
            )

        cache_artifact_found = self.cache_store.has_transcription_artifact(audio_source)
        cache_lookup_performed = self.config.transcription_cache_reuse_enabled

        # Cache reuse is attempted before backend execution so repeated runs do
        # not pay transcription cost when compatible artifacts are available.
        cached_transcription = self.cache_store.load(audio_source)
        if cached_transcription is not None:
            self._apply_transcription_metadata(
                audio_source=audio_source,
                chunks=cached_transcription.chunks,
                backend_metadata=cached_transcription.backend_metadata,
                cache_record=cached_transcription,
                cache_artifact_found=cache_artifact_found,
                cache_lookup_performed=cache_lookup_performed,
            )
            if self.config.transcription_debug_directory is not None:
                self._write_cache_debug_payload(
                    audio_source=audio_source,
                    cache_record=cached_transcription,
                )
            return cached_transcription.chunks

        if self.config.force_recompute and cache_artifact_found:
            LOGGER.info(
                "Ignoring transcription cache for '%s' because run mode is from scratch.",
                audio_source.audio_source_id,
            )

        try:
            backend_segments, backend_metadata = self.backend.transcribe(audio_path)
        except Exception as error:
            detail = str(error).strip()
            raise TranscriptionError(
                f"Failed to transcribe '{audio_source.audio_source_id}' from "
                f"'{audio_path.name}'."
                f"{f' {detail}' if detail else ''}",
            ) from error

        chunks = self._build_chunks(
            audio_source=audio_source,
            backend_segments=backend_segments,
            backend_metadata=backend_metadata,
        )

        # Persist the fresh backend result immediately so subsequent runs can
        # reuse it without re-entering the backend.
        cache_paths = self.cache_store.save(
            audio_source=audio_source,
            chunks=chunks,
            backend_metadata=backend_metadata,
        )
        self._apply_transcription_metadata(
            audio_source=audio_source,
            chunks=chunks,
            backend_metadata=backend_metadata,
            cache_record=None,
            cache_paths=cache_paths,
            cache_artifact_found=cache_artifact_found,
            cache_lookup_performed=cache_lookup_performed,
        )
        if self.config.transcription_debug_directory is not None:
            self._write_debug_payload(
                audio_source=audio_source,
                backend_segments=backend_segments,
                backend_metadata=backend_metadata,
                chunks=chunks,
            )
        return chunks

    def _build_chunks(
        self,
        audio_source: AudioSource,
        backend_segments: Sequence[BackendSegment],
        backend_metadata: dict[str, Any],
    ) -> list[TranscriptChunk]:
        """Convert backend segments into the shared transcript chunk model."""

        transcript_chunks: list[TranscriptChunk] = []
        source_language = backend_metadata.get("detected_language")
        source_language_confidence = backend_metadata.get("language_confidence")

        for segment_index, segment in enumerate(backend_segments, start=1):
            text = segment.text.strip()
            if not text:
                # Empty backend segments are ignored to keep downstream units
                # aligned with meaningful transcript content only.
                continue

            start_seconds = max(0.0, float(segment.start_seconds))
            end_seconds = max(start_seconds, float(segment.end_seconds))
            session_start_seconds = self._build_session_time(
                audio_source.session_offset_seconds,
                start_seconds,
            )
            session_end_seconds = self._build_session_time(
                audio_source.session_offset_seconds,
                end_seconds,
            )

            metadata = dict(segment.metadata)
            if segment.detected_language is None and source_language is not None:
                # Distinguish source-level language detection from per-segment
                # detection so later debugging can see where the value came from.
                metadata["language_scope"] = "audio_source"

            transcript_chunks.append(
                TranscriptChunk(
                    chunk_id=(
                        f"{audio_source.audio_source_id}_chunk_{segment_index:04d}"
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    text=text,
                    detected_language=segment.detected_language or source_language,
                    speaker_label=segment.speaker_label,
                    transcription_confidence=segment.transcription_confidence,
                    language_confidence=self._coalesce(
                        segment.language_confidence,
                        source_language_confidence,
                    ),
                    session_start_seconds=session_start_seconds,
                    session_end_seconds=session_end_seconds,
                    metadata=metadata,
                ),
            )
        return transcript_chunks

    def _apply_transcription_metadata(
        self,
        audio_source: AudioSource,
        chunks: Sequence[TranscriptChunk],
        backend_metadata: dict[str, object],
        cache_record: CachedTranscription | None,
        cache_paths: CachePaths | None = None,
        cache_artifact_found: bool = False,
        cache_lookup_performed: bool = False,
    ) -> None:
        """Attach traceable transcription metadata to one audio source."""

        resolved_cache_paths = cache_record.paths if cache_record else cache_paths
        transcription_recomputed = cache_record is None
        metadata = {
            "backend": backend_metadata.get(
                "backend",
                self.config.transcription_backend,
            ),
            "chunk_count": len(chunks),
            "empty_result": len(chunks) == 0,
            "detected_language": backend_metadata.get("detected_language"),
            "language_confidence": backend_metadata.get("language_confidence"),
            "cache_enabled": self.config.transcription_cache_enabled,
            "cache_lookup_performed": cache_lookup_performed,
            "cache_hit": cache_record is not None,
            "cache_artifact_found": cache_artifact_found,
            "cache_ignored_due_to_force_recompute": (
                cache_artifact_found and self.config.force_recompute
            ),
            "transcription_recomputed": transcription_recomputed,
            "transcription_forced_recompute": self.config.force_recompute,
            "used_cache": cache_record is not None,
            "used_existing_artifact": False,
            "forced_recompute": self.config.force_recompute,
            "status": (
                "reused_from_cache"
                if cache_record is not None
                else (
                    "executed_forced"
                    if self.config.force_recompute
                    else "executed"
                )
            ),
        }
        if cache_record is not None:
            metadata["cache_format"] = cache_record.cache_format
        if resolved_cache_paths is not None:
            # Persist cache locations directly on the audio source metadata so
            # exported JSON can point operators to the reusable artifacts.
            metadata["cache_text_path"] = str(resolved_cache_paths.text_path)
            metadata["cache_manifest_path"] = str(resolved_cache_paths.manifest_path)
        audio_source.metadata["transcription"] = metadata

    def _write_cache_debug_payload(
        self,
        audio_source: AudioSource,
        cache_record: CachedTranscription,
    ) -> None:
        """Persist a compact debug artifact describing a cache hit."""

        debug_directory = self.config.transcription_debug_directory
        if debug_directory is None:
            return

        payload = {
            "audio_source_id": audio_source.audio_source_id,
            "audio_path": str(audio_source.audio_path),
            "pipeline_execution_mode": self.config.pipeline_execution_mode,
            "cache_lookup_performed": self.config.transcription_cache_reuse_enabled,
            "cache_hit": True,
            "cache_format": cache_record.cache_format,
            "cache_text_path": str(cache_record.paths.text_path),
            "cache_manifest_path": str(cache_record.paths.manifest_path),
            "transcription_forced_recompute": self.config.force_recompute,
            "backend_metadata": cache_record.backend_metadata,
            "transcript_text": cache_record.transcript_text,
            "chunks": [chunk.to_dict() for chunk in cache_record.chunks],
        }

        debug_path = debug_directory / f"{audio_source.audio_source_id}.json"
        debug_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_debug_payload(
        self,
        audio_source: AudioSource,
        backend_segments: Sequence[BackendSegment],
        backend_metadata: dict[str, Any],
        chunks: Sequence[TranscriptChunk],
    ) -> None:
        """Persist a compact debug artifact for one audio source."""

        debug_directory = self.config.transcription_debug_directory
        if debug_directory is None:
            return

        payload = {
            "audio_source_id": audio_source.audio_source_id,
            "audio_path": str(audio_source.audio_path),
            "pipeline_execution_mode": self.config.pipeline_execution_mode,
            "cache_lookup_performed": self.config.transcription_cache_reuse_enabled,
            "cache_hit": False,
            "transcription_forced_recompute": self.config.force_recompute,
            "backend_metadata": backend_metadata,
            "backend_segments": [
                {
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    "text": segment.text,
                    "detected_language": segment.detected_language,
                    "speaker_label": segment.speaker_label,
                    "transcription_confidence": segment.transcription_confidence,
                    "language_confidence": segment.language_confidence,
                    "metadata": segment.metadata,
                }
                for segment in backend_segments
            ],
            "chunks": [chunk.to_dict() for chunk in chunks],
        }

        debug_path = debug_directory / f"{audio_source.audio_source_id}.json"
        debug_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

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
    def _coalesce(primary: Any, fallback: Any) -> Any:
        """Return the first non-`None` value without treating zero as missing."""

        if primary is not None:
            return primary
        return fallback
