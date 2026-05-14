"""Transcript segmentation strategies operating primarily on sentences.

Three deterministic strategies are currently available:
- structural: gap-aware heuristic segmentation for local discourse continuity
- windowed: stable time-based segmentation for fast monologic lecture speech
- adaptive: target-based segmentation with local boundary search

The preferred segmentation input is the reconstructed `sentences` layer. When
sentences are unavailable, the segmenter falls back explicitly to merged
transcript units so the pipeline remains backward compatible.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    LectureSession,
    MergedTranscriptUnit,
    Segment,
    Sentence,
    TranscriptChunk,
    Utterance,
)
from lecture_analyzer.core.types import SpeakerRole


@dataclass(slots=True)
class _SegmentationUnit:
    """One normalized unit consumed by the segmenter."""

    unit_id: str
    unit_kind: str
    text: str
    start_seconds: float
    end_seconds: float
    session_start_seconds: float | None = None
    session_end_seconds: float | None = None
    audio_source_id: str = ""
    transcript_chunk_ids: list[str] = field(default_factory=list)
    merged_transcript_unit_ids: list[str] = field(default_factory=list)
    sentence_ids: list[str] = field(default_factory=list)
    source_utterance_ids: list[str] = field(default_factory=list)
    detected_language: str | None = None
    estimated_speaker_roles: list[SpeakerRole] = field(default_factory=list)
    raw_speaker_labels: list[str] = field(default_factory=list)
    speaker_ids: list[str] = field(default_factory=list)
    speaker_uncertainty_count: int = 0
    speaker_unassigned_count: int = 0


@dataclass(slots=True)
class _SegmentDraft:
    """Mutable segment state used while scanning segmentation units."""

    start_seconds: float
    end_seconds: float
    text_parts: list[str] = field(default_factory=list)
    transcript_chunk_ids: list[str] = field(default_factory=list)
    merged_transcript_unit_ids: list[str] = field(default_factory=list)
    sentence_ids: list[str] = field(default_factory=list)
    source_utterance_ids: list[str] = field(default_factory=list)
    audio_source_ids: list[str] = field(default_factory=list)
    observed_languages: list[str] = field(default_factory=list)
    estimated_speaker_roles: list[SpeakerRole] = field(default_factory=list)
    raw_speaker_labels: list[str] = field(default_factory=list)
    observed_speaker_ids: list[str] = field(default_factory=list)
    input_unit_count: int = 0
    char_count: int = 0
    speaker_uncertainty_count: int = 0
    speaker_unassigned_count: int = 0
    closing_reason: str = ""
    boundary_type: str = "terminal"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _BoundaryDecision:
    """Deterministic boundary decision for the next segmentation unit."""

    should_close: bool
    reason: str | None = None
    boundary_type: str = "none"
    gap_seconds: float | None = None
    overridden_soft_boundary: bool = False
    override_note: str | None = None
    continuity_signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _AdaptiveBoundaryCandidate:
    """A scored boundary candidate near the adaptive target zone."""

    end_index: int
    score: float
    signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _AdaptiveSelection:
    """The selected adaptive boundary and the metadata behind it."""

    end_index: int
    closing_reason: str
    boundary_type: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _SegmentationInput:
    """The normalized input stream used for one segmentation run."""

    units: list[_SegmentationUnit]
    input_layer: str
    reason: str | None = None


class TranscriptSegmenter:
    """Segment reconstructed sentences using the configured deterministic mode."""

    _WORD_RE = re.compile(r"\b[\w']+\b", flags=re.UNICODE)
    _STRONG_ENDING_PUNCTUATION = (".", "?", "!")
    _WEAK_ENDING_PUNCTUATION = (",", ";", ":", "-", "(", "[", "{", "/")

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def resolved_mode(self, mode: str | None = None) -> str:
        """Return the active segmentation mode with a safe structural fallback."""

        candidate = self.config.segmentation_mode if mode is None else mode.strip().lower()
        if candidate in {"structural", "windowed", "adaptive"}:
            return candidate
        return "structural"

    def segment_session(
        self,
        session: LectureSession,
        mode: str | None = None,
    ) -> list[Segment]:
        """Build session segments using the configured segmentation strategy."""

        resolved_mode = self.resolved_mode(mode)
        segmentation_input = self._build_segmentation_input(session)
        session.metadata["segmentation_input_layer"] = segmentation_input.input_layer
        session.metadata["segmentation_status"] = "completed"
        if segmentation_input.reason is not None:
            session.metadata["segmentation_reason"] = segmentation_input.reason
        else:
            session.metadata.pop("segmentation_reason", None)

        filtered_units = self._filter_units(segmentation_input.units)
        if not filtered_units:
            session.metadata["segmentation_status"] = "skipped"
            if segmentation_input.reason is None:
                session.metadata["segmentation_reason"] = (
                    "segmentation_input_units_unavailable"
                )
            self._store_segmentation_debug(
                session,
                [],
                mode=resolved_mode,
                input_layer=segmentation_input.input_layer,
            )
            return []

        if resolved_mode == "windowed":
            segments = self._segment_windowed(filtered_units=filtered_units)
        elif resolved_mode == "adaptive":
            segments = self._segment_adaptive(filtered_units=filtered_units)
        else:
            segments = self._segment_structural(filtered_units=filtered_units)

        session.metadata["segment_count"] = len(segments)
        self._store_segmentation_debug(
            session,
            segments,
            mode=resolved_mode,
            input_layer=segmentation_input.input_layer,
        )
        return segments

    def _build_segmentation_input(self, session: LectureSession) -> _SegmentationInput:
        """Return the sentence-first segmentation input for the current session."""

        if session.sentences:
            return _SegmentationInput(
                units=self._build_sentence_units(session),
                input_layer="sentences",
            )

        merged_units = (
            list(session.merged_transcript.units)
            if session.merged_transcript is not None
            else []
        )
        if merged_units:
            return _SegmentationInput(
                units=self._build_merged_units(session, merged_units),
                input_layer="merged_transcript_fallback",
                reason="sentences_unavailable_using_merged_transcript",
            )

        return _SegmentationInput(
            units=[],
            input_layer="unavailable",
            reason="sentences_and_merged_transcript_unavailable",
        )

    def _build_sentence_units(
        self,
        session: LectureSession,
    ) -> list[_SegmentationUnit]:
        """Normalize sentences into segmenter-friendly units."""

        utterance_lookup = {
            utterance.utterance_id: utterance
            for utterance in session.utterances
        }
        chunks_by_id = self._build_chunks_by_id(session.transcript_chunks)
        merged_unit_ids_by_chunk_id = self._build_merged_unit_ids_by_chunk_id(
            session.merged_transcript.units if session.merged_transcript is not None else [],
        )

        units: list[_SegmentationUnit] = []
        for sentence in self._ordered_sentences(session.sentences):
            source_utterances = [
                utterance_lookup[utterance_id]
                for utterance_id in sentence.source_utterance_ids
                if utterance_id in utterance_lookup
            ]
            transcript_chunk_ids = self._ordered_unique(
                utterance.transcript_chunk_id
                for utterance in source_utterances
                if utterance.transcript_chunk_id is not None
            )
            source_chunks = [
                chunk
                for chunk_id in transcript_chunk_ids
                for chunk in chunks_by_id.get(chunk_id, [])
            ]
            merged_transcript_unit_ids = self._ordered_unique(
                merged_unit_id
                for chunk_id in transcript_chunk_ids
                for merged_unit_id in merged_unit_ids_by_chunk_id.get(chunk_id, [])
            )
            observed_speaker_ids = self._ordered_unique(
                speaker_id
                for speaker_id in (
                    sentence.speaker_id,
                    *(
                        utterance.speaker_id
                        for utterance in source_utterances
                    ),
                )
                if speaker_id is not None
            )

            units.append(
                _SegmentationUnit(
                    unit_id=sentence.sentence_id,
                    unit_kind="sentence",
                    text=sentence.text,
                    start_seconds=sentence.start_seconds,
                    end_seconds=sentence.end_seconds,
                    session_start_seconds=sentence.session_start_seconds,
                    session_end_seconds=sentence.session_end_seconds,
                    audio_source_id=sentence.audio_source_id,
                    transcript_chunk_ids=transcript_chunk_ids,
                    merged_transcript_unit_ids=merged_transcript_unit_ids,
                    sentence_ids=[sentence.sentence_id],
                    source_utterance_ids=list(sentence.source_utterance_ids),
                    detected_language=sentence.detected_language,
                    estimated_speaker_roles=self._ordered_unique(
                        chunk.estimated_speaker_role
                        for chunk in source_chunks
                        if chunk.estimated_speaker_role is not None
                    ),
                    raw_speaker_labels=self._ordered_unique(
                        chunk.speaker_label
                        for chunk in source_chunks
                        if chunk.speaker_label is not None
                    ),
                    speaker_ids=observed_speaker_ids,
                    speaker_uncertainty_count=sum(
                        1 for utterance in source_utterances if utterance.speaker_is_uncertain
                    ),
                    speaker_unassigned_count=sum(
                        1 for utterance in source_utterances if utterance.speaker_id is None
                    ),
                ),
            )

        return units

    def _build_merged_units(
        self,
        session: LectureSession,
        merged_units: Sequence[MergedTranscriptUnit],
    ) -> list[_SegmentationUnit]:
        """Normalize merged transcript units for the explicit fallback path."""

        chunk_lookup = self._build_chunk_lookup(session)
        units: list[_SegmentationUnit] = []

        for merged_unit in merged_units:
            chunk = chunk_lookup.get((merged_unit.chunk_id, merged_unit.chunk_occurrence))
            units.append(
                _SegmentationUnit(
                    unit_id=merged_unit.unit_id,
                    unit_kind="merged_transcript_unit",
                    text=merged_unit.text,
                    start_seconds=merged_unit.start_seconds,
                    end_seconds=merged_unit.end_seconds,
                    session_start_seconds=merged_unit.session_start_seconds,
                    session_end_seconds=merged_unit.session_end_seconds,
                    audio_source_id=merged_unit.audio_source_id,
                    transcript_chunk_ids=[merged_unit.chunk_id],
                    merged_transcript_unit_ids=[merged_unit.unit_id],
                    sentence_ids=[],
                    source_utterance_ids=[],
                    detected_language=merged_unit.detected_language,
                    estimated_speaker_roles=(
                        [chunk.estimated_speaker_role]
                        if chunk is not None and chunk.estimated_speaker_role is not None
                        else []
                    ),
                    raw_speaker_labels=(
                        [chunk.speaker_label]
                        if chunk is not None and chunk.speaker_label is not None
                        else []
                    ),
                ),
            )
        return units

    def _segment_structural(
        self,
        filtered_units: Sequence[_SegmentationUnit],
    ) -> list[Segment]:
        """Build segments using heuristic structural boundaries."""

        drafts: list[_SegmentDraft] = []
        current_draft: _SegmentDraft | None = None

        for unit in filtered_units:
            if current_draft is None:
                current_draft = self._start_draft(unit)
                continue

            boundary = self._structural_boundary_decision(
                current_draft=current_draft,
                next_unit=unit,
            )
            if boundary.overridden_soft_boundary:
                self._record_soft_boundary_override(current_draft, boundary)

            if boundary.should_close:
                current_draft.closing_reason = boundary.reason or "unknown"
                current_draft.boundary_type = boundary.boundary_type
                if boundary.gap_seconds is not None:
                    current_draft.metadata["closing_gap_seconds"] = round(
                        boundary.gap_seconds,
                        3,
                    )
                drafts.append(current_draft)
                current_draft = self._start_draft(unit)
                continue

            self._append_unit(current_draft, unit)

        if current_draft is not None:
            current_draft.closing_reason = "end_of_transcript"
            current_draft.boundary_type = "terminal"
            drafts.append(current_draft)

        if not self.config.segmentation_keep_singleton_short_segments:
            drafts = self._merge_short_singletons(drafts)

        return [
            self._build_segment(draft, index=index, mode="structural")
            for index, draft in enumerate(drafts, start=1)
        ]

    def _segment_windowed(
        self,
        filtered_units: Sequence[_SegmentationUnit],
    ) -> list[Segment]:
        """Build stable time-window segments over consecutive segmentation units."""

        drafts: list[_SegmentDraft] = []
        index = 0
        window_index = 1
        step_seconds = (
            self.config.segmentation_window_seconds
            - self.config.segmentation_window_overlap_seconds
        )

        while index < len(filtered_units):
            anchor_start_seconds, _ = self._unit_time_range(filtered_units[index])
            target_end_seconds = (
                anchor_start_seconds + self.config.segmentation_window_seconds
            )

            draft = self._start_draft(filtered_units[index])
            next_index = index + 1

            while next_index < len(filtered_units):
                next_unit = filtered_units[next_index]
                next_start_seconds, _ = self._unit_time_range(next_unit)
                inside_window = next_start_seconds < target_end_seconds
                if (
                    inside_window
                    or draft.input_unit_count < self.config.segmentation_window_min_units
                ):
                    self._append_unit(draft, next_unit)
                    next_index += 1
                    continue
                break

            draft.boundary_type = "window"
            draft.closing_reason = (
                "window_end" if next_index < len(filtered_units) else "end_of_transcript"
            )
            draft.metadata["window_index"] = window_index
            draft.metadata["window_target_start_seconds"] = round(anchor_start_seconds, 3)
            draft.metadata["window_target_end_seconds"] = round(target_end_seconds, 3)
            draft.metadata["window_overlap_seconds"] = round(
                self.config.segmentation_window_overlap_seconds,
                3,
            )
            drafts.append(draft)

            if next_index >= len(filtered_units):
                break

            next_window_start_seconds = anchor_start_seconds + step_seconds
            index = self._find_next_window_start_index(
                units=filtered_units,
                current_index=index,
                consumed_until_index=next_index,
                next_window_start_seconds=next_window_start_seconds,
            )
            window_index += 1

        return [
            self._build_segment(draft, index=index, mode="windowed")
            for index, draft in enumerate(drafts, start=1)
        ]

    def _segment_adaptive(
        self,
        filtered_units: Sequence[_SegmentationUnit],
    ) -> list[Segment]:
        """Build target-based segments with a local boundary search."""

        drafts: list[_SegmentDraft] = []
        start_index = 0

        while start_index < len(filtered_units):
            selection = self._select_adaptive_boundary(
                units=filtered_units,
                start_index=start_index,
            )
            selected_units = filtered_units[start_index : selection.end_index + 1]
            draft = self._build_draft_from_units(units=selected_units)
            draft.closing_reason = selection.closing_reason
            draft.boundary_type = selection.boundary_type
            draft.metadata.update(selection.metadata)
            drafts.append(draft)
            start_index = selection.end_index + 1

        return [
            self._build_segment(draft, index=index, mode="adaptive")
            for index, draft in enumerate(drafts, start=1)
        ]

    def _filter_units(
        self,
        units: Sequence[_SegmentationUnit],
    ) -> list[_SegmentationUnit]:
        """Drop empty segmentation units when configured."""

        if not self.config.segmentation_drop_empty_units:
            return list(units)
        return [unit for unit in units if unit.text.strip()]

    def _start_draft(self, unit: _SegmentationUnit) -> _SegmentDraft:
        """Create a new draft segment starting from one segmentation unit."""

        start_seconds, end_seconds = self._unit_time_range(unit)
        draft = _SegmentDraft(start_seconds=start_seconds, end_seconds=end_seconds)
        self._append_unit(draft, unit)
        return draft

    def _build_draft_from_units(
        self,
        units: Sequence[_SegmentationUnit],
    ) -> _SegmentDraft:
        """Create a draft by appending a consecutive slice of segmentation units."""

        if not units:
            raise ValueError("Cannot build a segment draft from an empty unit slice.")

        draft = self._start_draft(units[0])
        for unit in units[1:]:
            self._append_unit(draft, unit)
        return draft

    def _append_unit(self, draft: _SegmentDraft, unit: _SegmentationUnit) -> None:
        """Append one segmentation unit to an open draft segment."""

        draft.text_parts.append(unit.text)
        draft.transcript_chunk_ids = self._ordered_union(
            draft.transcript_chunk_ids,
            unit.transcript_chunk_ids,
        )
        draft.merged_transcript_unit_ids = self._ordered_union(
            draft.merged_transcript_unit_ids,
            unit.merged_transcript_unit_ids,
        )
        draft.sentence_ids = self._ordered_union(draft.sentence_ids, unit.sentence_ids)
        draft.source_utterance_ids = self._ordered_union(
            draft.source_utterance_ids,
            unit.source_utterance_ids,
        )
        self._append_unique(draft.audio_source_ids, unit.audio_source_id)
        if unit.detected_language is not None:
            self._append_unique(draft.observed_languages, unit.detected_language)
        for role in unit.estimated_speaker_roles:
            self._append_unique(draft.estimated_speaker_roles, role)
        for speaker_label in unit.raw_speaker_labels:
            self._append_unique(draft.raw_speaker_labels, speaker_label)
        for speaker_id in unit.speaker_ids:
            self._append_unique(draft.observed_speaker_ids, speaker_id)

        _, unit_end_seconds = self._unit_time_range(unit)
        draft.end_seconds = max(draft.end_seconds, unit_end_seconds)
        draft.input_unit_count += 1
        draft.char_count = self._text_length(draft.text_parts)
        draft.speaker_uncertainty_count += unit.speaker_uncertainty_count
        draft.speaker_unassigned_count += unit.speaker_unassigned_count

    def _structural_boundary_decision(
        self,
        current_draft: _SegmentDraft,
        next_unit: _SegmentationUnit,
    ) -> _BoundaryDecision:
        """Return the deterministic structural boundary decision."""

        next_start_seconds, next_end_seconds = self._unit_time_range(next_unit)

        hard_boundary = self._hard_boundary_decision(
            current_draft=current_draft,
            next_unit=next_unit,
            next_end_seconds=next_end_seconds,
        )
        if hard_boundary is not None:
            return hard_boundary

        gap_seconds = self._gap_seconds(
            current_draft=current_draft,
            next_unit=next_unit,
            next_start_seconds=next_start_seconds,
        )
        if gap_seconds is None or gap_seconds <= self.config.segmentation_max_gap_seconds:
            return _BoundaryDecision(should_close=False)

        continuity_signals = self._continuity_signals(
            current_draft=current_draft,
            next_unit=next_unit,
        )
        if (
            self.config.segmentation_gap_override_for_incomplete_text
            and gap_seconds <= self.config.segmentation_soft_max_gap_seconds
            and continuity_signals
        ):
            return _BoundaryDecision(
                should_close=False,
                boundary_type="soft",
                gap_seconds=gap_seconds,
                overridden_soft_boundary=True,
                override_note=(
                    "Soft gap boundary overridden because local phrasing "
                    "appears incomplete."
                ),
                continuity_signals=continuity_signals,
            )

        return _BoundaryDecision(
            should_close=True,
            reason="max_gap",
            boundary_type="soft",
            gap_seconds=gap_seconds,
            continuity_signals=continuity_signals,
        )

    def _hard_boundary_decision(
        self,
        current_draft: _SegmentDraft,
        next_unit: _SegmentationUnit,
        next_end_seconds: float,
    ) -> _BoundaryDecision | None:
        """Return a hard boundary decision when a closing rule must apply."""

        previous_unit_source_id = current_draft.audio_source_ids[-1]
        if self._should_split_on_source_change(
            previous_unit_source_id=previous_unit_source_id,
            next_unit=next_unit,
        ):
            return _BoundaryDecision(
                should_close=True,
                reason="source_change",
                boundary_type="hard",
            )

        candidate_duration = next_end_seconds - current_draft.start_seconds
        if candidate_duration > self.config.segmentation_max_duration_seconds:
            return _BoundaryDecision(
                should_close=True,
                reason="max_duration",
                boundary_type="hard",
            )

        candidate_text_length = current_draft.char_count
        if current_draft.text_parts and next_unit.text:
            candidate_text_length += 1
        candidate_text_length += len(next_unit.text)
        if candidate_text_length > self.config.segmentation_max_text_length:
            return _BoundaryDecision(
                should_close=True,
                reason="max_text_length",
                boundary_type="hard",
            )

        return None

    def _select_adaptive_boundary(
        self,
        units: Sequence[_SegmentationUnit],
        start_index: int,
    ) -> _AdaptiveSelection:
        """Return the chosen adaptive closing point for one segment slice."""

        current_end = start_index

        while current_end < len(units):
            if (
                current_end > start_index
                and self._should_split_on_source_change(
                    previous_unit_source_id=units[current_end - 1].audio_source_id,
                    next_unit=units[current_end],
                )
            ):
                return _AdaptiveSelection(
                    end_index=current_end - 1,
                    closing_reason="source_change",
                    boundary_type="hard",
                    metadata={"adaptive_boundary_source": "source_change"},
                )

            duration_seconds = self._slice_duration_seconds(units, start_index, current_end)
            text_length = self._slice_text_length(units, start_index, current_end)
            target_reached = (
                duration_seconds >= self.config.segmentation_adaptive_target_duration_seconds
                or text_length >= self.config.segmentation_adaptive_target_text_length
            )
            hard_limit_reason = self._adaptive_hard_limit_reason(
                duration_seconds=duration_seconds,
                text_length=text_length,
            )

            if target_reached or hard_limit_reason is not None:
                best_candidate = self._best_adaptive_candidate(
                    units=units,
                    start_index=start_index,
                    current_end=current_end,
                )
                if (
                    best_candidate is not None
                    and best_candidate.score
                    >= self.config.segmentation_adaptive_min_boundary_score
                ):
                    metadata = {
                        "adaptive_boundary_score": round(best_candidate.score, 3),
                        "adaptive_boundary_signals": best_candidate.signals,
                        "adaptive_candidate_count": min(
                            self.config.segmentation_adaptive_boundary_lookback_units,
                            current_end - start_index + 1,
                        ),
                        "adaptive_boundary_source": "score",
                    }
                    if hard_limit_reason is not None:
                        metadata["adaptive_hard_limit_reached"] = hard_limit_reason
                    return _AdaptiveSelection(
                        end_index=best_candidate.end_index,
                        closing_reason="adaptive_boundary",
                        boundary_type="adaptive",
                        metadata=metadata,
                    )

                if hard_limit_reason is not None:
                    fallback_index = best_candidate.end_index if best_candidate is not None else current_end
                    metadata = {
                        "adaptive_boundary_source": "hard_limit_fallback",
                        "adaptive_hard_limit_reached": hard_limit_reason,
                    }
                    if best_candidate is not None:
                        metadata["adaptive_boundary_score"] = round(best_candidate.score, 3)
                        metadata["adaptive_boundary_signals"] = best_candidate.signals
                    return _AdaptiveSelection(
                        end_index=fallback_index,
                        closing_reason=f"adaptive_hard_max_{hard_limit_reason}",
                        boundary_type="hard",
                        metadata=metadata,
                    )

            current_end += 1

        return _AdaptiveSelection(
            end_index=len(units) - 1,
            closing_reason="end_of_transcript",
            boundary_type="terminal",
            metadata={"adaptive_boundary_source": "transcript_end"},
        )

    def _best_adaptive_candidate(
        self,
        units: Sequence[_SegmentationUnit],
        start_index: int,
        current_end: int,
    ) -> _AdaptiveBoundaryCandidate | None:
        """Return the best scored boundary candidate near the current end."""

        candidate_start = max(
            start_index,
            current_end - self.config.segmentation_adaptive_boundary_lookback_units + 1,
        )
        best_candidate: _AdaptiveBoundaryCandidate | None = None

        for candidate_end in range(candidate_start, current_end + 1):
            candidate = self._adaptive_candidate_score(
                units=units,
                start_index=start_index,
                candidate_end=candidate_end,
            )
            if best_candidate is None or candidate.score > best_candidate.score:
                best_candidate = candidate
        return best_candidate

    def _adaptive_candidate_score(
        self,
        units: Sequence[_SegmentationUnit],
        start_index: int,
        candidate_end: int,
    ) -> _AdaptiveBoundaryCandidate:
        """Score one candidate boundary between recent adaptive units."""

        segment_text = self._joined_text(
            unit.text for unit in units[start_index : candidate_end + 1]
        )
        trailing_unit = units[candidate_end]
        next_unit = units[candidate_end + 1] if candidate_end + 1 < len(units) else None
        score = 0.0
        signals: list[str] = []

        if segment_text.endswith(self._STRONG_ENDING_PUNCTUATION):
            score += 2.0
            signals.append("strong_ending_punctuation")
        elif segment_text.endswith(self._WEAK_ENDING_PUNCTUATION):
            score += 0.5
            signals.append("weak_ending_punctuation")

        if not self._is_weak_standalone_text(segment_text):
            score += 0.75
            signals.append("standalone_text_is_strong_enough")
        else:
            score -= 0.5
            signals.append("standalone_text_is_short")

        if self._looks_complete(segment_text):
            score += 1.0
            signals.append("text_looks_complete")
        else:
            score -= 1.0
            signals.append("text_looks_incomplete")

        if self._ends_with_continuation_marker(segment_text):
            score -= 1.25
            signals.append("ends_with_continuation_marker")

        if self._is_weak_standalone_text(trailing_unit.text):
            score -= 0.5
            signals.append("trailing_unit_is_short")

        if next_unit is not None:
            next_text = next_unit.text.strip()
            gap_seconds = self._gap_between_units(trailing_unit, next_unit)

            if gap_seconds is not None and gap_seconds >= 0.35:
                score += 0.5
                signals.append("local_pause_before_next_unit")
            if gap_seconds is not None and gap_seconds <= 0.15:
                score -= 0.25
                signals.append("very_short_pause_before_next_unit")
            if self._starts_with_transition_marker(next_text):
                score += 1.0
                signals.append("next_unit_starts_with_transition_marker")
            if self._starts_with_continuation_marker(next_text):
                score -= 1.0
                signals.append("next_unit_starts_with_continuation_marker")
            if self._starts_with_lowercase(next_text):
                score -= 0.35
                signals.append("next_unit_starts_lowercase")

        return _AdaptiveBoundaryCandidate(
            end_index=candidate_end,
            score=score,
            signals=signals,
        )

    def _adaptive_hard_limit_reason(
        self,
        duration_seconds: float,
        text_length: int,
    ) -> str | None:
        """Return the adaptive hard-limit reason when a maximum is reached."""

        duration_limit = (
            duration_seconds >= self.config.segmentation_adaptive_max_duration_seconds
        )
        text_limit = text_length >= self.config.segmentation_adaptive_max_text_length
        if duration_limit and text_limit:
            return "duration_and_text"
        if duration_limit:
            return "duration"
        if text_limit:
            return "text"
        return None

    def _continuity_signals(
        self,
        current_draft: _SegmentDraft,
        next_unit: _SegmentationUnit,
    ) -> list[str]:
        """Return signals suggesting that local phrasing likely continues."""

        if not self.config.segmentation_incomplete_text_continuation_enabled:
            return []

        incomplete_signals: list[str] = []
        support_signals: list[str] = []
        current_text = self._joined_text(current_draft.text_parts)
        next_text = next_unit.text.strip()

        if self._looks_incomplete(current_text):
            incomplete_signals.append("current_text_looks_incomplete")
        if self._ends_with_continuation_marker(current_text):
            incomplete_signals.append("current_text_ends_with_continuation_marker")
        if self._is_weak_standalone_text(current_text):
            support_signals.append("current_text_is_short")
        if self._is_weak_standalone_text(next_text):
            support_signals.append("next_unit_is_short")
        if self._starts_with_lowercase(next_text):
            support_signals.append("next_unit_starts_lowercase")
        if self._starts_with_continuation_marker(next_text):
            support_signals.append("next_unit_starts_with_continuation_marker")

        if not incomplete_signals:
            return []
        signals = incomplete_signals + support_signals
        if len(signals) < 2:
            return []
        return signals

    def _should_split_on_source_change(
        self,
        previous_unit_source_id: str,
        next_unit: _SegmentationUnit,
    ) -> bool:
        """Return whether a source boundary should force a new segment."""

        if previous_unit_source_id == next_unit.audio_source_id:
            return False
        if self.config.segmentation_split_on_source_change:
            return True
        return (
            next_unit.session_start_seconds is None
            or next_unit.session_end_seconds is None
        )

    def _gap_seconds(
        self,
        current_draft: _SegmentDraft,
        next_unit: _SegmentationUnit,
        next_start_seconds: float,
    ) -> float | None:
        """Return the time gap before a unit when timing anchors are comparable."""

        if next_unit.session_start_seconds is not None:
            return next_start_seconds - current_draft.end_seconds
        if (
            current_draft.audio_source_ids
            and current_draft.audio_source_ids[-1] == next_unit.audio_source_id
        ):
            return next_start_seconds - current_draft.end_seconds
        return None

    def _gap_between_units(
        self,
        previous_unit: _SegmentationUnit,
        next_unit: _SegmentationUnit,
    ) -> float | None:
        """Return the comparable time gap between two segmentation units."""

        _, previous_end_seconds = self._unit_time_range(previous_unit)
        next_start_seconds, _ = self._unit_time_range(next_unit)
        if (
            previous_unit.session_end_seconds is not None
            and next_unit.session_start_seconds is not None
        ):
            return next_start_seconds - previous_end_seconds
        if previous_unit.audio_source_id == next_unit.audio_source_id:
            return next_start_seconds - previous_end_seconds
        return None

    def _find_next_window_start_index(
        self,
        units: Sequence[_SegmentationUnit],
        current_index: int,
        consumed_until_index: int,
        next_window_start_seconds: float,
    ) -> int:
        """Return the next window start index with optional time overlap."""

        for index in range(current_index + 1, len(units)):
            unit_start_seconds, _ = self._unit_time_range(units[index])
            if unit_start_seconds >= next_window_start_seconds:
                return index
        if consumed_until_index > current_index:
            return consumed_until_index
        return min(current_index + 1, len(units))

    def _record_soft_boundary_override(
        self,
        draft: _SegmentDraft,
        boundary: _BoundaryDecision,
    ) -> None:
        """Persist lightweight debugging metadata for a soft-boundary override."""

        override_notes = draft.metadata.setdefault("soft_boundary_overrides", [])
        if not isinstance(override_notes, list):
            override_notes = []
            draft.metadata["soft_boundary_overrides"] = override_notes

        note = {
            "reason": "max_gap",
            "note": boundary.override_note,
            "gap_seconds": round(boundary.gap_seconds or 0.0, 3),
            "signals": boundary.continuity_signals,
        }
        override_notes.append(note)
        draft.metadata["soft_boundary_override_count"] = len(override_notes)

    def _merge_short_singletons(
        self,
        drafts: Sequence[_SegmentDraft],
    ) -> list[_SegmentDraft]:
        """Merge very short singleton drafts into adjacent drafts when possible."""

        merged_drafts: list[_SegmentDraft] = []
        pending_drafts = list(drafts)

        for index, draft in enumerate(pending_drafts):
            if not self._is_short_singleton(draft):
                merged_drafts.append(draft)
                continue

            if merged_drafts:
                self._merge_draft_into_previous(
                    previous_draft=merged_drafts[-1],
                    draft=draft,
                )
                continue

            if index + 1 < len(pending_drafts):
                self._prepend_draft_into_next(
                    draft=draft,
                    next_draft=pending_drafts[index + 1],
                )
                continue

            merged_drafts.append(draft)

        return merged_drafts

    def _build_segment(
        self,
        draft: _SegmentDraft,
        index: int,
        mode: str,
    ) -> Segment:
        """Convert an internal draft into the public `Segment` model."""

        metadata = dict(draft.metadata)
        metadata["segmentation_mode"] = mode
        metadata["input_unit_count"] = draft.input_unit_count
        metadata["sentence_count"] = len(draft.sentence_ids)
        metadata["source_utterance_count"] = len(draft.source_utterance_ids)
        metadata["source_audio_ids"] = draft.audio_source_ids
        metadata["observed_languages"] = draft.observed_languages
        metadata["closing_reason"] = draft.closing_reason
        metadata["closing_boundary_type"] = draft.boundary_type
        metadata["speaker_ids"] = draft.observed_speaker_ids
        metadata["speaker_uncertain_utterance_count"] = draft.speaker_uncertainty_count
        metadata["speaker_unassigned_utterance_count"] = draft.speaker_unassigned_count

        return Segment(
            segment_id=f"segment_{index:04d}",
            start_seconds=draft.start_seconds,
            end_seconds=draft.end_seconds,
            text=self._joined_text(draft.text_parts),
            transcript_chunk_ids=draft.transcript_chunk_ids,
            merged_transcript_unit_ids=draft.merged_transcript_unit_ids,
            sentence_ids=draft.sentence_ids,
            source_utterance_ids=draft.source_utterance_ids,
            audio_source_ids=draft.audio_source_ids,
            observed_languages=draft.observed_languages,
            estimated_speaker_roles=draft.estimated_speaker_roles,
            raw_speaker_labels=draft.raw_speaker_labels,
            metadata=metadata,
        )

    def _build_chunk_lookup(
        self,
        session: LectureSession,
    ) -> dict[tuple[str, int], TranscriptChunk]:
        """Map merged unit references back to original transcript chunks."""

        source_order = {
            source.audio_source_id: index
            for index, source in enumerate(session.audio_sources, start=1)
        }
        unknown_audio_source_ids = sorted(
            {
                chunk.audio_source_id
                for chunk in session.transcript_chunks
                if chunk.audio_source_id not in source_order
            },
        )
        unknown_source_order = {
            audio_source_id: len(source_order) + index
            for index, audio_source_id in enumerate(unknown_audio_source_ids, start=1)
        }

        ordered_chunks = sorted(
            enumerate(session.transcript_chunks, start=1),
            key=lambda item: (
                self._source_order_index(
                    audio_source_id=item[1].audio_source_id,
                    source_order=source_order,
                    unknown_source_order=unknown_source_order,
                ),
                self._safe_float(item[1].start_seconds),
                self._safe_float(item[1].end_seconds),
                item[0],
                item[1].chunk_id,
            ),
        )

        chunk_lookup: dict[tuple[str, int], TranscriptChunk] = {}
        occurrence_counter: Counter[str] = Counter()
        for _, chunk in ordered_chunks:
            occurrence_counter[chunk.chunk_id] += 1
            chunk_lookup[(chunk.chunk_id, occurrence_counter[chunk.chunk_id])] = chunk
        return chunk_lookup

    def _store_segmentation_debug(
        self,
        session: LectureSession,
        segments: Sequence[Segment],
        mode: str,
        input_layer: str,
    ) -> None:
        """Persist lightweight session-level debug statistics."""

        closing_reason_counts = Counter(
            str(segment.metadata.get("closing_reason", "unknown"))
            for segment in segments
        )
        total_units = sum(int(segment.metadata.get("input_unit_count", 0)) for segment in segments)
        total_sentences = sum(len(segment.sentence_ids) for segment in segments)
        total_text_length = sum(len(segment.text) for segment in segments)
        override_count = sum(
            int(segment.metadata.get("soft_boundary_override_count", 0))
            for segment in segments
        )

        debug_summary = {
            "segmentation_mode": mode,
            "segmentation_input_layer": input_layer,
            "segment_count": len(segments),
            "closing_reason_counts": dict(sorted(closing_reason_counts.items())),
            "average_units_per_segment": round(total_units / len(segments), 3) if segments else 0.0,
            "average_sentences_per_segment": round(
                total_sentences / len(segments),
                3,
            ) if segments else 0.0,
            "average_segment_text_length": round(
                total_text_length / len(segments),
                3,
            ) if segments else 0.0,
            "soft_boundary_override_count": override_count,
        }
        if mode == "windowed":
            debug_summary["window_seconds"] = round(
                self.config.segmentation_window_seconds,
                3,
            )
            debug_summary["window_overlap_seconds"] = round(
                self.config.segmentation_window_overlap_seconds,
                3,
            )
            debug_summary["window_min_units"] = self.config.segmentation_window_min_units
        if mode == "adaptive":
            adaptive_scores = [
                float(segment.metadata["adaptive_boundary_score"])
                for segment in segments
                if "adaptive_boundary_score" in segment.metadata
            ]
            adaptive_hard_limit_count = sum(
                1
                for segment in segments
                if str(segment.metadata.get("closing_reason", "")).startswith(
                    "adaptive_hard_max",
                )
            )
            adaptive_scored_boundary_count = sum(
                1
                for segment in segments
                if segment.metadata.get("closing_reason") == "adaptive_boundary"
            )
            debug_summary["adaptive_scored_boundary_count"] = adaptive_scored_boundary_count
            debug_summary["adaptive_hard_limit_count"] = adaptive_hard_limit_count
            debug_summary["adaptive_average_boundary_score"] = round(
                sum(adaptive_scores) / len(adaptive_scores),
                3,
            ) if adaptive_scores else 0.0
        session.metadata["segmentation_debug"] = debug_summary

    def _merge_draft_into_previous(
        self,
        previous_draft: _SegmentDraft,
        draft: _SegmentDraft,
    ) -> None:
        """Append one draft into the previous draft during post-processing."""

        previous_draft.text_parts.extend(draft.text_parts)
        previous_draft.transcript_chunk_ids = self._ordered_union(
            previous_draft.transcript_chunk_ids,
            draft.transcript_chunk_ids,
        )
        previous_draft.merged_transcript_unit_ids = self._ordered_union(
            previous_draft.merged_transcript_unit_ids,
            draft.merged_transcript_unit_ids,
        )
        previous_draft.sentence_ids = self._ordered_union(
            previous_draft.sentence_ids,
            draft.sentence_ids,
        )
        previous_draft.source_utterance_ids = self._ordered_union(
            previous_draft.source_utterance_ids,
            draft.source_utterance_ids,
        )
        previous_draft.audio_source_ids = self._ordered_union(
            previous_draft.audio_source_ids,
            draft.audio_source_ids,
        )
        previous_draft.observed_languages = self._ordered_union(
            previous_draft.observed_languages,
            draft.observed_languages,
        )
        previous_draft.estimated_speaker_roles = self._ordered_union(
            previous_draft.estimated_speaker_roles,
            draft.estimated_speaker_roles,
        )
        previous_draft.raw_speaker_labels = self._ordered_union(
            previous_draft.raw_speaker_labels,
            draft.raw_speaker_labels,
        )
        previous_draft.observed_speaker_ids = self._ordered_union(
            previous_draft.observed_speaker_ids,
            draft.observed_speaker_ids,
        )
        previous_draft.end_seconds = max(previous_draft.end_seconds, draft.end_seconds)
        previous_draft.input_unit_count += draft.input_unit_count
        previous_draft.char_count = self._text_length(previous_draft.text_parts)
        previous_draft.speaker_uncertainty_count += draft.speaker_uncertainty_count
        previous_draft.speaker_unassigned_count += draft.speaker_unassigned_count
        previous_draft.metadata["singleton_merge_applied"] = True
        previous_draft.metadata["singleton_merge_count"] = (
            int(previous_draft.metadata.get("singleton_merge_count", 0)) + 1
        )

    def _prepend_draft_into_next(
        self,
        draft: _SegmentDraft,
        next_draft: _SegmentDraft,
    ) -> None:
        """Prepend one draft into the following draft during post-processing."""

        next_draft.text_parts = draft.text_parts + next_draft.text_parts
        next_draft.transcript_chunk_ids = self._ordered_union(
            draft.transcript_chunk_ids,
            next_draft.transcript_chunk_ids,
        )
        next_draft.merged_transcript_unit_ids = self._ordered_union(
            draft.merged_transcript_unit_ids,
            next_draft.merged_transcript_unit_ids,
        )
        next_draft.sentence_ids = self._ordered_union(draft.sentence_ids, next_draft.sentence_ids)
        next_draft.source_utterance_ids = self._ordered_union(
            draft.source_utterance_ids,
            next_draft.source_utterance_ids,
        )
        next_draft.audio_source_ids = self._ordered_union(
            draft.audio_source_ids,
            next_draft.audio_source_ids,
        )
        next_draft.observed_languages = self._ordered_union(
            draft.observed_languages,
            next_draft.observed_languages,
        )
        next_draft.estimated_speaker_roles = self._ordered_union(
            draft.estimated_speaker_roles,
            next_draft.estimated_speaker_roles,
        )
        next_draft.raw_speaker_labels = self._ordered_union(
            draft.raw_speaker_labels,
            next_draft.raw_speaker_labels,
        )
        next_draft.observed_speaker_ids = self._ordered_union(
            draft.observed_speaker_ids,
            next_draft.observed_speaker_ids,
        )
        next_draft.start_seconds = min(draft.start_seconds, next_draft.start_seconds)
        next_draft.input_unit_count += draft.input_unit_count
        next_draft.char_count = self._text_length(next_draft.text_parts)
        next_draft.speaker_uncertainty_count += draft.speaker_uncertainty_count
        next_draft.speaker_unassigned_count += draft.speaker_unassigned_count
        next_draft.metadata["singleton_merge_applied"] = True
        next_draft.metadata["singleton_merge_count"] = (
            int(next_draft.metadata.get("singleton_merge_count", 0)) + 1
        )

    def _is_short_singleton(self, draft: _SegmentDraft) -> bool:
        """Return whether a draft is a tiny one-unit segment."""

        if draft.input_unit_count != 1:
            return False
        text = self._joined_text(draft.text_parts)
        return self._is_weak_standalone_text(text)

    def _looks_incomplete(self, text: str) -> bool:
        """Return whether text looks like an unfinished local phrase."""

        stripped_text = text.strip()
        if not stripped_text:
            return True
        if stripped_text.endswith(self._STRONG_ENDING_PUNCTUATION):
            return False
        if stripped_text.endswith(self._WEAK_ENDING_PUNCTUATION):
            return True
        if self._ends_with_continuation_marker(stripped_text):
            return True
        return True

    def _looks_complete(self, text: str) -> bool:
        """Return whether text looks sufficiently complete as a segment end."""

        return not self._looks_incomplete(text) and not self._is_weak_standalone_text(text)

    def _is_weak_standalone_text(self, text: str) -> bool:
        """Return whether text is weak as an isolated segment."""

        return (
            len(text.strip()) < self.config.segmentation_min_standalone_text_length
            or self._word_count(text) < self.config.segmentation_min_standalone_word_count
        )

    def _ends_with_continuation_marker(self, text: str) -> bool:
        """Return whether text ends with a configured continuation marker."""

        last_word = self._last_word(text)
        if last_word is None:
            return False
        return last_word in self.config.segmentation_continuation_markers

    def _starts_with_continuation_marker(self, text: str) -> bool:
        """Return whether text starts with a configured continuation marker."""

        first_word = self._first_word(text)
        if first_word is None:
            return False
        return first_word in self.config.segmentation_continuation_markers

    def _starts_with_transition_marker(self, text: str) -> bool:
        """Return whether text starts with a configured adaptive transition marker."""

        first_word = self._first_word(text)
        if first_word is None:
            return False
        return first_word in self.config.segmentation_adaptive_transition_markers

    @staticmethod
    def _starts_with_lowercase(text: str) -> bool:
        """Return whether text starts with a lowercase alphabetic character."""

        for character in text.strip():
            if character.isalpha():
                return character.islower()
        return False

    @staticmethod
    def _append_unique(target: list, value: object) -> None:
        """Append a value only when it has not appeared before."""

        if value not in target:
            target.append(value)

    @staticmethod
    def _ordered_union(first: list, second: list) -> list:
        """Return the ordered union of two small lists."""

        ordered: list = []
        for value in first + second:
            if value not in ordered:
                ordered.append(value)
        return ordered

    @staticmethod
    def _unit_time_range(unit: _SegmentationUnit) -> tuple[float, float]:
        """Return the most stable time range available for a segmentation unit."""

        start_seconds = (
            unit.session_start_seconds
            if unit.session_start_seconds is not None
            else unit.start_seconds
        )
        end_seconds = (
            unit.session_end_seconds
            if unit.session_end_seconds is not None
            else unit.end_seconds
        )
        return float(start_seconds), float(end_seconds)

    @staticmethod
    def _text_length(text_parts: Sequence[str]) -> int:
        """Return the length of a space-joined segment text candidate."""

        return len(TranscriptSegmenter._joined_text(text_parts))

    @staticmethod
    def _joined_text(text_parts: Sequence[str]) -> str:
        """Return a readable text built from normalized transcript parts."""

        return " ".join(part for part in text_parts if part).strip()

    def _slice_duration_seconds(
        self,
        units: Sequence[_SegmentationUnit],
        start_index: int,
        end_index: int,
    ) -> float:
        """Return the duration of a consecutive unit slice."""

        start_seconds, _ = self._unit_time_range(units[start_index])
        _, end_seconds = self._unit_time_range(units[end_index])
        return end_seconds - start_seconds

    def _slice_text_length(
        self,
        units: Sequence[_SegmentationUnit],
        start_index: int,
        end_index: int,
    ) -> int:
        """Return the joined text length of a consecutive unit slice."""

        return len(
            self._joined_text(
                unit.text for unit in units[start_index : end_index + 1]
            ),
        )

    def _word_count(self, text: str) -> int:
        """Return a lightweight word count for transcript heuristics."""

        return len(self._WORD_RE.findall(text))

    def _last_word(self, text: str) -> str | None:
        """Return the last lowercase word in a text span when available."""

        words = self._WORD_RE.findall(text.lower())
        if not words:
            return None
        return words[-1]

    def _first_word(self, text: str) -> str | None:
        """Return the first lowercase word in a text span when available."""

        words = self._WORD_RE.findall(text.lower())
        if not words:
            return None
        return words[0]

    @staticmethod
    def _safe_float(value: float | None) -> float:
        """Return a stable float key even if timing metadata is missing."""

        if value is None:
            return float("inf")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("inf")

    @staticmethod
    def _source_order_index(
        audio_source_id: str,
        source_order: dict[str, int],
        unknown_source_order: dict[str, int],
    ) -> int:
        """Return a deterministic source order index for chunk sorting."""

        if audio_source_id in source_order:
            return source_order[audio_source_id]
        return unknown_source_order[audio_source_id]

    @staticmethod
    def _build_chunks_by_id(
        transcript_chunks: Sequence[TranscriptChunk],
    ) -> dict[str, list[TranscriptChunk]]:
        """Group transcript chunks by chunk id for sentence traceability."""

        chunks_by_id: dict[str, list[TranscriptChunk]] = defaultdict(list)
        for chunk in transcript_chunks:
            chunks_by_id[chunk.chunk_id].append(chunk)
        return dict(chunks_by_id)

    @staticmethod
    def _build_merged_unit_ids_by_chunk_id(
        merged_units: Sequence[MergedTranscriptUnit],
    ) -> dict[str, list[str]]:
        """Group merged transcript unit ids by originating chunk id."""

        unit_ids_by_chunk_id: dict[str, list[str]] = defaultdict(list)
        for merged_unit in merged_units:
            unit_ids_by_chunk_id[merged_unit.chunk_id].append(merged_unit.unit_id)
        return dict(unit_ids_by_chunk_id)

    @staticmethod
    def _ordered_sentences(sentences: Sequence[Sentence]) -> list[Sentence]:
        """Return sentences in a deterministic processing order."""

        return sorted(
            sentences,
            key=lambda sentence: (
                sentence.session_start_seconds
                if sentence.session_start_seconds is not None
                else sentence.start_seconds,
                sentence.session_end_seconds
                if sentence.session_end_seconds is not None
                else sentence.end_seconds,
                sentence.sentence_id,
            ),
        )

    @staticmethod
    def _ordered_unique(values: Sequence[object]) -> list[object]:
        """Return a deterministic ordered list without duplicates or nulls."""

        ordered: list[object] = []
        for value in values:
            if value is None or value in ordered:
                continue
            ordered.append(value)
        return ordered
