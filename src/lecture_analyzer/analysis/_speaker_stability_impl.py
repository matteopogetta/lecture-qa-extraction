"""Post-processing helpers that stabilize utterance-level speaker changes."""

from __future__ import annotations

from dataclasses import dataclass

from lecture_analyzer.analysis.audio_quality import AudioQualityAssessment
from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import Utterance


@dataclass(slots=True)
class SpeakerAttributionDecision:
    """Mutable attribution record for one utterance before final persistence."""

    utterance: Utterance
    provisional_speaker_id: str | None
    provisional_status: str
    provisional_reason: str | None
    top_speaker_id: str | None
    top_overlap_seconds: float
    top_overlap_ratio: float
    runner_up_overlap_seconds: float
    segment_source: str | None
    confidence_score: float
    audio_quality: AudioQualityAssessment
    speaker_id: str | None = None
    status: str = ""
    reason: str | None = None
    stability_label: str = "direct"
    is_uncertain: bool = False

    def __post_init__(self) -> None:
        """Initialize mutable final-state fields from the provisional state."""

        if not self.status:
            self.status = self.provisional_status
        if self.reason is None:
            self.reason = self.provisional_reason
        if self.speaker_id is None:
            self.speaker_id = self.provisional_speaker_id

    @property
    def duration_seconds(self) -> float:
        """Return the utterance duration used by stability heuristics."""

        return max(0.0, self.utterance.end_seconds - self.utterance.start_seconds)


@dataclass(slots=True)
class _DecisionRun:
    """Contiguous group of decisions sharing the same current speaker state."""

    decisions: list[SpeakerAttributionDecision]
    speaker_id: str | None

    @property
    def duration_seconds(self) -> float:
        """Return the temporal span covered by the current run."""

        if not self.decisions:
            return 0.0
        return max(
            0.0,
            self.decisions[-1].utterance.end_seconds
            - self.decisions[0].utterance.start_seconds,
        )

    @property
    def evidence_seconds(self) -> float:
        """Return the total overlap evidence available for this run."""

        return sum(decision.top_overlap_seconds for decision in self.decisions)

    @property
    def degraded_count(self) -> int:
        """Return how many decisions in the run were marked degraded."""

        return sum(1 for decision in self.decisions if decision.audio_quality.is_degraded)


