"""Assign anonymous speaker identifiers to utterances using diarization overlap."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from analysis.audio_quality import AudioQualityAnalyzer
from analysis.speaker_stability import (
    SpeakerAttributionDecision,
    SpeakerStabilitySmoother,
)
from core.config import PipelineConfig
from core.models import AudioSource, DiarizationSegment, LectureSession, Utterance


class SpeakerAttributor:
    """Assign speaker identifiers to utterances using overlap and stability rules."""

    def __init__(
        self,
        config: PipelineConfig,
        audio_quality_analyzer: AudioQualityAnalyzer | None = None,
        stability_smoother: SpeakerStabilitySmoother | None = None,
    ) -> None:
        self.config = config
        self.audio_quality_analyzer = audio_quality_analyzer or AudioQualityAnalyzer(
            config,
        )
        self.stability_smoother = stability_smoother or SpeakerStabilitySmoother(config)

    def attribute_session(self, session: LectureSession) -> list[Utterance]:
        """Assign speaker ids to the current session utterances when possible."""

        if not session.utterances:
            session.metadata["speaker_attribution_status"] = "skipped"
            session.metadata["speaker_attribution_reason"] = "utterances_unavailable"
            session.metadata["speaker_attribution_assigned_count"] = 0
            session.metadata["speaker_attribution_uncertain_count"] = 0
            session.metadata["speaker_attribution_unassigned_count"] = 0
            for audio_source in session.audio_sources:
                audio_source.metadata["speaker_attribution"] = {
                    "status": "skipped",
                    "reason": "utterances_unavailable",
                    "assigned_utterance_count": 0,
                    "uncertain_utterance_count": 0,
                    "unassigned_utterance_count": 0,
                }
            return []

        diarization_by_source = self._group_segments_by_source(session.diarization_segments)
        audio_sources_by_id = {
            audio_source.audio_source_id: audio_source
            for audio_source in session.audio_sources
        }
        if not diarization_by_source:
            utterance_counts_by_source = {
                audio_source.audio_source_id: 0
                for audio_source in session.audio_sources
            }
            for utterance in session.utterances:
                utterance_counts_by_source[utterance.audio_source_id] = (
                    utterance_counts_by_source.get(utterance.audio_source_id, 0) + 1
                )
                self._apply_unassigned_metadata(
                    utterance,
                    reason="diarization_unavailable",
                    top_speaker_id=None,
                    top_overlap_seconds=0.0,
                    top_overlap_ratio=0.0,
                    runner_up_overlap_seconds=0.0,
                    confidence_score=0.0,
                )
            session.metadata["speaker_attribution_status"] = "skipped"
            session.metadata["speaker_attribution_reason"] = "diarization_unavailable"
            session.metadata["speaker_attribution_assigned_count"] = 0
            session.metadata["speaker_attribution_uncertain_count"] = 0
            session.metadata["speaker_attribution_unassigned_count"] = len(
                session.utterances,
            )
            for audio_source in session.audio_sources:
                audio_source.metadata["speaker_attribution"] = {
                    "status": "skipped",
                    "reason": "diarization_unavailable",
                    "assigned_utterance_count": 0,
                    "uncertain_utterance_count": 0,
                    "unassigned_utterance_count": utterance_counts_by_source.get(
                        audio_source.audio_source_id,
                        0,
                    ),
                }
            return session.utterances

        assigned_count = 0
        uncertain_count = 0
        unassigned_count = 0
        assigned_by_source: dict[str, int] = defaultdict(int)
        uncertain_by_source: dict[str, int] = defaultdict(int)
        total_by_source: dict[str, int] = defaultdict(int)

        utterances_by_source = self._group_utterances_by_source(session.utterances)
        for audio_source_id, utterances in utterances_by_source.items():
            for utterance in utterances:
                total_by_source[audio_source_id] += 1

            diarization_segments = diarization_by_source.get(audio_source_id, [])
            audio_source = audio_sources_by_id.get(audio_source_id)
            decisions = [
                self.attribute_utterance(
                    utterance=utterance,
                    diarization_segments=diarization_segments,
                    audio_source=audio_source,
                )
                for utterance in utterances
            ]
            decisions = self.stability_smoother.smooth_assignments(decisions)

            for decision in decisions:
                self._apply_decision(decision)
                if decision.speaker_id is not None:
                    assigned_count += 1
                    assigned_by_source[audio_source_id] += 1
                else:
                    unassigned_count += 1
                if decision.is_uncertain:
                    uncertain_count += 1
                    uncertain_by_source[audio_source_id] += 1

        session.metadata["speaker_attribution_status"] = (
            "ready" if assigned_count > 0 else "skipped"
        )
        session.metadata["speaker_attribution_assigned_count"] = assigned_count
        session.metadata["speaker_attribution_uncertain_count"] = uncertain_count
        session.metadata["speaker_attribution_unassigned_count"] = unassigned_count
        session.metadata["speaker_attribution_reason"] = (
            None if assigned_count > 0 else "no_reliable_overlap"
        )

        for audio_source in session.audio_sources:
            total_utterances = total_by_source.get(audio_source.audio_source_id, 0)
            assigned_utterances = assigned_by_source.get(audio_source.audio_source_id, 0)
            uncertain_utterances = uncertain_by_source.get(
                audio_source.audio_source_id,
                0,
            )
            status = "ready" if assigned_utterances > 0 else "skipped"
            audio_source.metadata["speaker_attribution"] = {
                "status": status,
                "reason": None if assigned_utterances > 0 else "no_reliable_overlap",
                "assigned_utterance_count": assigned_utterances,
                "uncertain_utterance_count": uncertain_utterances,
                "unassigned_utterance_count": max(
                    0,
                    total_utterances - assigned_utterances,
                ),
            }
        return session.utterances

    def attribute_utterance(
        self,
        utterance: Utterance,
        diarization_segments: Sequence[DiarizationSegment],
        audio_source: AudioSource | None,
    ) -> SpeakerAttributionDecision:
        """Return one provisional speaker decision for the utterance."""

        audio_quality = self.audio_quality_analyzer.assess_utterance(
            utterance,
            audio_source,
        )
        utterance_duration = max(0.0, utterance.end_seconds - utterance.start_seconds)
        if utterance_duration <= 0.0:
            return self._build_decision(
                utterance=utterance,
                provisional_speaker_id=None,
                provisional_status="unassigned",
                provisional_reason="invalid_utterance_timing",
                top_speaker_id=None,
                top_overlap_seconds=0.0,
                top_overlap_ratio=0.0,
                runner_up_overlap_seconds=0.0,
                segment_source=None,
                confidence_score=0.0,
                audio_quality=audio_quality,
            )

        overlap_by_speaker: dict[str, float] = defaultdict(float)
        overlap_by_source: dict[str, float] = defaultdict(float)
        for diarization_segment in diarization_segments:
            overlap_seconds = self._overlap_seconds(
                utterance.start_seconds,
                utterance.end_seconds,
                diarization_segment.start_seconds,
                diarization_segment.end_seconds,
            )
            if overlap_seconds <= 0.0:
                continue
            overlap_by_speaker[diarization_segment.speaker_id] += overlap_seconds
            overlap_by_source[diarization_segment.segment_source] += overlap_seconds

        if not overlap_by_speaker:
            return self._build_decision(
                utterance=utterance,
                provisional_speaker_id=None,
                provisional_status="unassigned",
                provisional_reason="no_overlap",
                top_speaker_id=None,
                top_overlap_seconds=0.0,
                top_overlap_ratio=0.0,
                runner_up_overlap_seconds=0.0,
                segment_source=None,
                confidence_score=0.0,
                audio_quality=audio_quality,
            )

        ranked_overlaps = sorted(
            overlap_by_speaker.items(),
            key=lambda item: (-item[1], item[0]),
        )
        ranked_sources = sorted(
            overlap_by_source.items(),
            key=lambda item: (-item[1], item[0]),
        )
        top_speaker_id, top_overlap_seconds = ranked_overlaps[0]
        runner_up_overlap_seconds = (
            ranked_overlaps[1][1] if len(ranked_overlaps) > 1 else 0.0
        )
        top_overlap_ratio = top_overlap_seconds / utterance_duration
        segment_source = ranked_sources[0][0] if ranked_sources else None
        confidence_score = self._build_confidence_score(
            top_overlap_ratio=top_overlap_ratio,
            top_overlap_seconds=top_overlap_seconds,
            runner_up_overlap_seconds=runner_up_overlap_seconds,
            utterance_duration=utterance_duration,
            audio_quality=audio_quality,
        )

        if top_overlap_ratio < self.config.speaker_attribution_min_overlap_ratio:
            return self._build_decision(
                utterance=utterance,
                provisional_speaker_id=None,
                provisional_status="unassigned",
                provisional_reason="low_overlap",
                top_speaker_id=top_speaker_id,
                top_overlap_seconds=top_overlap_seconds,
                top_overlap_ratio=top_overlap_ratio,
                runner_up_overlap_seconds=runner_up_overlap_seconds,
                segment_source=segment_source,
                confidence_score=confidence_score,
                audio_quality=audio_quality,
            )

        if (
            runner_up_overlap_seconds > 0.0
            and top_overlap_seconds > 0.0
            and runner_up_overlap_seconds / top_overlap_seconds
            >= self.config.speaker_attribution_ambiguity_ratio
        ):
            return self._build_decision(
                utterance=utterance,
                provisional_speaker_id=None,
                provisional_status="unassigned",
                provisional_reason="ambiguous_overlap",
                top_speaker_id=top_speaker_id,
                top_overlap_seconds=top_overlap_seconds,
                top_overlap_ratio=top_overlap_ratio,
                runner_up_overlap_seconds=runner_up_overlap_seconds,
                segment_source=segment_source,
                confidence_score=confidence_score,
                audio_quality=audio_quality,
            )

        return self._build_decision(
            utterance=utterance,
            provisional_speaker_id=top_speaker_id,
            provisional_status="assigned",
            provisional_reason=None,
            top_speaker_id=top_speaker_id,
            top_overlap_seconds=top_overlap_seconds,
            top_overlap_ratio=top_overlap_ratio,
            runner_up_overlap_seconds=runner_up_overlap_seconds,
            segment_source=segment_source,
            confidence_score=confidence_score,
            audio_quality=audio_quality,
        )

    @staticmethod
    def _group_segments_by_source(
        diarization_segments: Sequence[DiarizationSegment],
    ) -> dict[str, list[DiarizationSegment]]:
        """Return diarization segments grouped by source while preserving order."""

        segments_by_source: dict[str, list[DiarizationSegment]] = defaultdict(list)
        for segment in diarization_segments:
            segments_by_source[segment.audio_source_id].append(segment)
        return dict(segments_by_source)

    @staticmethod
    def _group_utterances_by_source(
        utterances: Sequence[Utterance],
    ) -> dict[str, list[Utterance]]:
        """Return utterances grouped by source in chronological order."""

        utterances_by_source: dict[str, list[Utterance]] = defaultdict(list)
        for utterance in utterances:
            utterances_by_source[utterance.audio_source_id].append(utterance)
        for source_utterances in utterances_by_source.values():
            source_utterances.sort(
                key=lambda item: (item.start_seconds, item.end_seconds, item.utterance_id),
            )
        return dict(utterances_by_source)

    @staticmethod
    def _overlap_seconds(
        start_a: float,
        end_a: float,
        start_b: float,
        end_b: float,
    ) -> float:
        """Return the positive overlap duration between two time intervals."""

        return max(0.0, min(end_a, end_b) - max(start_a, start_b))

    def _apply_decision(self, decision: SpeakerAttributionDecision) -> None:
        """Persist the final decision back into the utterance model."""

        utterance = decision.utterance
        utterance.speaker_id = decision.speaker_id
        utterance.speaker_attribution_status = decision.status
        utterance.speaker_confidence_score = round(decision.confidence_score, 4)
        utterance.speaker_is_uncertain = decision.is_uncertain
        utterance.metadata["speaker_attribution"] = {
            "status": decision.status,
            "reason": decision.reason,
            "top_speaker_id": decision.top_speaker_id,
            "provisional_speaker_id": decision.provisional_speaker_id,
            "provisional_status": decision.provisional_status,
            "provisional_reason": decision.provisional_reason,
            "final_speaker_id": decision.speaker_id,
            "top_overlap_seconds": round(decision.top_overlap_seconds, 4),
            "top_overlap_ratio": round(decision.top_overlap_ratio, 4),
            "runner_up_overlap_seconds": round(
                decision.runner_up_overlap_seconds,
                4,
            ),
            "confidence_score": round(decision.confidence_score, 4),
            "is_uncertain": decision.is_uncertain,
            "stability_label": decision.stability_label,
            "segment_source": decision.segment_source,
            "audio_quality": decision.audio_quality.to_dict(),
        }

    def _apply_unassigned_metadata(
        self,
        utterance: Utterance,
        reason: str,
        top_speaker_id: str | None,
        top_overlap_seconds: float,
        top_overlap_ratio: float,
        runner_up_overlap_seconds: float,
        confidence_score: float,
    ) -> None:
        """Attach a standardized unassigned attribution payload to one utterance."""

        utterance.speaker_id = None
        utterance.speaker_attribution_status = "unassigned"
        utterance.speaker_confidence_score = round(confidence_score, 4)
        utterance.speaker_is_uncertain = False
        utterance.metadata["speaker_attribution"] = {
            "status": "unassigned",
            "reason": reason,
            "top_speaker_id": top_speaker_id,
            "top_overlap_seconds": round(top_overlap_seconds, 4),
            "top_overlap_ratio": round(top_overlap_ratio, 4),
            "runner_up_overlap_seconds": round(runner_up_overlap_seconds, 4),
            "confidence_score": round(confidence_score, 4),
            "is_uncertain": False,
            "stability_label": "direct",
        }

    @staticmethod
    def _build_decision(
        utterance: Utterance,
        provisional_speaker_id: str | None,
        provisional_status: str,
        provisional_reason: str | None,
        top_speaker_id: str | None,
        top_overlap_seconds: float,
        top_overlap_ratio: float,
        runner_up_overlap_seconds: float,
        segment_source: str | None,
        confidence_score: float,
        audio_quality,
    ) -> SpeakerAttributionDecision:
        """Return a normalized provisional speaker decision."""

        return SpeakerAttributionDecision(
            utterance=utterance,
            provisional_speaker_id=provisional_speaker_id,
            provisional_status=provisional_status,
            provisional_reason=provisional_reason,
            top_speaker_id=top_speaker_id,
            top_overlap_seconds=round(top_overlap_seconds, 4),
            top_overlap_ratio=round(top_overlap_ratio, 4),
            runner_up_overlap_seconds=round(runner_up_overlap_seconds, 4),
            segment_source=segment_source,
            confidence_score=round(confidence_score, 4),
            audio_quality=audio_quality,
        )

    @staticmethod
    def _build_confidence_score(
        top_overlap_ratio: float,
        top_overlap_seconds: float,
        runner_up_overlap_seconds: float,
        utterance_duration: float,
        audio_quality,
    ) -> float:
        """Return a compact confidence score derived from overlap evidence."""

        if utterance_duration <= 0.0:
            return 0.0
        margin_ratio = 1.0
        if top_overlap_seconds > 0.0 and runner_up_overlap_seconds > 0.0:
            margin_ratio = max(
                0.0,
                1.0 - (runner_up_overlap_seconds / top_overlap_seconds),
            )
        coverage_score = min(1.0, max(0.0, top_overlap_ratio))
        evidence_score = min(1.0, top_overlap_seconds / utterance_duration)
        confidence_score = (coverage_score * 0.55) + (margin_ratio * 0.25) + (
            evidence_score * 0.20
        )
        if audio_quality.is_degraded:
            confidence_score *= 0.7
        return min(1.0, max(0.0, confidence_score))
