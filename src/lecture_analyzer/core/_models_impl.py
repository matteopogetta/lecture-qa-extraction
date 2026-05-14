"""Typed data models used across the lecture processing prototype."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from lecture_analyzer.core.types import MediaType, ProcessingStatus, SpeakerRole


@dataclass(slots=True)
class TimeRange:
    """A bounded time interval with optional source and session anchors."""

    start_seconds: float
    end_seconds: float
    audio_source_id: str | None = None
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the time range."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class InputSource:
    """An original lecture file provided to the system."""

    source_id: str
    original_path: Path
    media_type: MediaType
    order_index: int | None = None
    original_filename: str | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the input source."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class NormalizedAudioAssetMetadata:
    """Minimal metadata persisted for one normalized audio artifact."""

    source_path: str
    source_filename: str
    source_last_modified_ns: int
    output_format: str
    sample_rate: int
    channels: int
    bit_depth: int | None = None
    derived_path: str | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the normalized artifact."""

        return _serialize_value(asdict(self))

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "NormalizedAudioAssetMetadata":
        """Build metadata from a JSON-compatible dictionary."""

        return cls(
            source_path=str(payload["source_path"]),
            source_filename=str(payload["source_filename"]),
            source_last_modified_ns=int(payload["source_last_modified_ns"]),
            output_format=str(payload["output_format"]),
            sample_rate=int(payload["sample_rate"]),
            channels=int(payload["channels"]),
            bit_depth=(
                int(payload["bit_depth"])
                if payload.get("bit_depth") is not None
                else None
            ),
            derived_path=(
                str(payload["derived_path"])
                if payload.get("derived_path") is not None
                else None
            ),
            duration_seconds=(
                float(payload["duration_seconds"])
                if payload.get("duration_seconds") is not None
                else None
            ),
        )


