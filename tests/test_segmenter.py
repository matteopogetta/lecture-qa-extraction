"""Tests for sentence-centric transcript segmentation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analysis.segmenter import TranscriptSegmenter
from core.config import PipelineConfig
from core.models import (
    LectureSession,
    MergedTranscript,
    MergedTranscriptUnit,
    Segment,
    Sentence,
    TranscriptChunk,
    Utterance,
)
from core.types import SpeakerRole


class TranscriptSegmenterTests(unittest.TestCase):
    """Exercise segmentation over sentence-centric inputs."""

    def test_structural_segmentation_uses_sentences_as_primary_input(self) -> None:
        """Structural segmentation should aggregate sentence-level traces."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                segmentation_max_gap_seconds=1.0,
                segmentation_soft_max_gap_seconds=2.0,
            )
            segmenter = TranscriptSegmenter(config)
            session = self._build_sentence_session()

            segments = segmenter.segment_session(session, mode="structural")

            self.assertEqual(len(segments), 2)
            self.assertEqual(session.metadata["segmentation_input_layer"], "sentences")
            self.assertEqual(segments[0].sentence_ids, ["sentence_0001", "sentence_0002"])
            self.assertEqual(
                segments[0].source_utterance_ids,
                ["utterance_0001", "utterance_0002", "utterance_0003"],
            )
            self.assertEqual(
                segments[0].transcript_chunk_ids,
                ["chunk_0001", "chunk_0002"],
            )
            self.assertEqual(
                segments[0].merged_transcript_unit_ids,
                ["unit_0001", "unit_0002"],
            )
            self.assertEqual(
                segments[0].metadata["speaker_uncertain_utterance_count"],
                1,
            )
            self.assertEqual(
                segments[0].metadata["speaker_unassigned_utterance_count"],
                1,
            )

    def test_windowed_and_adaptive_modes_operate_on_sentences(self) -> None:
        """All non-fallback segmentation modes should preserve sentence traces."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                segmentation_window_seconds=3.0,
                segmentation_window_overlap_seconds=0.0,
                segmentation_adaptive_target_duration_seconds=2.5,
                segmentation_adaptive_max_duration_seconds=6.0,
                segmentation_adaptive_target_text_length=40,
                segmentation_adaptive_max_text_length=120,
            )
            segmenter = TranscriptSegmenter(config)

            for mode in ("windowed", "adaptive"):
                session = self._build_sentence_session()
                segments = segmenter.segment_session(session, mode=mode)

                self.assertTrue(segments)
                self.assertEqual(session.metadata["segmentation_input_layer"], "sentences")
                self.assertTrue(all(segment.sentence_ids for segment in segments))
                self.assertTrue(
                    all(
                        segment.metadata["segmentation_mode"] == mode
                        for segment in segments
                    ),
                )

    def test_segmenter_falls_back_explicitly_to_merged_transcript(self) -> None:
        """Merged transcript fallback should remain available and explicit."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            segmenter = TranscriptSegmenter(config)
            merged_units = [
                MergedTranscriptUnit(
                    unit_id="unit_0001",
                    chunk_id="chunk_0001",
                    chunk_occurrence=1,
                    audio_source_id="audio_source_001",
                    source_order_index=1,
                    input_source_id="source_001",
                    start_seconds=0.0,
                    end_seconds=2.0,
                    session_start_seconds=0.0,
                    session_end_seconds=2.0,
                    text="Fallback merged transcript unit.",
                    detected_language="en",
                ),
            ]
            session = LectureSession(
                session_id="session_001",
                transcript_chunks=[
                    TranscriptChunk(
                        chunk_id="chunk_0001",
                        audio_source_id="audio_source_001",
                        start_seconds=0.0,
                        end_seconds=2.0,
                        text="Fallback merged transcript unit.",
                        speaker_label="speaker_1",
                        estimated_speaker_role=SpeakerRole.TEACHER,
                        detected_language="en",
                    ),
                ],
                merged_transcript=MergedTranscript(
                    session_id="session_001",
                    units=merged_units,
                    full_text="Fallback merged transcript unit.",
                    detected_languages=["en"],
                ),
            )

            segments = segmenter.segment_session(session, mode="structural")

            self.assertEqual(len(segments), 1)
            self.assertEqual(
                session.metadata["segmentation_input_layer"],
                "merged_transcript_fallback",
            )
            self.assertEqual(
                session.metadata["segmentation_reason"],
                "sentences_unavailable_using_merged_transcript",
            )
            self.assertEqual(segments[0].sentence_ids, [])
            self.assertEqual(segments[0].merged_transcript_unit_ids, ["unit_0001"])

    @staticmethod
    def _build_sentence_session() -> LectureSession:
        """Build a lightweight session with sentence and utterance traces."""

        utterances = [
            Utterance(
                utterance_id="utterance_0001",
                audio_source_id="audio_source_001",
                text="First complete",
                start_seconds=0.0,
                end_seconds=0.8,
                aligned_segment_id="aligned_segment_0001",
                aligned_segment_index=1,
                transcript_chunk_id="chunk_0001",
                speaker_id="SPEAKER_00",
                speaker_is_uncertain=False,
                detected_language="en",
            ),
            Utterance(
                utterance_id="utterance_0002",
                audio_source_id="audio_source_001",
                text="sentence.",
                start_seconds=0.81,
                end_seconds=1.4,
                aligned_segment_id="aligned_segment_0001",
                aligned_segment_index=1,
                transcript_chunk_id="chunk_0001",
                speaker_id="SPEAKER_00",
                speaker_is_uncertain=True,
                detected_language="en",
            ),
            Utterance(
                utterance_id="utterance_0003",
                audio_source_id="audio_source_001",
                text="Second complete sentence.",
                start_seconds=1.5,
                end_seconds=2.6,
                aligned_segment_id="aligned_segment_0002",
                aligned_segment_index=2,
                transcript_chunk_id="chunk_0002",
                speaker_id=None,
                speaker_is_uncertain=False,
                detected_language="en",
            ),
            Utterance(
                utterance_id="utterance_0004",
                audio_source_id="audio_source_001",
                text="New topic begins.",
                start_seconds=5.2,
                end_seconds=6.1,
                aligned_segment_id="aligned_segment_0003",
                aligned_segment_index=3,
                transcript_chunk_id="chunk_0003",
                speaker_id="SPEAKER_01",
                speaker_is_uncertain=False,
                detected_language="en",
            ),
        ]
        sentences = [
            Sentence(
                sentence_id="sentence_0001",
                audio_source_id="audio_source_001",
                text="First complete sentence.",
                start_seconds=0.0,
                end_seconds=1.4,
                source_utterance_ids=["utterance_0001", "utterance_0002"],
                source_utterance_start_index=1,
                source_utterance_end_index=2,
                detected_language="en",
                speaker_id="SPEAKER_00",
                session_start_seconds=0.0,
                session_end_seconds=1.4,
            ),
            Sentence(
                sentence_id="sentence_0002",
                audio_source_id="audio_source_001",
                text="Second complete sentence.",
                start_seconds=1.5,
                end_seconds=2.6,
                source_utterance_ids=["utterance_0003"],
                source_utterance_start_index=3,
                source_utterance_end_index=3,
                detected_language="en",
                speaker_id=None,
                session_start_seconds=1.5,
                session_end_seconds=2.6,
            ),
            Sentence(
                sentence_id="sentence_0003",
                audio_source_id="audio_source_001",
                text="New topic begins.",
                start_seconds=5.2,
                end_seconds=6.1,
                source_utterance_ids=["utterance_0004"],
                source_utterance_start_index=4,
                source_utterance_end_index=4,
                detected_language="en",
                speaker_id="SPEAKER_01",
                session_start_seconds=5.2,
                session_end_seconds=6.1,
            ),
        ]
        merged_units = [
            MergedTranscriptUnit(
                unit_id="unit_0001",
                chunk_id="chunk_0001",
                chunk_occurrence=1,
                audio_source_id="audio_source_001",
                source_order_index=1,
                input_source_id="source_001",
                start_seconds=0.0,
                end_seconds=1.4,
                session_start_seconds=0.0,
                session_end_seconds=1.4,
                text="First complete sentence.",
                detected_language="en",
            ),
            MergedTranscriptUnit(
                unit_id="unit_0002",
                chunk_id="chunk_0002",
                chunk_occurrence=1,
                audio_source_id="audio_source_001",
                source_order_index=1,
                input_source_id="source_001",
                start_seconds=1.5,
                end_seconds=2.6,
                session_start_seconds=1.5,
                session_end_seconds=2.6,
                text="Second complete sentence.",
                detected_language="en",
            ),
            MergedTranscriptUnit(
                unit_id="unit_0003",
                chunk_id="chunk_0003",
                chunk_occurrence=1,
                audio_source_id="audio_source_001",
                source_order_index=1,
                input_source_id="source_001",
                start_seconds=5.2,
                end_seconds=6.1,
                session_start_seconds=5.2,
                session_end_seconds=6.1,
                text="New topic begins.",
                detected_language="en",
            ),
        ]
        transcript_chunks = [
            TranscriptChunk(
                chunk_id="chunk_0001",
                audio_source_id="audio_source_001",
                start_seconds=0.0,
                end_seconds=1.4,
                text="First complete sentence.",
                speaker_label="speaker_1",
                estimated_speaker_role=SpeakerRole.TEACHER,
                detected_language="en",
            ),
            TranscriptChunk(
                chunk_id="chunk_0002",
                audio_source_id="audio_source_001",
                start_seconds=1.5,
                end_seconds=2.6,
                text="Second complete sentence.",
                speaker_label="speaker_2",
                estimated_speaker_role=SpeakerRole.STUDENT,
                detected_language="en",
            ),
            TranscriptChunk(
                chunk_id="chunk_0003",
                audio_source_id="audio_source_001",
                start_seconds=5.2,
                end_seconds=6.1,
                text="New topic begins.",
                speaker_label="speaker_1",
                estimated_speaker_role=SpeakerRole.TEACHER,
                detected_language="en",
            ),
        ]
        return LectureSession(
            session_id="session_001",
            transcript_chunks=transcript_chunks,
            merged_transcript=MergedTranscript(
                session_id="session_001",
                units=merged_units,
                full_text=" ".join(unit.text for unit in merged_units),
                detected_languages=["en"],
            ),
            transcript_text=" ".join(unit.text for unit in merged_units),
            utterances=utterances,
            sentences=sentences,
        )


if __name__ == "__main__":
    unittest.main()
