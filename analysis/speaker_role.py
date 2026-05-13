"""Placeholder speaker role estimation."""

from __future__ import annotations

from typing import Sequence

from core.models import Segment, SpeakerRoleEstimate
from core.types import SpeakerRole


class SpeakerRoleEstimator:
    """Estimate speaker roles from session segments."""

    def estimate(self, segments: Sequence[Segment]) -> list[SpeakerRoleEstimate]:
        """Return placeholder role estimates for observed speaker labels."""

        # Keep first appearance order so the placeholder heuristic remains
        # deterministic across repeated runs.
        labels = []
        for segment in segments:
            for speaker_label in segment.raw_speaker_labels:
                if speaker_label not in labels:
                    labels.append(speaker_label)

        estimates: list[SpeakerRoleEstimate] = []
        for index, label in enumerate(labels):
            role = SpeakerRole.TEACHER if index == 0 else SpeakerRole.STUDENT
            estimates.append(
                SpeakerRoleEstimate(
                    speaker_label=label,
                    speaker_role=role,
                    confidence=0.25,
                    source_segment_ids=[
                        segment.segment_id
                        for segment in segments
                        if label in segment.raw_speaker_labels
                    ],
                ),
            )
        return estimates

    @staticmethod
    def apply_to_segments(
        segments: Sequence[Segment],
        estimates: Sequence[SpeakerRoleEstimate],
    ) -> None:
        """Propagate estimated roles to matching segments."""

        role_by_label = {
            estimate.speaker_label: estimate.speaker_role for estimate in estimates
        }
        for segment in segments:
            # Propagation is intentionally narrow: only labels already present on
            # a segment can contribute a role.
            segment.estimated_speaker_roles = [
                role_by_label[speaker_label]
                for speaker_label in segment.raw_speaker_labels
                if speaker_label in role_by_label
            ]