@dataclass(slots=True)
class AudioSource:
    """A normalized audio source used by the internal audio-based pipeline."""

    audio_source_id: str
    input_source_id: str
    audio_path: Path
    normalized_asset: NormalizedAudioAssetMetadata | None = None
    audio_format: str | None = None
    duration_seconds: float | None = None
    order_index: int | None = None
    session_offset_seconds: float | None = None
    extracted_from_video: bool = False
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the audio source."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class TranscriptChunk:
    """A time-bounded transcription unit linked to a normalized audio source."""

    chunk_id: str
    audio_source_id: str
    start_seconds: float
    end_seconds: float
    text: str
    detected_language: str | None = None
    speaker_label: str | None = None
    estimated_speaker_role: SpeakerRole | None = None
    transcription_confidence: float | None = None
    language_confidence: float | None = None
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the transcript chunk."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class AlignedWord:
    """A word-level timing produced by the alignment refinement layer."""

    word_id: str
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    confidence: float | None = None
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the aligned word."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class AlignedTranscriptSegment:
    """One aligned segment linked back to the original ASR chunk."""

    segment_id: str
    audio_source_id: str
    transcript_chunk_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    text: str = ""
    detected_language: str | None = None
    words: list[AlignedWord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the aligned segment."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class AlignedTranscript:
    """A per-source alignment artifact derived from existing ASR chunks."""

    audio_source_id: str
    source_audio_path: Path
    detected_language: str | None = None
    source_chunk_count: int = 0
    segments: list[AlignedTranscriptSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the aligned transcript."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class Utterance:
    """A traceable speech unit derived from aligned words."""

    utterance_id: str
    audio_source_id: str
    text: str
    start_seconds: float
    end_seconds: float
    aligned_segment_id: str
    aligned_segment_index: int
    transcript_chunk_id: str | None = None
    start_word_index: int | None = None
    end_word_index: int | None = None
    source_word_ids: list[str] = field(default_factory=list)
    detected_language: str | None = None
    speaker_id: str | None = None
    speaker_attribution_status: str | None = None
    speaker_confidence_score: float | None = None
    speaker_is_uncertain: bool = False
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the utterance."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class UtteranceCollection:
    """A per-source artifact grouping utterances built from one alignment."""

    audio_source_id: str
    source_audio_path: Path
    detected_language: str | None = None
    source_segment_count: int = 0
    source_word_count: int = 0
    utterances: list[Utterance] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the utterance artifact."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class Sentence:
    """A traceable sentence reconstructed from one or more utterances."""

    sentence_id: str
    audio_source_id: str
    text: str
    start_seconds: float
    end_seconds: float
    source_utterance_ids: list[str] = field(default_factory=list)
    source_utterance_start_index: int | None = None
    source_utterance_end_index: int | None = None
    detected_language: str | None = None
    speaker_id: str | None = None
    speaker_resolution_status: str | None = None
    speaker_confidence_label: str | None = None
    speaker_stability_label: str | None = None
    speaker_assignment_method: str | None = None
    speaker_evidence_summary: str | None = None
    merge_safety_label: str | None = None
    semantic_quality_label: str | None = None
    length_bucket: str | None = None
    duration_bucket: str | None = None
    review_priority: str | None = None
    sentence_review_flags: list[str] = field(default_factory=list)
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the sentence."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class SentenceCollection:
    """A per-source artifact grouping reconstructed sentences."""

    audio_source_id: str
    source_audio_path: Path
    detected_language: str | None = None
    source_utterance_count: int = 0
    sentences: list[Sentence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the sentence artifact."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class DiarizationSegment:
    """A speaker-time segment produced by the diarization layer."""

    diarization_segment_id: str
    audio_source_id: str
    speaker_id: str
    start_seconds: float
    end_seconds: float
    segment_source: str = "regular"
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the diarization segment."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class DiarizationResult:
    """A per-source diarization artifact built from normalized audio."""

    audio_source_id: str
    source_audio_path: Path
    preferred_segment_source: str | None = None
    available_segment_sources: list[str] = field(default_factory=list)
    speaker_ids: list[str] = field(default_factory=list)
    segments: list[DiarizationSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the diarization artifact."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class MergedTranscriptUnit:
    """A conservative session-level transcript unit linked to one chunk."""

    unit_id: str
    chunk_id: str
    chunk_occurrence: int
    audio_source_id: str
    source_order_index: int
    input_source_id: str | None = None
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    text: str = ""
    raw_text: str | None = None
    detected_language: str | None = None
    transcription_confidence: float | None = None
    language_confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the merged transcript unit."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class MergedTranscript:
    """A merged session transcript prepared for later segmentation steps."""

    session_id: str
    units: list[MergedTranscriptUnit] = field(default_factory=list)
    full_text: str = ""
    detected_languages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the merged transcript."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class Segment:
    """A conservative analysis unit built from consecutive sentence spans."""

    segment_id: str
    start_seconds: float
    end_seconds: float
    text: str
    transcript_chunk_ids: list[str]
    merged_transcript_unit_ids: list[str] = field(default_factory=list)
    sentence_ids: list[str] = field(default_factory=list)
    source_utterance_ids: list[str] = field(default_factory=list)
    audio_source_ids: list[str] = field(default_factory=list)
    observed_languages: list[str] = field(default_factory=list)
    estimated_speaker_roles: list[SpeakerRole] = field(default_factory=list)
    raw_speaker_labels: list[str] = field(default_factory=list)
    relevance_score: float | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the segment."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class SpeakerRoleEstimate:
    """An estimated role assigned to a raw speaker label."""

    speaker_label: str
    speaker_role: SpeakerRole
    confidence: float
    source_segment_ids: list[str] = field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the role estimate."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class QAPairCandidate:
    """A candidate question and answer pair extracted from lecture content."""

    qa_candidate_id: str
    question_text: str
    answer_text: str | None
    context_text: str | None = None
    question_unit_ids: list[str] = field(default_factory=list)
    answer_unit_ids: list[str] = field(default_factory=list)
    question_sentence_ids: list[str] = field(default_factory=list)
    answer_sentence_ids: list[str] = field(default_factory=list)
    context_sentence_ids: list[str] = field(default_factory=list)
    question_source_utterance_ids: list[str] = field(default_factory=list)
    answer_source_utterance_ids: list[str] = field(default_factory=list)
    context_source_utterance_ids: list[str] = field(default_factory=list)
    question_segment_id: str | None = None
    answer_segment_id: str | None = None
    context_strategy: str | None = None
    context_confidence: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    question_timing: TimeRange | None = None
    answer_timing: TimeRange | None = None
    question_speaker_role: SpeakerRole = SpeakerRole.UNKNOWN
    answer_speaker_role: SpeakerRole = SpeakerRole.UNKNOWN
    source_segment_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    confidence_label: str = "low"
    confidence_score: float = 0.0
    question_type: str = "unknown"
    didactic_question_score: float | None = None
    answer_is_question: bool = False
    reason_codes: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    extraction_notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the QA candidate."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class PipelineStageTiming:
    """A serializable timing record for one pipeline stage execution."""

    stage_name: str
    status: str = "executed"
    started_at: str | None = None
    finished_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    used_cache: bool = False
    used_existing_artifact: bool = False
    forced_recompute: bool = False
    note: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep legacy and new finish timestamps aligned."""

        if self.finished_at is None and self.ended_at is not None:
            self.finished_at = self.ended_at
        if self.ended_at is None and self.finished_at is not None:
            self.ended_at = self.finished_at

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the stage timing."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class PipelineTimingSummary:
    """Operational summary derived from the collected stage timings."""

    pipeline_execution_mode: str = "normal"
    run_profile_label: str = "cold_run"
    total_duration_seconds: float | None = None
    stage_count: int = 0
    executed_stage_count: int = 0
    completed_stage_count: int = 0
    partial_stage_count: int = 0
    skipped_stage_count: int = 0
    disabled_stage_count: int = 0
    failed_stage_count: int = 0
    reused_cache_stage_count: int = 0
    reused_artifact_stage_count: int = 0
    forced_recompute_stage_count: int = 0
    any_cache_hit: bool = False
    any_artifact_reuse: bool = False
    full_recompute_requested: bool = False
    most_expensive_stage_name: str | None = None
    most_expensive_stage_duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the timing summary."""

        return _serialize_value(asdict(self))


@dataclass(slots=True)
class PipelineTiming:
    """Collected timing data for the current pipeline run."""

    stages: list[PipelineStageTiming] = field(default_factory=list)
    summary: PipelineTimingSummary = field(default_factory=PipelineTimingSummary)

    def refresh_summary(self) -> None:
        """Recompute the operational summary from the current stage list."""

        total_stage = next(
            (
                stage
                for stage in reversed(self.stages)
                if stage.stage_name == "total_pipeline_execution"
            ),
            None,
        )
        measured_stages = [
            stage
            for stage in self.stages
            if stage.stage_name != "total_pipeline_execution"
        ]
        most_expensive_stage = max(
            measured_stages,
            key=lambda stage: stage.duration_seconds or 0.0,
            default=None,
        )
        status_counts = {
            "completed": 0,
            "partial": 0,
            "skipped": 0,
            "disabled": 0,
            "failed": 0,
            "executed": 0,
            "executed_forced": 0,
            "reused_from_cache": 0,
            "reused_from_artifact": 0,
        }
        for stage in measured_stages:
            if stage.status in status_counts:
                status_counts[stage.status] += 1

        any_cache_hit = any(stage.used_cache for stage in measured_stages)
        any_artifact_reuse = any(
            stage.used_existing_artifact for stage in measured_stages
        )
        full_recompute_requested = bool(
            (
                total_stage.metadata.get("full_recompute_requested")
                if total_stage is not None and isinstance(total_stage.metadata, dict)
                else False
            )
            or any(stage.forced_recompute for stage in measured_stages)
        )
        pipeline_execution_mode = (
            str(total_stage.metadata.get("pipeline_execution_mode") or "").strip()
            if total_stage is not None and isinstance(total_stage.metadata, dict)
            else ""
        ) or ("from_scratch" if full_recompute_requested else "normal")
        executed_stage_count = sum(
            1
            for stage in measured_stages
            if stage.status in {"completed", "partial", "executed", "executed_forced"}
        )
        reused_cache_stage_count = sum(
            1
            for stage in measured_stages
            if stage.status == "reused_from_cache" or stage.used_cache
        )
        reused_artifact_stage_count = sum(
            1
            for stage in measured_stages
            if (
                stage.status == "reused_from_artifact"
                or stage.used_existing_artifact
            )
        )
        forced_recompute_stage_count = sum(
            1
            for stage in measured_stages
            if stage.status == "executed_forced" or stage.forced_recompute
        )

        self.summary = PipelineTimingSummary(
            pipeline_execution_mode=pipeline_execution_mode,
            run_profile_label=self._resolve_run_profile_label(
                executed_stage_count=executed_stage_count,
                reused_cache_stage_count=reused_cache_stage_count,
                reused_artifact_stage_count=reused_artifact_stage_count,
                full_recompute_requested=full_recompute_requested,
            ),
            total_duration_seconds=(
                total_stage.duration_seconds
                if total_stage is not None
                else round(
                    sum(stage.duration_seconds or 0.0 for stage in measured_stages),
                    6,
                )
                if measured_stages
                else None
            ),
            stage_count=len(measured_stages),
            executed_stage_count=executed_stage_count,
            completed_stage_count=(
                status_counts["completed"]
                + status_counts["executed"]
                + status_counts["executed_forced"]
                + status_counts["reused_from_cache"]
                + status_counts["reused_from_artifact"]
            ),
            partial_stage_count=status_counts["partial"],
            skipped_stage_count=status_counts["skipped"],
            disabled_stage_count=status_counts["disabled"],
            failed_stage_count=status_counts["failed"],
            reused_cache_stage_count=reused_cache_stage_count,
            reused_artifact_stage_count=reused_artifact_stage_count,
            forced_recompute_stage_count=forced_recompute_stage_count,
            any_cache_hit=any_cache_hit,
            any_artifact_reuse=any_artifact_reuse,
            full_recompute_requested=full_recompute_requested,
            most_expensive_stage_name=(
                most_expensive_stage.stage_name
                if most_expensive_stage is not None
                else None
            ),
            most_expensive_stage_duration_seconds=(
                most_expensive_stage.duration_seconds
                if most_expensive_stage is not None
                else None
            ),
        )

    @staticmethod
    def _resolve_run_profile_label(
        *,
        executed_stage_count: int,
        reused_cache_stage_count: int,
        reused_artifact_stage_count: int,
        full_recompute_requested: bool,
    ) -> str:
        """Return a compact label describing the overall run profile."""

        if full_recompute_requested:
            return "forced_recompute_run"
        if reused_cache_stage_count == 0 and reused_artifact_stage_count == 0:
            return "cold_run"
        if executed_stage_count <= (
            reused_cache_stage_count + reused_artifact_stage_count
        ):
            return "warm_run"
        return "mixed_run"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the timing collection."""

        self.refresh_summary()
        return _serialize_value(asdict(self))


@dataclass(slots=True)
class LectureSession:
    """A lecture session and the artifacts produced by the prototype."""

    session_id: str
    input_sources: list[InputSource] = field(default_factory=list)
    audio_sources: list[AudioSource] = field(default_factory=list)
    language_codes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    transcript_chunks: list[TranscriptChunk] = field(default_factory=list)
    aligned_transcripts: list[AlignedTranscript] = field(default_factory=list)
    diarization_segments: list[DiarizationSegment] = field(default_factory=list)
    utterances: list[Utterance] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    merged_transcript: MergedTranscript | None = None
    transcript_text: str = ""
    segments: list[Segment] = field(default_factory=list)
    speaker_role_estimates: list[SpeakerRoleEstimate] = field(default_factory=list)
    qa_candidates: list[QAPairCandidate] = field(default_factory=list)
    pipeline_timing: PipelineTiming | None = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    schema_version: str = "0.6.0"

    def to_dict(self) -> dict[str, Any]:
        """Return the first JSON-oriented output structure for the session."""

        # Keep the exported JSON grouped by processing layer so external
        # consumers can inspect session metadata, sources, transcript data, and
        # higher-level analysis artifacts independently.
        output = {
            "session_metadata": {
                "schema_version": self.schema_version,
                "session_id": self.session_id,
                "language_codes": self.language_codes,
                "supports_mixed_language": len(self.language_codes) > 1,
                "processing_status": self.processing_status,
                "metadata": self.metadata,
            },
            "input_sources": [source.to_dict() for source in self.input_sources],
            "audio_sources": [source.to_dict() for source in self.audio_sources],
            "transcript": {
                "full_text": self.transcript_text,
                "chunk_count": len(self.transcript_chunks),
                "aligned_source_count": len(self.aligned_transcripts),
                "aligned_segment_count": sum(
                    len(aligned_transcript.segments)
                    for aligned_transcript in self.aligned_transcripts
                ),
                "aligned_word_count": sum(
                    len(segment.words)
                    for aligned_transcript in self.aligned_transcripts
                    for segment in aligned_transcript.segments
                ),
                "diarization_segment_count": len(self.diarization_segments),
                "speaker_count": len(
                    {
                        segment.speaker_id
                        for segment in self.diarization_segments
                        if segment.speaker_id
                    },
                ),
                "utterance_count": len(self.utterances),
                "utterance_with_speaker_count": sum(
                    1 for utterance in self.utterances if utterance.speaker_id is not None
                ),
                "utterance_uncertain_speaker_count": sum(
                    1 for utterance in self.utterances if utterance.speaker_is_uncertain
                ),
                "sentence_count": len(self.sentences),
                "sentence_with_speaker_count": sum(
                    1 for sentence in self.sentences if sentence.speaker_id is not None
                ),
                "merged_unit_count": (
                    len(self.merged_transcript.units)
                    if self.merged_transcript is not None
                    else 0
                ),
                "detected_languages": (
                    self.merged_transcript.detected_languages
                    if self.merged_transcript is not None
                    else sorted(
                        {
                            chunk.detected_language
                            for chunk in self.transcript_chunks
                            if chunk.detected_language
                        },
                    )
                ),
            },
            "merged_transcript": (
                self.merged_transcript.to_dict()
                if self.merged_transcript is not None
                else None
            ),
            "transcript_chunks": [
                chunk.to_dict() for chunk in self.transcript_chunks
            ],
            "aligned_transcripts": [
                aligned_transcript.to_dict()
                for aligned_transcript in self.aligned_transcripts
            ],
            "diarization_segments": [
                segment.to_dict() for segment in self.diarization_segments
            ],
            "utterances": [
                utterance.to_dict() for utterance in self.utterances
            ],
            "sentences": [
                sentence.to_dict() for sentence in self.sentences
            ],
            "segments": [segment.to_dict() for segment in self.segments],
            "speaker_role_estimates": [
                estimate.to_dict() for estimate in self.speaker_role_estimates
            ],
            "qa_candidates": [
                candidate.to_dict() for candidate in self.qa_candidates
            ],
            "pipeline_timing": (
                self.pipeline_timing.to_dict()
                if self.pipeline_timing is not None
                else None
            ),
        }
        return _serialize_value(output)


def _serialize_value(value: Any) -> Any:
    """Convert paths, enums, and dataclasses into JSON-friendly values."""

    # Serialization stays centralized here so each model can delegate JSON
    # shaping without duplicating the same conversion rules.
    if is_dataclass(value):
        return _serialize_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize_value(item) for item in value if item is not None]
    if isinstance(value, dict):
        return {
            key: _serialize_value(item)
            for key, item in value.items()
            if item is not None
        }
    return value
