"""Tests for utterance speaker attribution from diarization overlap."""

from __future__ import annotations

import unittest

from analysis.audio_quality import AudioQualityAssessment
from analysis.speaker_attribution import SpeakerAttributor
from core.config import PipelineConfig
from core.models import AudioSource, DiarizationSegment, LectureSession, Utterance


class _StubAudioQualityAnalyzer:
    """Return deterministic per-utterance quality assessments for tests."""

    def __init__(
        self,
        assessments: dict[str, AudioQualityAssessment] | None = None,
    ) -> None:
        self.assessments = assessments or {}

    def assess_utterance(
        self,
        utterance: Utterance,
        audio_source: AudioSource | None,
    ) -> AudioQualityAssessment:
        del audio_source
        return self.assessments.get(
            utterance.utterance_id,
            AudioQualityAssessment(status="available", is_degraded=False),
        )


class SpeakerAttributionTests(unittest.TestCase):
    """Exercise anonymous speaker assignment for utterances."""

    def test_attribute_session_assigns_speaker_on_clear_max_overlap(self) -> None:
        """The dominant overlapping speaker should be assigned to the utterance."""

        attributor = SpeakerAttributor(
            PipelineConfig(
                diarization_enabled=True,
                speaker_attribution_min_overlap_ratio=0.6,
                speaker_attribution_ambiguity_ratio=0.85,
            ),
        )
        utterance = self._build_utterance("utterance_001", 0.0, 1.0)
        session = LectureSession(
            session_id="session_001",
            utterances=[utterance],
            diarization_segments=[
                self._build_diarization_segment(
                    segment_id="seg_1",
                    speaker_id="SPEAKER_00",
                    start_seconds=0.0,
                    end_seconds=0.8,
                ),
                self._build_diarization_segment(
                    segment_id="seg_2",
                    speaker_id="SPEAKER_01",
                    start_seconds=0.8,
                    end_seconds=1.0,
                ),
            ],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertEqual(attributed_utterances[0].speaker_id, "SPEAKER_00")
        self.assertEqual(
            attributed_utterances[0].metadata["speaker_attribution"]["status"],
            "assigned",
        )
        self.assertEqual(session.metadata["speaker_attribution_assigned_count"], 1)
        self.assertEqual(
            payload := session.to_dict()["utterances"][0]["speaker_id"],
            "SPEAKER_00",
        )

    def test_attribute_session_leaves_speaker_none_when_overlap_is_ambiguous(self) -> None:
        """Near-equal overlap between speakers should keep the utterance unassigned."""

        attributor = SpeakerAttributor(
            PipelineConfig(
                diarization_enabled=True,
                speaker_attribution_min_overlap_ratio=0.4,
                speaker_attribution_ambiguity_ratio=0.85,
            ),
        )
        utterance = self._build_utterance("utterance_001", 0.0, 1.0)
        session = LectureSession(
            session_id="session_001",
            utterances=[utterance],
            diarization_segments=[
                self._build_diarization_segment(
                    segment_id="seg_1",
                    speaker_id="SPEAKER_00",
                    start_seconds=0.0,
                    end_seconds=0.55,
                ),
                self._build_diarization_segment(
                    segment_id="seg_2",
                    speaker_id="SPEAKER_01",
                    start_seconds=0.45,
                    end_seconds=1.0,
                ),
            ],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertIsNone(attributed_utterances[0].speaker_id)
        self.assertEqual(
            attributed_utterances[0].metadata["speaker_attribution"]["reason"],
            "ambiguous_overlap",
        )
        self.assertEqual(session.metadata["speaker_attribution_assigned_count"], 0)
        self.assertEqual(session.metadata["speaker_attribution_unassigned_count"], 1)

    def test_real_speaker_change_survives_stability_smoothing(self) -> None:
        """Long enough speaker changes should remain intact after smoothing."""

        attributor = SpeakerAttributor(
            PipelineConfig(
                diarization_enabled=True,
                speaker_switch_min_duration_seconds=0.8,
                speaker_switch_min_stable_evidence_seconds=0.8,
            ),
        )
        utterances = [
            self._build_utterance("utterance_001", 0.0, 1.0),
            self._build_utterance("utterance_002", 1.0, 2.0),
            self._build_utterance("utterance_003", 2.0, 3.0),
        ]
        session = LectureSession(
            session_id="session_001",
            utterances=utterances,
            diarization_segments=[
                self._build_diarization_segment("seg_1", "SPEAKER_00", 0.0, 1.0),
                self._build_diarization_segment("seg_2", "SPEAKER_01", 1.0, 3.0),
            ],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertEqual(attributed_utterances[0].speaker_id, "SPEAKER_00")
        self.assertEqual(attributed_utterances[1].speaker_id, "SPEAKER_01")
        self.assertEqual(attributed_utterances[2].speaker_id, "SPEAKER_01")
        self.assertFalse(attributed_utterances[1].speaker_is_uncertain)

    def test_false_short_speaker_flip_is_smoothed(self) -> None:
        """A brief sandwich flip should collapse back to the surrounding speaker."""

        attributor = SpeakerAttributor(
            PipelineConfig(
                diarization_enabled=True,
                speaker_switch_min_duration_seconds=0.8,
                speaker_switch_min_stable_evidence_seconds=0.8,
            ),
        )
        utterances = [
            self._build_utterance("utterance_001", 0.0, 1.0),
            self._build_utterance("utterance_002", 1.0, 1.4),
            self._build_utterance("utterance_003", 1.4, 2.4),
        ]
        session = LectureSession(
            session_id="session_001",
            utterances=utterances,
            diarization_segments=[
                self._build_diarization_segment("seg_1", "SPEAKER_00", 0.0, 1.0),
                self._build_diarization_segment("seg_2", "SPEAKER_01", 1.0, 1.4),
                self._build_diarization_segment("seg_3", "SPEAKER_00", 1.4, 2.4),
            ],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertEqual(attributed_utterances[1].speaker_id, "SPEAKER_00")
        self.assertTrue(attributed_utterances[1].speaker_is_uncertain)
        self.assertEqual(
            attributed_utterances[1].metadata["speaker_attribution"]["reason"],
            "short_switch_smoothed",
        )

    def test_degraded_gap_between_same_speaker_stretches_is_bridged(self) -> None:
        """A short degraded gap should preserve the surrounding dominant speaker."""

        quality_analyzer = _StubAudioQualityAnalyzer(
            {
                "utterance_002": AudioQualityAssessment(
                    status="available",
                    is_degraded=True,
                    rms_ratio=0.2,
                    zero_crossing_rate=0.3,
                    degraded_reasons=["low_energy", "high_zero_crossing_rate"],
                ),
            },
        )
        attributor = SpeakerAttributor(
            PipelineConfig(
                diarization_enabled=True,
                speaker_switch_short_gap_merge_seconds=0.5,
            ),
            audio_quality_analyzer=quality_analyzer,
        )
        utterances = [
            self._build_utterance("utterance_001", 0.0, 1.0),
            self._build_utterance("utterance_002", 1.0, 1.35),
            self._build_utterance("utterance_003", 1.35, 2.35),
        ]
        session = LectureSession(
            session_id="session_001",
            utterances=utterances,
            diarization_segments=[
                self._build_diarization_segment("seg_1", "SPEAKER_00", 0.0, 1.0),
                self._build_diarization_segment("seg_2", "SPEAKER_00", 1.35, 2.35),
            ],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertEqual(attributed_utterances[1].speaker_id, "SPEAKER_00")
        self.assertTrue(attributed_utterances[1].speaker_is_uncertain)
        self.assertEqual(
            attributed_utterances[1].metadata["speaker_attribution"]["reason"],
            "short_gap_merged",
        )

    def test_attribute_session_skips_cleanly_without_diarization(self) -> None:
        """Missing diarization should leave utterances unassigned without crashing."""

        attributor = SpeakerAttributor(PipelineConfig(diarization_enabled=False))
        utterance = self._build_utterance("utterance_001", 0.0, 1.0)
        session = LectureSession(
            session_id="session_001",
            utterances=[utterance],
            diarization_segments=[],
        )

        attributed_utterances = attributor.attribute_session(session)

        self.assertIsNone(attributed_utterances[0].speaker_id)
        self.assertEqual(
            attributed_utterances[0].metadata["speaker_attribution"]["reason"],
            "diarization_unavailable",
        )
        self.assertEqual(session.metadata["speaker_attribution_status"], "skipped")

    @staticmethod
    def _build_utterance(
        utterance_id: str,
        start_seconds: float,
        end_seconds: float,
    ) -> Utterance:
        """Build a minimal utterance for attribution tests."""

        return Utterance(
            utterance_id=utterance_id,
            audio_source_id="audio_source_001",
            text="Hello there",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            aligned_segment_id="audio_source_001_aligned_segment_0001",
            aligned_segment_index=1,
        )

    @staticmethod
    def _build_diarization_segment(
        segment_id: str,
        speaker_id: str,
        start_seconds: float,
        end_seconds: float,
        segment_source: str = "regular",
    ) -> DiarizationSegment:
        """Build a minimal diarization segment for attribution tests."""

        return DiarizationSegment(
            diarization_segment_id=segment_id,
            audio_source_id="audio_source_001",
            speaker_id=speaker_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            segment_source=segment_source,
        )


if __name__ == "__main__":
    unittest.main()
