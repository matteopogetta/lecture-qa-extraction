"""Reusable transcription and alignment cache helpers.

The cache stores a plain-text transcript for quick inspection and a compact
JSON sidecar with chunk-level details. This keeps repeated runs fast without
discarding the timing and metadata needed by downstream pipeline stages.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AlignedTranscript,
    AlignedTranscriptSegment,
    AlignedWord,
    AudioSource,
    DiarizationResult,
    DiarizationSegment,
    Sentence,
    SentenceCollection,
    TranscriptChunk,
    Utterance,
    UtteranceCollection,
)
from lecture_analyzer.core.types import SpeakerRole


@dataclass(slots=True)
class CachePaths:
    """Resolved cache file paths for one original media source."""

    source_path: Path
    text_path: Path
    manifest_path: Path


@dataclass(slots=True)
class CachedTranscription:
    """In-memory representation of a cached transcription."""

    chunks: list[TranscriptChunk]
    backend_metadata: dict[str, Any]
    transcript_text: str
    cache_format: str
    paths: CachePaths


@dataclass(slots=True)
class AlignmentPaths:
    """Resolved alignment artifact paths for one original media source."""

    source_path: Path
    manifest_path: Path


@dataclass(slots=True)
class CachedAlignment:
    """In-memory representation of a cached alignment artifact."""

    aligned_transcript: AlignedTranscript
    cache_format: str
    paths: AlignmentPaths


@dataclass(slots=True)
class DiarizationPaths:
    """Resolved diarization artifact paths for one original media source."""

    source_path: Path
    manifest_path: Path


@dataclass(slots=True)
class CachedDiarization:
    """In-memory representation of a cached diarization artifact."""

    diarization_result: DiarizationResult
    cache_format: str
    paths: DiarizationPaths


@dataclass(slots=True)
class UtterancePaths:
    """Resolved utterance artifact paths for one original media source."""

    source_path: Path
    manifest_path: Path


@dataclass(slots=True)
class CachedUtterances:
    """In-memory representation of a cached utterance artifact."""

    utterance_collection: UtteranceCollection
    cache_format: str
    paths: UtterancePaths


@dataclass(slots=True)
class SentencePaths:
    """Resolved sentence artifact paths for one original media source."""

    source_path: Path
    manifest_path: Path


@dataclass(slots=True)
class CachedSentences:
    """In-memory representation of a cached sentence artifact."""

    sentence_collection: SentenceCollection
    cache_format: str
    paths: SentencePaths


class TranscriptionCacheStore:
    """Persist and reload reusable transcription artifacts."""

    _CACHE_VERSION = 1
    _ALIGNMENT_CACHE_VERSION = 1
    _DIARIZATION_CACHE_VERSION = 2
    _UTTERANCE_CACHE_VERSION = 1
    _SENTENCE_CACHE_VERSION = 2

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def load(self, audio_source: AudioSource) -> CachedTranscription | None:
        """Return a cached transcription when a reusable artifact exists."""

        if not self.config.transcription_cache_reuse_enabled:
            return None

        paths = self.resolve_paths(audio_source)
        if paths.manifest_path.exists():
            # Prefer the manifest because it preserves chunk-level timing and
            # metadata, which are required by later pipeline stages.
            cached_transcription = self._load_manifest(
                audio_source=audio_source,
                paths=paths,
            )
            if cached_transcription is not None:
                return cached_transcription

        if (
            self.config.transcription_cache_allow_text_fallback
            and paths.text_path.exists()
        ):
            # Fall back to plain text only when explicitly allowed. This keeps
            # repeated runs fast even if structured cache data is unavailable.
            transcript_text = paths.text_path.read_text(encoding="utf-8").strip()
            return CachedTranscription(
                chunks=self._build_text_only_chunks(
                    audio_source=audio_source,
                    transcript_text=transcript_text,
                ),
                backend_metadata={"backend": "text-cache"},
                transcript_text=transcript_text,
                cache_format="text_only",
                paths=paths,
            )

        return None

    def save(
        self,
        audio_source: AudioSource,
        chunks: Sequence[TranscriptChunk],
        backend_metadata: dict[str, Any],
    ) -> CachePaths | None:
        """Persist cache artifacts for one audio source when enabled."""

        if not self.config.transcription_cache_enabled:
            return None

        paths = self.resolve_paths(audio_source)
        paths.text_path.parent.mkdir(parents=True, exist_ok=True)

        # Store both a human-readable text transcript and a structured
        # manifest so the cache remains easy to inspect and robust to reuse.
        transcript_text = self._build_transcript_text(chunks)
        paths.text_path.write_text(transcript_text, encoding="utf-8")

        payload = {
            "cache_version": self._CACHE_VERSION,
            "source_reference": {
                "original_path": str(paths.source_path),
                "audio_path": str(audio_source.audio_path),
                "audio_source_id": audio_source.audio_source_id,
                "input_source_id": audio_source.input_source_id,
                "order_index": audio_source.order_index,
                "session_offset_seconds": audio_source.session_offset_seconds,
                "duration_seconds": audio_source.duration_seconds,
                "extracted_from_video": audio_source.extracted_from_video,
            },
            "config_snapshot": self._build_config_snapshot(),
            "backend_metadata": backend_metadata,
            "transcript_text": transcript_text,
            "chunks": [chunk.to_dict() for chunk in chunks],
        }
        paths.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def load_alignment(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> CachedAlignment | None:
        """Return a cached alignment artifact when it is still reusable."""

        if not self.config.intermediate_artifact_reuse_enabled:
            return None

        paths = self.resolve_alignment_paths(audio_source)
        if not paths.manifest_path.exists():
            return None

        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not self._is_alignment_manifest_compatible(payload, transcript_chunks):
            return None

        raw_aligned_transcript = payload.get("aligned_transcript")
        if not isinstance(raw_aligned_transcript, dict):
            return None

        return CachedAlignment(
            aligned_transcript=self._deserialize_aligned_transcript(
                audio_source=audio_source,
                transcript_chunks=transcript_chunks,
                raw_payload=raw_aligned_transcript,
            ),
            cache_format="manifest",
            paths=paths,
        )

    def save_alignment(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
        aligned_transcript: AlignedTranscript,
    ) -> AlignmentPaths:
        """Persist one aligned transcript artifact for later reuse."""

        paths = self.resolve_alignment_paths(audio_source)
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": self._ALIGNMENT_CACHE_VERSION,
            "artifact_type": "whisperx_alignment",
            "source_reference": {
                "original_path": str(paths.source_path),
                "audio_path": str(audio_source.audio_path),
                "audio_source_id": audio_source.audio_source_id,
                "input_source_id": audio_source.input_source_id,
                "order_index": audio_source.order_index,
                "session_offset_seconds": audio_source.session_offset_seconds,
                "duration_seconds": audio_source.duration_seconds,
            },
            "config_snapshot": self._build_alignment_config_snapshot(),
            "transcript_reference": {
                "chunk_count": len(transcript_chunks),
                "digest": self._build_transcript_digest(transcript_chunks),
            },
            "aligned_transcript": aligned_transcript.to_dict(),
        }
        paths.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def load_diarization(
        self,
        audio_source: AudioSource,
    ) -> CachedDiarization | None:
        """Return a cached diarization artifact when it is still reusable."""

        if not self.config.intermediate_artifact_reuse_enabled:
            return None

        paths = self.resolve_diarization_paths(audio_source)
        if not paths.manifest_path.exists():
            return None

        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not self._is_diarization_manifest_compatible(payload, audio_source):
            return None

        raw_result = payload.get("diarization_result")
        if not isinstance(raw_result, dict):
            return None

        return CachedDiarization(
            diarization_result=self._deserialize_diarization_result(
                audio_source=audio_source,
                raw_payload=raw_result,
            ),
            cache_format="manifest",
            paths=paths,
        )

    def save_diarization(
        self,
        audio_source: AudioSource,
        diarization_result: DiarizationResult,
    ) -> DiarizationPaths:
        """Persist one diarization artifact for later reuse."""

        paths = self.resolve_diarization_paths(audio_source)
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": self._DIARIZATION_CACHE_VERSION,
            "artifact_type": "diarization",
            "source_reference": {
                "original_path": str(paths.source_path),
                "audio_path": str(audio_source.audio_path),
                "audio_source_id": audio_source.audio_source_id,
                "input_source_id": audio_source.input_source_id,
                "order_index": audio_source.order_index,
                "session_offset_seconds": audio_source.session_offset_seconds,
                "duration_seconds": audio_source.duration_seconds,
            },
            "config_snapshot": self._build_diarization_config_snapshot(),
            "audio_reference": self._build_audio_reference(audio_source.audio_path),
            "diarization_result": diarization_result.to_dict(),
        }
        paths.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def load_utterances(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
    ) -> CachedUtterances | None:
        """Return a cached utterance artifact when it is still reusable."""

        if not self.config.intermediate_artifact_reuse_enabled:
            return None

        paths = self.resolve_utterance_paths(audio_source)
        if not paths.manifest_path.exists():
            return None

        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not self._is_utterance_manifest_compatible(payload, aligned_transcript):
            return None

        raw_collection = payload.get("utterance_collection")
        if not isinstance(raw_collection, dict):
            return None

        return CachedUtterances(
            utterance_collection=self._deserialize_utterance_collection(
                audio_source=audio_source,
                aligned_transcript=aligned_transcript,
                raw_payload=raw_collection,
            ),
            cache_format="manifest",
            paths=paths,
        )

    def save_utterances(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        utterance_collection: UtteranceCollection,
    ) -> UtterancePaths:
        """Persist one utterance artifact for later reuse."""

        paths = self.resolve_utterance_paths(audio_source)
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": self._UTTERANCE_CACHE_VERSION,
            "artifact_type": "utterances",
            "source_reference": {
                "original_path": str(paths.source_path),
                "audio_path": str(audio_source.audio_path),
                "audio_source_id": audio_source.audio_source_id,
                "input_source_id": audio_source.input_source_id,
                "order_index": audio_source.order_index,
                "session_offset_seconds": audio_source.session_offset_seconds,
                "duration_seconds": audio_source.duration_seconds,
            },
            "config_snapshot": self._build_utterance_config_snapshot(),
            "alignment_reference": {
                "segment_count": len(aligned_transcript.segments),
                "digest": self._build_aligned_transcript_digest(aligned_transcript),
            },
            "utterance_collection": utterance_collection.to_dict(),
        }
        paths.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def load_sentences(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
    ) -> CachedSentences | None:
        """Return a cached sentence artifact when it is still reusable."""

        if not self.config.intermediate_artifact_reuse_enabled:
            return None

        paths = self.resolve_sentence_paths(audio_source)
        if not paths.manifest_path.exists():
            return None

        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not self._is_sentence_manifest_compatible(payload, utterances):
            return None

        raw_collection = payload.get("sentence_collection")
        if not isinstance(raw_collection, dict):
            return None

        return CachedSentences(
            sentence_collection=self._deserialize_sentence_collection(
                audio_source=audio_source,
                utterances=utterances,
                raw_payload=raw_collection,
            ),
            cache_format="manifest",
            paths=paths,
        )

    def save_sentences(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        sentence_collection: SentenceCollection,
    ) -> SentencePaths:
        """Persist one sentence artifact for later reuse."""

        paths = self.resolve_sentence_paths(audio_source)
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": self._SENTENCE_CACHE_VERSION,
            "artifact_type": "sentences",
            "source_reference": {
                "original_path": str(paths.source_path),
                "audio_path": str(audio_source.audio_path),
                "audio_source_id": audio_source.audio_source_id,
                "input_source_id": audio_source.input_source_id,
                "order_index": audio_source.order_index,
                "session_offset_seconds": audio_source.session_offset_seconds,
                "duration_seconds": audio_source.duration_seconds,
            },
            "config_snapshot": self._build_sentence_config_snapshot(),
            "utterance_reference": {
                "utterance_count": len(utterances),
                "digest": self._build_utterance_digest(utterances),
            },
            "sentence_collection": sentence_collection.to_dict(),
        }
        paths.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def resolve_paths(self, audio_source: AudioSource) -> CachePaths:
        """Build deterministic cache paths for one audio source."""

        source_path = self._resolve_source_path(audio_source)
        cache_directory = self.config.transcription_cache_directory

        if cache_directory is None:
            # Default behavior keeps cache artifacts next to the original media,
            # which is convenient for single-user local workflows.
            text_path = source_path.with_suffix(
                self.config.transcription_cache_text_extension,
            )
            manifest_path = source_path.with_suffix(
                self.config.transcription_cache_manifest_extension,
            )
        else:
            # Centralized cache layouts derive a stable directory key from the
            # source path so duplicate filenames from different folders do not collide.
            directory_key = self._build_directory_key(source_path)
            target_directory = cache_directory / directory_key
            text_path = (
                target_directory
                / f"{source_path.stem}{self.config.transcription_cache_text_extension}"
            )
            manifest_path = (
                target_directory
                / (
                    f"{source_path.stem}"
                    f"{self.config.transcription_cache_manifest_extension}"
                )
            )

        return CachePaths(
            source_path=source_path,
            text_path=text_path,
            manifest_path=manifest_path,
        )

    def resolve_alignment_paths(self, audio_source: AudioSource) -> AlignmentPaths:
        """Build deterministic alignment artifact paths for one audio source."""

        source_path = self._resolve_source_path(audio_source)
        directory_key = self._build_directory_key(source_path)
        target_directory = self.config.alignment_artifacts_directory / directory_key
        manifest_path = target_directory / f"{source_path.stem}.alignment.json"
        return AlignmentPaths(
            source_path=source_path,
            manifest_path=manifest_path,
        )

    def resolve_diarization_paths(self, audio_source: AudioSource) -> DiarizationPaths:
        """Build deterministic diarization artifact paths for one audio source."""

        source_path = self._resolve_source_path(audio_source)
        directory_key = self._build_directory_key(source_path)
        target_directory = self.config.diarization_artifacts_directory / directory_key
        manifest_path = target_directory / f"{source_path.stem}.diarization.json"
        return DiarizationPaths(
            source_path=source_path,
            manifest_path=manifest_path,
        )

    def resolve_utterance_paths(self, audio_source: AudioSource) -> UtterancePaths:
        """Build deterministic utterance artifact paths for one audio source."""

        source_path = self._resolve_source_path(audio_source)
        directory_key = self._build_directory_key(source_path)
        target_directory = self.config.utterance_artifacts_directory / directory_key
        manifest_path = target_directory / f"{source_path.stem}.utterances.json"
        return UtterancePaths(
            source_path=source_path,
            manifest_path=manifest_path,
        )

    def resolve_sentence_paths(self, audio_source: AudioSource) -> SentencePaths:
        """Build deterministic sentence artifact paths for one audio source."""

        source_path = self._resolve_source_path(audio_source)
        directory_key = self._build_directory_key(source_path)
        target_directory = self.config.sentence_artifacts_directory / directory_key
        manifest_path = target_directory / f"{source_path.stem}.sentences.json"
        return SentencePaths(
            source_path=source_path,
            manifest_path=manifest_path,
        )

    def has_transcription_artifact(self, audio_source: AudioSource) -> bool:
        """Return whether any transcription cache artifact exists for a source."""

        paths = self.resolve_paths(audio_source)
        return paths.manifest_path.exists() or paths.text_path.exists()

    def has_alignment_artifact(self, audio_source: AudioSource) -> bool:
        """Return whether an alignment manifest exists for a source."""

        return self.resolve_alignment_paths(audio_source).manifest_path.exists()

    def has_diarization_artifact(self, audio_source: AudioSource) -> bool:
        """Return whether a diarization manifest exists for a source."""

        return self.resolve_diarization_paths(audio_source).manifest_path.exists()

    def has_utterance_artifact(self, audio_source: AudioSource) -> bool:
        """Return whether an utterance manifest exists for a source."""

        return self.resolve_utterance_paths(audio_source).manifest_path.exists()

    def has_sentence_artifact(self, audio_source: AudioSource) -> bool:
        """Return whether a sentence manifest exists for a source."""

        return self.resolve_sentence_paths(audio_source).manifest_path.exists()

    def _load_manifest(
        self,
        audio_source: AudioSource,
        paths: CachePaths,
    ) -> CachedTranscription | None:
        """Load cached chunks from the JSON sidecar when it is compatible."""

        try:
            payload = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not self._is_manifest_compatible(payload):
            return None

        raw_chunks = payload.get("chunks")
        if not isinstance(raw_chunks, list):
            return None

        # Rebuild chunk identifiers against the current audio source so cache
        # reuse stays valid even when the session-level source ids change.
        chunks = self._deserialize_chunks(
            audio_source=audio_source,
            raw_chunks=raw_chunks,
        )
        transcript_text = self._coerce_transcript_text(
            payload.get("transcript_text"),
            chunks,
        )
        backend_metadata = payload.get("backend_metadata")
        if not isinstance(backend_metadata, dict):
            backend_metadata = {}

        return CachedTranscription(
            chunks=chunks,
            backend_metadata=backend_metadata,
            transcript_text=transcript_text,
            cache_format="manifest",
            paths=paths,
        )

    def _is_manifest_compatible(self, payload: dict[str, Any]) -> bool:
        """Return whether a cache manifest can be reused safely."""

        if not isinstance(payload, dict):
            return False

        cache_version = payload.get("cache_version")
        if cache_version != self._CACHE_VERSION:
            return False

        if not self.config.transcription_cache_require_backend_match:
            return True

        snapshot = payload.get("config_snapshot")
        if not isinstance(snapshot, dict):
            return False
        return snapshot == self._build_config_snapshot()

    def _is_alignment_manifest_compatible(
        self,
        payload: dict[str, Any],
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> bool:
        """Return whether a stored alignment artifact can be reused safely."""

        if not isinstance(payload, dict):
            return False

        if payload.get("cache_version") != self._ALIGNMENT_CACHE_VERSION:
            return False

        snapshot = payload.get("config_snapshot")
        if not isinstance(snapshot, dict):
            return False
        if snapshot != self._build_alignment_config_snapshot():
            return False

        transcript_reference = payload.get("transcript_reference")
        if not isinstance(transcript_reference, dict):
            return False

        return transcript_reference == {
            "chunk_count": len(transcript_chunks),
            "digest": self._build_transcript_digest(transcript_chunks),
        }

    def _is_diarization_manifest_compatible(
        self,
        payload: dict[str, Any],
        audio_source: AudioSource,
    ) -> bool:
        """Return whether a stored diarization artifact can be reused safely."""

        if not isinstance(payload, dict):
            return False

        if payload.get("cache_version") != self._DIARIZATION_CACHE_VERSION:
            return False

        snapshot = payload.get("config_snapshot")
        if not isinstance(snapshot, dict):
            return False
        if snapshot != self._build_diarization_config_snapshot():
            return False

        audio_reference = payload.get("audio_reference")
        if not isinstance(audio_reference, dict):
            return False
        return audio_reference == self._build_audio_reference(audio_source.audio_path)

    def _is_utterance_manifest_compatible(
        self,
        payload: dict[str, Any],
        aligned_transcript: AlignedTranscript,
    ) -> bool:
        """Return whether a stored utterance artifact can be reused safely."""

        if not isinstance(payload, dict):
            return False

        if payload.get("cache_version") != self._UTTERANCE_CACHE_VERSION:
            return False

        snapshot = payload.get("config_snapshot")
        if not isinstance(snapshot, dict):
            return False
        if snapshot != self._build_utterance_config_snapshot():
            return False

        alignment_reference = payload.get("alignment_reference")
        if not isinstance(alignment_reference, dict):
            return False

        return alignment_reference == {
            "segment_count": len(aligned_transcript.segments),
            "digest": self._build_aligned_transcript_digest(aligned_transcript),
        }

    def _is_sentence_manifest_compatible(
        self,
        payload: dict[str, Any],
        utterances: Sequence[Utterance],
    ) -> bool:
        """Return whether a stored sentence artifact can be reused safely."""

        if not isinstance(payload, dict):
            return False

        if payload.get("cache_version") != self._SENTENCE_CACHE_VERSION:
            return False

        snapshot = payload.get("config_snapshot")
        if not isinstance(snapshot, dict):
            return False
        if snapshot != self._build_sentence_config_snapshot():
            return False

        utterance_reference = payload.get("utterance_reference")
        if not isinstance(utterance_reference, dict):
            return False

        return utterance_reference == {
            "utterance_count": len(utterances),
            "digest": self._build_utterance_digest(utterances),
        }

    def _deserialize_chunks(
        self,
        audio_source: AudioSource,
        raw_chunks: Sequence[Any],
    ) -> list[TranscriptChunk]:
        """Convert cached raw chunk payloads into session-specific chunks."""

        chunks: list[TranscriptChunk] = []
        for index, raw_chunk in enumerate(raw_chunks, start=1):
            if not isinstance(raw_chunk, dict):
                continue

            start_seconds = self._safe_float(raw_chunk.get("start_seconds"))
            end_seconds = self._safe_float(raw_chunk.get("end_seconds"))
            if end_seconds < start_seconds:
                end_seconds = start_seconds

            text = str(raw_chunk.get("text", "")).strip()
            metadata = raw_chunk.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            chunks.append(
                TranscriptChunk(
                    chunk_id=f"{audio_source.audio_source_id}_chunk_{index:04d}",
                    audio_source_id=audio_source.audio_source_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    text=text,
                    detected_language=self._optional_string(
                        raw_chunk.get("detected_language"),
                    ),
                    speaker_label=self._optional_string(
                        raw_chunk.get("speaker_label"),
                    ),
                    estimated_speaker_role=self._parse_speaker_role(
                        raw_chunk.get("estimated_speaker_role"),
                    ),
                    transcription_confidence=self._optional_float(
                        raw_chunk.get("transcription_confidence"),
                    ),
                    language_confidence=self._optional_float(
                        raw_chunk.get("language_confidence"),
                    ),
                    session_start_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        start_seconds,
                    ),
                    session_end_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        end_seconds,
                    ),
                    metadata=metadata,
                ),
            )
        return chunks

    def _deserialize_aligned_transcript(
        self,
        audio_source: AudioSource,
        transcript_chunks: Sequence[TranscriptChunk],
        raw_payload: dict[str, Any],
    ) -> AlignedTranscript:
        """Convert cached alignment data into the current source context."""

        raw_segments = raw_payload.get("segments")
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
            segment_metadata = raw_segment.get("metadata")
            if not isinstance(segment_metadata, dict):
                segment_metadata = {}

            raw_words = raw_segment.get("words")
            if not isinstance(raw_words, list):
                raw_words = []

            words: list[AlignedWord] = []
            for word_index, raw_word in enumerate(raw_words, start=1):
                if not isinstance(raw_word, dict):
                    continue
                word_metadata = raw_word.get("metadata")
                if not isinstance(word_metadata, dict):
                    word_metadata = {}
                words.append(
                    AlignedWord(
                        word_id=(
                            f"{audio_source.audio_source_id}"
                            f"_aligned_word_{segment_index:04d}_{word_index:04d}"
                        ),
                        text=str(raw_word.get("text", "")).strip(),
                        start_seconds=self._optional_float(
                            raw_word.get("start_seconds"),
                        ),
                        end_seconds=self._optional_float(raw_word.get("end_seconds")),
                        confidence=self._optional_float(raw_word.get("confidence")),
                        session_start_seconds=self._optional_float(
                            raw_word.get("session_start_seconds"),
                        ),
                        session_end_seconds=self._optional_float(
                            raw_word.get("session_end_seconds"),
                        ),
                        metadata=word_metadata,
                    ),
                )

            segments.append(
                AlignedTranscriptSegment(
                    segment_id=(
                        f"{audio_source.audio_source_id}"
                        f"_aligned_segment_{segment_index:04d}"
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    transcript_chunk_id=(
                        linked_chunk.chunk_id
                        if linked_chunk is not None
                        else self._optional_string(
                            raw_segment.get("transcript_chunk_id"),
                        )
                    ),
                    start_seconds=self._optional_float(raw_segment.get("start_seconds")),
                    end_seconds=self._optional_float(raw_segment.get("end_seconds")),
                    text=str(raw_segment.get("text", "")).strip(),
                    detected_language=self._optional_string(
                        raw_segment.get("detected_language"),
                    ),
                    words=words,
                    metadata=segment_metadata,
                ),
            )

        metadata = raw_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return AlignedTranscript(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=self._optional_string(
                raw_payload.get("detected_language"),
            ),
            source_chunk_count=len(transcript_chunks),
            segments=segments,
            metadata=metadata,
        )

    def _deserialize_diarization_result(
        self,
        audio_source: AudioSource,
        raw_payload: dict[str, Any],
    ) -> DiarizationResult:
        """Convert cached diarization data into the current source context."""

        raw_segments = raw_payload.get("segments")
        if not isinstance(raw_segments, list):
            raw_segments = []

        segments: list[DiarizationSegment] = []
        speaker_ids: set[str] = set()
        for segment_index, raw_segment in enumerate(raw_segments, start=1):
            if not isinstance(raw_segment, dict):
                continue

            speaker_id = self._optional_string(raw_segment.get("speaker_id"))
            start_seconds = self._optional_float(raw_segment.get("start_seconds"))
            end_seconds = self._optional_float(raw_segment.get("end_seconds"))
            if speaker_id is None or start_seconds is None or end_seconds is None:
                continue
            if end_seconds < start_seconds:
                end_seconds = start_seconds

            metadata = raw_segment.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            speaker_ids.add(speaker_id)
            segments.append(
                DiarizationSegment(
                    diarization_segment_id=(
                        f"{audio_source.audio_source_id}"
                        f"_diarization_segment_{segment_index:04d}"
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    speaker_id=speaker_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    segment_source=(
                        self._optional_string(raw_segment.get("segment_source"))
                        or "regular"
                    ),
                    session_start_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        start_seconds,
                    ),
                    session_end_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        end_seconds,
                    ),
                    metadata=metadata,
                ),
            )

        metadata = raw_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return DiarizationResult(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            preferred_segment_source=self._optional_string(
                raw_payload.get("preferred_segment_source"),
            )
            or self._optional_string(metadata.get("preferred_segment_source"))
            or "regular",
            available_segment_sources=self._coerce_string_list(
                raw_payload.get("available_segment_sources"),
            )
            or self._coerce_string_list(metadata.get("available_segment_sources"))
            or ["regular"],
            speaker_ids=sorted(speaker_ids),
            segments=segments,
            metadata=metadata,
        )

    def _deserialize_utterance_collection(
        self,
        audio_source: AudioSource,
        aligned_transcript: AlignedTranscript,
        raw_payload: dict[str, Any],
    ) -> UtteranceCollection:
        """Convert cached utterances into the current source context."""

        raw_utterances = raw_payload.get("utterances")
        if not isinstance(raw_utterances, list):
            raw_utterances = []

        utterances: list[Utterance] = []
        for raw_utterance in raw_utterances:
            if not isinstance(raw_utterance, dict):
                continue

            segment_index = self._optional_int(
                raw_utterance.get("aligned_segment_index"),
            )
            if segment_index is None:
                continue
            if segment_index < 1 or segment_index > len(aligned_transcript.segments):
                continue

            current_segment = aligned_transcript.segments[segment_index - 1]
            metadata = raw_utterance.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            text = str(raw_utterance.get("text", "")).strip()
            if not text:
                continue

            start_seconds = self._optional_float(raw_utterance.get("start_seconds"))
            end_seconds = self._optional_float(raw_utterance.get("end_seconds"))
            if start_seconds is None:
                start_seconds = current_segment.start_seconds
            if end_seconds is None:
                end_seconds = current_segment.end_seconds
            if start_seconds is None or end_seconds is None:
                continue
            if end_seconds < start_seconds:
                end_seconds = start_seconds

            start_word_index = self._optional_int(
                raw_utterance.get("start_word_index"),
            )
            end_word_index = self._optional_int(raw_utterance.get("end_word_index"))

            utterances.append(
                Utterance(
                    utterance_id=(
                        self._normalize_utterance_id(
                            audio_source_id=audio_source.audio_source_id,
                            utterance_id=self._optional_string(
                                raw_utterance.get("utterance_id"),
                            ),
                        )
                        or self._build_fallback_utterance_id(
                            audio_source_id=audio_source.audio_source_id,
                            segment_index=segment_index,
                            start_word_index=start_word_index,
                            end_word_index=end_word_index,
                            start_seconds=start_seconds,
                            end_seconds=end_seconds,
                            text=text,
                        )
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    text=text,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    aligned_segment_id=current_segment.segment_id,
                    aligned_segment_index=segment_index,
                    transcript_chunk_id=current_segment.transcript_chunk_id,
                    start_word_index=start_word_index,
                    end_word_index=end_word_index,
                    source_word_ids=self._resolve_current_word_ids(
                        current_segment=current_segment,
                        start_word_index=start_word_index,
                        end_word_index=end_word_index,
                    ),
                    detected_language=(
                        self._optional_string(raw_utterance.get("detected_language"))
                        or current_segment.detected_language
                        or aligned_transcript.detected_language
                    ),
                    speaker_id=self._optional_string(raw_utterance.get("speaker_id")),
                    speaker_attribution_status=self._optional_string(
                        raw_utterance.get("speaker_attribution_status"),
                    ),
                    speaker_confidence_score=self._optional_float(
                        raw_utterance.get("speaker_confidence_score"),
                    ),
                    speaker_is_uncertain=bool(
                        raw_utterance.get("speaker_is_uncertain", False),
                    ),
                    session_start_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        start_seconds,
                    ),
                    session_end_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        end_seconds,
                    ),
                    metadata=metadata,
                ),
            )

        metadata = raw_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return UtteranceCollection(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=(
                self._optional_string(raw_payload.get("detected_language"))
                or aligned_transcript.detected_language
            ),
            source_segment_count=len(aligned_transcript.segments),
            source_word_count=sum(
                len(segment.words) for segment in aligned_transcript.segments
            ),
            utterances=utterances,
            metadata=metadata,
        )

    def _deserialize_sentence_collection(
        self,
        audio_source: AudioSource,
        utterances: Sequence[Utterance],
        raw_payload: dict[str, Any],
    ) -> SentenceCollection:
        """Convert cached sentences into the current source context."""

        raw_sentences = raw_payload.get("sentences")
        if not isinstance(raw_sentences, list):
            raw_sentences = []

        sentence_items: list[Sentence] = []
        for sentence_index, raw_sentence in enumerate(raw_sentences, start=1):
            if not isinstance(raw_sentence, dict):
                continue

            metadata = raw_sentence.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}

            text = str(raw_sentence.get("text", "")).strip()
            if not text:
                continue

            start_utterance_index = self._optional_int(
                raw_sentence.get("source_utterance_start_index"),
            )
            end_utterance_index = self._optional_int(
                raw_sentence.get("source_utterance_end_index"),
            )
            bound_utterances = self._resolve_current_sentence_utterances(
                utterances=utterances,
                start_index=start_utterance_index,
                end_index=end_utterance_index,
            )

            start_seconds = self._optional_float(raw_sentence.get("start_seconds"))
            end_seconds = self._optional_float(raw_sentence.get("end_seconds"))
            if start_seconds is None and bound_utterances:
                start_seconds = min(
                    utterance.start_seconds for utterance in bound_utterances
                )
            if end_seconds is None and bound_utterances:
                end_seconds = max(utterance.end_seconds for utterance in bound_utterances)
            if start_seconds is None or end_seconds is None:
                continue
            if end_seconds < start_seconds:
                end_seconds = start_seconds

            sentence_items.append(
                Sentence(
                    sentence_id=f"{audio_source.audio_source_id}_sentence_{sentence_index:04d}",
                    audio_source_id=audio_source.audio_source_id,
                    text=text,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    source_utterance_ids=[
                        utterance.utterance_id for utterance in bound_utterances
                    ],
                    source_utterance_start_index=start_utterance_index,
                    source_utterance_end_index=end_utterance_index,
                    detected_language=(
                        self._optional_string(raw_sentence.get("detected_language"))
                        or self._resolve_sentence_language(bound_utterances)
                    ),
                    speaker_id=(
                        self._optional_string(raw_sentence.get("speaker_id"))
                        or self._resolve_sentence_speaker(bound_utterances)
                    ),
                    speaker_resolution_status=self._optional_string(
                        raw_sentence.get("speaker_resolution_status"),
                    ),
                    speaker_confidence_label=self._optional_string(
                        raw_sentence.get("speaker_confidence_label"),
                    ),
                    speaker_stability_label=self._optional_string(
                        raw_sentence.get("speaker_stability_label"),
                    ),
                    speaker_assignment_method=self._optional_string(
                        raw_sentence.get("speaker_assignment_method"),
                    ),
                    speaker_evidence_summary=self._optional_string(
                        raw_sentence.get("speaker_evidence_summary"),
                    ),
                    merge_safety_label=self._optional_string(
                        raw_sentence.get("merge_safety_label"),
                    ),
                    semantic_quality_label=self._optional_string(
                        raw_sentence.get("semantic_quality_label"),
                    ),
                    length_bucket=self._optional_string(
                        raw_sentence.get("length_bucket"),
                    ),
                    duration_bucket=self._optional_string(
                        raw_sentence.get("duration_bucket"),
                    ),
                    review_priority=self._optional_string(
                        raw_sentence.get("review_priority"),
                    ),
                    sentence_review_flags=self._coerce_string_list(
                        raw_sentence.get("sentence_review_flags"),
                    ),
                    session_start_seconds=(
                        self._resolve_sentence_session_time(
                            bound_utterances=bound_utterances,
                            attribute_name="session_start_seconds",
                        )
                        if bound_utterances
                        else self._optional_float(
                            raw_sentence.get("session_start_seconds"),
                        )
                    ),
                    session_end_seconds=(
                        self._resolve_sentence_session_time(
                            bound_utterances=bound_utterances,
                            attribute_name="session_end_seconds",
                        )
                        if bound_utterances
                        else self._optional_float(raw_sentence.get("session_end_seconds"))
                    ),
                    metadata=metadata,
                ),
            )

        metadata = raw_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return SentenceCollection(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            detected_language=(
                self._optional_string(raw_payload.get("detected_language"))
                or self._resolve_sentence_language(utterances)
            ),
            source_utterance_count=len(utterances),
            sentences=sentence_items,
            metadata=metadata,
        )

    def _build_text_only_chunks(
        self,
        audio_source: AudioSource,
        transcript_text: str,
    ) -> list[TranscriptChunk]:
        """Return a synthetic one-chunk transcript from a text cache."""

        if not transcript_text:
            return []

        # A plain-text fallback cannot preserve original timestamps, so the
        # synthetic chunk spans the best available source duration.
        end_seconds = max(0.0, float(audio_source.duration_seconds or 0.0))
        return [
            TranscriptChunk(
                chunk_id=f"{audio_source.audio_source_id}_chunk_0001",
                audio_source_id=audio_source.audio_source_id,
                start_seconds=0.0,
                end_seconds=end_seconds,
                text=transcript_text,
                session_start_seconds=self._build_session_time(
                    audio_source.session_offset_seconds,
                    0.0,
                ),
                session_end_seconds=self._build_session_time(
                    audio_source.session_offset_seconds,
                    end_seconds,
                ),
                metadata={
                    "cache_fallback": "text_only",
                    "timing_source": "synthetic",
                },
            ),
        ]

    def _resolve_source_path(self, audio_source: AudioSource) -> Path:
        """Return the original media path used as cache identity."""

        raw_original_path = audio_source.metadata.get("original_path")
        if isinstance(raw_original_path, str) and raw_original_path.strip():
            return Path(raw_original_path).expanduser().resolve()
        return audio_source.audio_path.expanduser().resolve()

    def _build_directory_key(self, source_path: Path) -> str:
        """Return a stable directory key for centralized cache layouts."""

        parent_digest = hashlib.sha1(
            str(source_path.parent).encode("utf-8"),
        ).hexdigest()[:10]
        return f"{source_path.stem}_{parent_digest}"

    def _build_config_snapshot(self) -> dict[str, Any]:
        """Return the cache-relevant configuration subset."""

        return {
            "backend": self.config.transcription_backend,
            "model_name": self.config.transcription_model_name,
            "language_mode": self.config.transcription_language_mode,
            "language": self.config.transcription_language,
            "beam_size": self.config.transcription_beam_size,
        }

    def _build_alignment_config_snapshot(self) -> dict[str, Any]:
        """Return the alignment-relevant configuration subset."""

        return {
            "alignment_enabled": self.config.transcript_alignment_enabled,
            "alignment_model_name": self.config.transcript_alignment_model_name,
            "alignment_device": self.config.transcript_alignment_device,
        }

    def _build_diarization_config_snapshot(self) -> dict[str, Any]:
        """Return the diarization-relevant configuration subset."""

        return {
            "diarization_enabled": self.config.diarization_enabled,
            "diarization_model_name": self.config.diarization_model_name,
            "diarization_device": self.config.diarization_device,
            "diarization_num_speakers": self.config.diarization_num_speakers,
            "diarization_min_speakers": self.config.diarization_min_speakers,
            "diarization_max_speakers": self.config.diarization_max_speakers,
            "diarization_prefer_exclusive": self.config.diarization_prefer_exclusive,
        }

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        """Return a compact list of non-empty strings from raw payload data."""

        if not isinstance(value, list):
            return []
        coerced_values: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            normalized_item = item.strip()
            if not normalized_item:
                continue
            coerced_values.append(normalized_item)
        return coerced_values

    def _build_utterance_config_snapshot(self) -> dict[str, Any]:
        """Return the utterance-builder-relevant configuration subset."""

        return {
            "utterance_max_gap_seconds": self.config.utterance_max_gap_seconds,
        }

    def _build_sentence_config_snapshot(self) -> dict[str, Any]:
        """Return the sentence-reconstruction-relevant configuration subset."""

        return {
            "sentence_reconstruction_enabled": (
                self.config.sentence_reconstruction_enabled
            ),
            "sentence_splitter_backend": self.config.sentence_splitter_backend,
            "sentence_splitter_model_name": self.config.sentence_splitter_model_name,
            "sentence_reconstruction_max_gap_seconds": (
                self.config.sentence_reconstruction_max_gap_seconds
            ),
            "sentence_reconstruction_respect_speaker_boundaries": (
                self.config.sentence_reconstruction_respect_speaker_boundaries
            ),
        }

    @staticmethod
    def _build_transcript_digest(chunks: Sequence[TranscriptChunk]) -> str:
        """Return a stable digest for the transcript content being aligned."""

        payload = [
            {
                "start_seconds": float(chunk.start_seconds),
                "end_seconds": float(chunk.end_seconds),
                "text": chunk.text,
                "detected_language": chunk.detected_language,
            }
            for chunk in chunks
        ]
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def _build_aligned_transcript_digest(
        aligned_transcript: AlignedTranscript,
    ) -> str:
        """Return a stable digest for one aligned transcript artifact."""

        payload = [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "detected_language": segment.detected_language,
                "words": [
                    {
                        "text": word.text,
                        "start_seconds": word.start_seconds,
                        "end_seconds": word.end_seconds,
                        "confidence": word.confidence,
                    }
                    for word in segment.words
                ],
            }
            for segment in aligned_transcript.segments
        ]
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def _build_utterance_digest(utterances: Sequence[Utterance]) -> str:
        """Return a stable digest for one utterance sequence."""

        payload = [
            {
                "text": utterance.text,
                "start_seconds": utterance.start_seconds,
                "end_seconds": utterance.end_seconds,
                "speaker_id": utterance.speaker_id,
                "detected_language": utterance.detected_language,
                "aligned_segment_index": utterance.aligned_segment_index,
                "start_word_index": utterance.start_word_index,
                "end_word_index": utterance.end_word_index,
            }
            for utterance in utterances
        ]
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def _build_audio_reference(audio_path: Path) -> dict[str, Any]:
        """Return a stable file-system reference for one normalized audio file."""

        resolved_path = audio_path.expanduser().resolve()
        try:
            stat_result = resolved_path.stat()
        except OSError:
            return {
                "audio_path": str(resolved_path),
                "exists": False,
            }
        return {
            "audio_path": str(resolved_path),
            "exists": True,
            "size_bytes": stat_result.st_size,
            "last_modified_ns": stat_result.st_mtime_ns,
        }

    @staticmethod
    def _build_transcript_text(chunks: Sequence[TranscriptChunk]) -> str:
        """Join chunk texts into a reusable text transcript."""

        return "\n".join(chunk.text for chunk in chunks if chunk.text.strip())

    @staticmethod
    def _coerce_transcript_text(
        transcript_text: Any,
        chunks: Sequence[TranscriptChunk],
    ) -> str:
        """Return a normalized transcript string from cache payload data."""

        if isinstance(transcript_text, str):
            return transcript_text.strip()
        return TranscriptionCacheStore._build_transcript_text(chunks)

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Return a non-negative float from a possibly invalid value."""

        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        """Return a float or `None` when the input is missing."""

        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        """Return an integer or `None` when the input is missing."""

        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        """Return a stripped string or `None` when empty."""

        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _parse_speaker_role(value: Any) -> SpeakerRole | None:
        """Return a speaker role enum when the cached value is valid."""

        if value is None:
            return None
        try:
            return SpeakerRole(str(value))
        except ValueError:
            return None

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
    def _resolve_current_word_ids(
        current_segment: AlignedTranscriptSegment,
        start_word_index: int | None,
        end_word_index: int | None,
    ) -> list[str]:
        """Return the current word identifiers for the cached utterance span."""

        if start_word_index is None or end_word_index is None:
            return []
        if start_word_index < 1 or end_word_index < start_word_index:
            return []
        if end_word_index > len(current_segment.words):
            return []
        return [
            word.word_id
            for word in current_segment.words[start_word_index - 1 : end_word_index]
        ]

    @staticmethod
    def _resolve_current_sentence_utterances(
        *,
        utterances: Sequence[Utterance],
        start_index: int | None,
        end_index: int | None,
    ) -> list[Utterance]:
        """Return the current utterance slice referenced by one cached sentence."""

        if start_index is None or end_index is None:
            return []
        if start_index < 1 or end_index < start_index:
            return []
        if end_index > len(utterances):
            return []
        return list(utterances[start_index - 1 : end_index])

    @staticmethod
    def _resolve_sentence_language(utterances: Sequence[Utterance]) -> str | None:
        """Return the last non-empty language available for one utterance slice."""

        for utterance in reversed(utterances):
            if utterance.detected_language:
                return utterance.detected_language
        return None

    @staticmethod
    def _resolve_sentence_speaker(utterances: Sequence[Utterance]) -> str | None:
        """Return a stable speaker id for one utterance slice when available."""

        observed_speakers = {
            speaker_id
            for utterance in utterances
            if (speaker_id := (utterance.speaker_id or "").strip())
        }
        if len(observed_speakers) == 1:
            return next(iter(observed_speakers))
        return None

    @staticmethod
    def _resolve_sentence_session_time(
        *,
        bound_utterances: Sequence[Utterance],
        attribute_name: str,
    ) -> float | None:
        """Resolve sentence session timing from the linked utterance slice."""

        values = [
            value
            for utterance in bound_utterances
            if (value := getattr(utterance, attribute_name)) is not None
        ]
        if not values:
            return None
        if attribute_name == "session_start_seconds":
            return min(values)
        return max(values)

    @staticmethod
    def _build_fallback_utterance_id(
        audio_source_id: str,
        segment_index: int,
        start_word_index: int | None,
        end_word_index: int | None,
        start_seconds: float,
        end_seconds: float,
        text: str,
    ) -> str:
        """Return a deterministic utterance identifier when one is missing."""

        payload = {
            "segment_index": segment_index,
            "start_word_index": start_word_index,
            "end_word_index": end_word_index,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "text": text,
        }
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ).hexdigest()[:16]
        return f"{audio_source_id}_utterance_{digest}"

    @staticmethod
    def _normalize_utterance_id(
        *,
        audio_source_id: str,
        utterance_id: str | None,
    ) -> str | None:
        """Return a source-scoped utterance identifier for cached artifacts."""

        normalized_utterance_id = str(utterance_id or "").strip()
        if not normalized_utterance_id:
            return None
        scoped_prefix = f"{audio_source_id}_"
        if normalized_utterance_id.startswith(scoped_prefix):
            return normalized_utterance_id
        utterance_marker = "utterance_"
        marker_index = normalized_utterance_id.find(utterance_marker)
        if marker_index >= 0:
            normalized_utterance_id = normalized_utterance_id[marker_index:]
        return f"{scoped_prefix}{normalized_utterance_id}"
