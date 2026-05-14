"""Session-level transcript merge utilities.

The merger stays deliberately conservative. It does not interpret meaning,
split semantics, or infer speaker roles. It only creates a deterministic,
traceable session-level ordering over existing `TranscriptChunk` items.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AudioSource,
    LectureSession,
    MergedTranscript,
    MergedTranscriptUnit,
    TranscriptChunk,
)


@dataclass(slots=True)
class _SourceContext:
    """Compact source metadata used to build merged transcript units."""

    audio_source_id: str
    source_order_index: int
    input_source_id: str | None
    audio_path: Path | None


class TranscriptMerger:
    """Merge transcript chunks into a deterministic session-level transcript.

    Ordering is intentionally simple:
    1. use the audio source order already stored on the lecture session
    2. use chunk start time within each audio source
    3. apply stable tie-breakers so repeated runs produce the same result

    Each merged unit still maps to one original chunk. This keeps the step
    easy to audit and prepares a stable basis for later segmentation.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def merge_session(self, session: LectureSession) -> MergedTranscript:
        """Build a merged transcript from all transcript chunks in a session."""

        source_contexts = self._build_source_contexts(session.audio_sources)
        ordered_chunks = self._order_chunks(
            transcript_chunks=session.transcript_chunks,
            source_contexts=source_contexts,
        )

        unknown_audio_source_ids = sorted(
            {
                chunk.audio_source_id
                for chunk in session.transcript_chunks
                if chunk.audio_source_id not in source_contexts
            },
        )
        merged_units: list[MergedTranscriptUnit] = []
        duplicate_chunk_counter: Counter[str] = Counter()
        duplicate_chunk_ids: set[str] = set()
        overlapping_chunk_ids: list[str] = []
        dropped_chunk_ids: list[str] = []
        last_end_by_source: dict[str, float] = {}

        for source_order_index, chunk in ordered_chunks:
            # Unknown source ids are still preserved in a deterministic order so
            # partially inconsistent metadata does not discard transcript data.
            context = source_contexts.get(
                chunk.audio_source_id,
                _SourceContext(
                    audio_source_id=chunk.audio_source_id,
                    source_order_index=source_order_index,
                    input_source_id=None,
                    audio_path=None,
                ),
            )

            timing = self._resolve_timing(chunk)
            if timing is None:
                # Invalid timing can be dropped only when the configured policy
                # explicitly prioritizes strictness over retention.
                dropped_chunk_ids.append(chunk.chunk_id)
                continue

            start_seconds, end_seconds, timing_notes = timing
            metadata = dict(chunk.metadata)
            if context.audio_path is not None:
                metadata["audio_path"] = str(context.audio_path)
            if timing_notes:
                metadata["timing_notes"] = timing_notes

            duplicate_chunk_counter[chunk.chunk_id] += 1
            occurrence = duplicate_chunk_counter[chunk.chunk_id]
            if occurrence > 1:
                # Duplicate identifiers are preserved rather than rewritten so
                # exported data still reveals the original anomaly.
                duplicate_chunk_ids.add(chunk.chunk_id)
                metadata["duplicate_chunk_id"] = True
                metadata["chunk_occurrence"] = occurrence

            previous_end = last_end_by_source.get(chunk.audio_source_id)
            if previous_end is not None and start_seconds < previous_end:
                # Overlaps are reported as warnings only. The merger does not
                # attempt semantic repair at this stage.
                overlapping_chunk_ids.append(chunk.chunk_id)
                metadata["ordering_warning"] = (
                    "Chunk overlaps a previous chunk in the same audio source. "
                    "The merger keeps source order and start-time order."
                )
            last_end_by_source[chunk.audio_source_id] = max(
                previous_end or 0.0,
                end_seconds,
            )

            merged_units.append(
                MergedTranscriptUnit(
                    unit_id=f"merged_unit_{len(merged_units) + 1:05d}",
                    chunk_id=chunk.chunk_id,
                    chunk_occurrence=occurrence,
                    audio_source_id=chunk.audio_source_id,
                    source_order_index=source_order_index,
                    input_source_id=context.input_source_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    session_start_seconds=chunk.session_start_seconds,
                    session_end_seconds=chunk.session_end_seconds,
                    text=chunk.text,
                    raw_text=chunk.text,
                    detected_language=chunk.detected_language,
                    transcription_confidence=chunk.transcription_confidence,
                    language_confidence=chunk.language_confidence,
                    metadata=metadata,
                ),
            )

        detected_languages = sorted(
            {
                unit.detected_language
                for unit in merged_units
                if unit.detected_language
            },
        )
        source_order = [
            context.audio_source_id
            for context in sorted(
                source_contexts.values(),
                key=lambda value: value.source_order_index,
            )
        ]

        return MergedTranscript(
            session_id=session.session_id,
            units=merged_units,
            full_text=self._build_full_text(merged_units),
            detected_languages=detected_languages,
            metadata={
                "ordering_rule": (
                    "Audio sources keep lecture session order. Chunks are then "
                    "ordered by start_seconds within each source, with end time, "
                    "original chunk position, and chunk_id as stable tie-breakers."
                ),
                "source_order": source_order,
                "dropped_chunk_ids": dropped_chunk_ids,
                "duplicate_chunk_ids": sorted(duplicate_chunk_ids),
                "overlapping_chunk_ids": overlapping_chunk_ids,
                "unknown_audio_source_ids": unknown_audio_source_ids,
                "invalid_timing_policy": self.config.transcript_invalid_timing_policy,
            },
        )

    @staticmethod
    def _build_source_contexts(
        audio_sources: Sequence[AudioSource],
    ) -> dict[str, _SourceContext]:
        """Capture source-level references while preserving session order."""

        contexts: dict[str, _SourceContext] = {}
        for index, source in enumerate(audio_sources, start=1):
            contexts[source.audio_source_id] = _SourceContext(
                audio_source_id=source.audio_source_id,
                source_order_index=index,
                input_source_id=source.input_source_id,
                audio_path=source.audio_path,
            )
        return contexts

    def _order_chunks(
        self,
        transcript_chunks: Sequence[TranscriptChunk],
        source_contexts: dict[str, _SourceContext],
    ) -> list[tuple[int, TranscriptChunk]]:
        """Return chunks in deterministic session order.

        Unknown audio source identifiers are appended after known sources in
        alphabetical order so the result stays stable even when metadata is
        partially inconsistent.
        """

        unknown_source_ids = sorted(
            {
                chunk.audio_source_id
                for chunk in transcript_chunks
                if chunk.audio_source_id not in source_contexts
            },
        )
        unknown_order = {
            audio_source_id: len(source_contexts) + index
            for index, audio_source_id in enumerate(unknown_source_ids, start=1)
        }

        ordered = sorted(
            enumerate(transcript_chunks, start=1),
            key=lambda item: (
                self._source_order_index(
                    audio_source_id=item[1].audio_source_id,
                    source_contexts=source_contexts,
                    unknown_order=unknown_order,
                ),
                self._safe_float(item[1].start_seconds),
                self._safe_float(item[1].end_seconds),
                item[0],
                item[1].chunk_id,
            ),
        )
        return [
            (
                self._source_order_index(
                    audio_source_id=chunk.audio_source_id,
                    source_contexts=source_contexts,
                    unknown_order=unknown_order,
                ),
                chunk,
            )
            for _, chunk in ordered
        ]

    def _resolve_timing(
        self,
        chunk: TranscriptChunk,
    ) -> tuple[float, float, list[str]] | None:
        """Validate timing ranges using a small, readable policy.

        The current implementation supports two policies:
        `clamp` fixes negative values and reversed ranges conservatively,
        while `drop` removes invalid chunks from the merged output.
        """

        policy = self.config.transcript_invalid_timing_policy
        timing_notes: list[str] = []

        try:
            start_seconds = float(chunk.start_seconds)
            end_seconds = float(chunk.end_seconds)
        except (TypeError, ValueError):
            if policy == "drop":
                return None
            timing_notes.append("Non-numeric timing replaced with 0.0.")
            return 0.0, 0.0, timing_notes

        if start_seconds < 0.0:
            if policy == "drop":
                return None
            timing_notes.append("Negative start_seconds clamped to 0.0.")
            start_seconds = 0.0

        if end_seconds < 0.0:
            if policy == "drop":
                return None
            timing_notes.append("Negative end_seconds clamped to start_seconds.")
            end_seconds = start_seconds

        if end_seconds < start_seconds:
            if policy == "drop":
                return None
            timing_notes.append("Reversed timing range clamped to zero-length.")
            end_seconds = start_seconds

        return start_seconds, end_seconds, timing_notes

    @staticmethod
    def _build_full_text(units: Sequence[MergedTranscriptUnit]) -> str:
        """Join non-empty unit text into a session-level transcript string."""

        return "\n".join(unit.text for unit in units if unit.text.strip())

    @staticmethod
    def _source_order_index(
        audio_source_id: str,
        source_contexts: dict[str, _SourceContext],
        unknown_order: dict[str, int],
    ) -> int:
        """Return a deterministic source order index for known or unknown ids."""

        context = source_contexts.get(audio_source_id)
        if context is not None:
            return context.source_order_index
        return unknown_order[audio_source_id]

    @staticmethod
    def _safe_float(value: float | None) -> float:
        """Return a stable float key even when timing metadata is missing."""

        if value is None:
            return float("inf")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("inf")