class SpeakerStabilitySmoother:
    """Smooth short or degraded speaker flips after overlap attribution."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def smooth_assignments(
        self,
        decisions: list[SpeakerAttributionDecision],
    ) -> list[SpeakerAttributionDecision]:
        """Return the same decisions after applying stability heuristics."""

        if len(decisions) < 2:
            self._mark_residual_uncertainty(decisions)
            return decisions

        self._bridge_short_gaps(decisions)
        self._smooth_short_switches(decisions)
        self._preserve_previous_speaker(decisions)
        self._mark_residual_uncertainty(decisions)
        return decisions

    def _bridge_short_gaps(self, decisions: list[SpeakerAttributionDecision]) -> None:
        """Carry a stable speaker through short unassigned spans when possible."""

        runs = self._collect_runs(decisions)
        for index, run in enumerate(runs):
            if run.speaker_id is not None:
                continue
            previous_speaker = self._neighbor_speaker(runs, index, -1)
            next_speaker = self._neighbor_speaker(runs, index, 1)
            if previous_speaker is None or previous_speaker != next_speaker:
                continue
            if run.duration_seconds > self.config.speaker_switch_short_gap_merge_seconds:
                continue
            for decision in run.decisions:
                self._reassign(
                    decision,
                    speaker_id=previous_speaker,
                    reason="short_gap_merged",
                    stability_label="bridged_gap",
                    uncertain=self.config.speaker_attribution_allow_uncertain,
                )

    def _smooth_short_switches(
        self,
        decisions: list[SpeakerAttributionDecision],
    ) -> None:
        """Collapse brief same-speaker sandwich flips into a stable speaker."""

        runs = self._collect_runs(decisions)
        for index, run in enumerate(runs):
            if run.speaker_id is None:
                continue
            previous_speaker = self._neighbor_speaker(runs, index, -1)
            next_speaker = self._neighbor_speaker(runs, index, 1)
            if (
                previous_speaker is None
                or next_speaker is None
                or previous_speaker != next_speaker
                or previous_speaker == run.speaker_id
            ):
                continue
            if not self._is_unstable_run(run):
                continue
            for decision in run.decisions:
                self._reassign(
                    decision,
                    speaker_id=previous_speaker,
                    reason="short_switch_smoothed",
                    stability_label="short_switch",
                    uncertain=self.config.speaker_attribution_allow_uncertain,
                )

    def _preserve_previous_speaker(
        self,
        decisions: list[SpeakerAttributionDecision],
    ) -> None:
        """Preserve the previous speaker over short degraded spans."""

        runs = self._collect_runs(decisions)
        for index, run in enumerate(runs):
            if run.speaker_id is None:
                continue
            previous_speaker = self._neighbor_speaker(runs, index, -1)
            if previous_speaker is None or previous_speaker == run.speaker_id:
                continue
            if not self._is_unstable_run(run):
                continue
            if run.degraded_count == 0:
                continue
            for decision in run.decisions:
                self._reassign(
                    decision,
                    speaker_id=previous_speaker,
                    reason="degraded_span_preserved_previous",
                    stability_label="preserved_previous",
                    uncertain=self.config.speaker_attribution_allow_uncertain,
                )

    def _mark_residual_uncertainty(
        self,
        decisions: list[SpeakerAttributionDecision],
    ) -> None:
        """Expose degraded or unresolved spans without forcing new speakers."""

        for decision in decisions:
            if decision.speaker_id is None:
                if (
                    self.config.speaker_attribution_allow_uncertain
                    and decision.audio_quality.is_degraded
                ):
                    decision.status = "uncertain"
                    decision.is_uncertain = True
                    decision.stability_label = "degraded_unassigned"
                continue
            if decision.audio_quality.is_degraded and decision.stability_label == "direct":
                if self.config.speaker_attribution_allow_uncertain:
                    decision.status = "uncertain"
                    decision.is_uncertain = True
                    decision.stability_label = "degraded_direct"
                decision.confidence_score = round(decision.confidence_score * 0.75, 4)

    def _is_unstable_run(self, run: _DecisionRun) -> bool:
        """Return whether a run is too short or weak to count as a real switch."""

        if run.duration_seconds < self.config.speaker_switch_min_duration_seconds:
            return True
        if run.evidence_seconds < self.config.speaker_switch_min_stable_evidence_seconds:
            return True
        return (
            run.degraded_count == len(run.decisions)
            and run.duration_seconds
            <= (
                self.config.speaker_switch_min_duration_seconds
                + self.config.speaker_switch_short_gap_merge_seconds
            )
        )

    @staticmethod
    def _collect_runs(
        decisions: list[SpeakerAttributionDecision],
    ) -> list[_DecisionRun]:
        """Return contiguous runs based on the current speaker assignment."""

        if not decisions:
            return []

        runs: list[_DecisionRun] = []
        current_run: list[SpeakerAttributionDecision] = [decisions[0]]
        current_speaker_id = decisions[0].speaker_id
        for decision in decisions[1:]:
            if decision.speaker_id == current_speaker_id:
                current_run.append(decision)
                continue
            runs.append(
                _DecisionRun(
                    decisions=current_run,
                    speaker_id=current_speaker_id,
                ),
            )
            current_run = [decision]
            current_speaker_id = decision.speaker_id
        runs.append(
            _DecisionRun(
                decisions=current_run,
                speaker_id=current_speaker_id,
            ),
        )
        return runs

    @staticmethod
    def _neighbor_speaker(
        runs: list[_DecisionRun],
        index: int,
        direction: int,
    ) -> str | None:
        """Return the nearest non-empty speaker id around the selected run."""

        cursor = index + direction
        while 0 <= cursor < len(runs):
            speaker_id = runs[cursor].speaker_id
            if speaker_id is not None:
                return speaker_id
            cursor += direction
        return None

    @staticmethod
    def _reassign(
        decision: SpeakerAttributionDecision,
        speaker_id: str,
        reason: str,
        stability_label: str,
        uncertain: bool,
    ) -> None:
        """Rewrite one decision to preserve a more stable speaker state."""

        decision.speaker_id = speaker_id
        decision.reason = reason
        decision.stability_label = stability_label
        decision.is_uncertain = uncertain
        decision.status = "uncertain" if uncertain else "assigned"
        decision.confidence_score = round(decision.confidence_score * 0.7, 4)
