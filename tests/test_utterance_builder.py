"""Tests for the alignment-derived utterance layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.analysis.utterance_builder import UtteranceBuilder
from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AlignedTranscript,
    AlignedTranscriptSegment,
    AlignedWord,
    AudioSource,
    LectureSession,
)
from lecture_analyzer.core.pipeline import LectureProcessingPipeline


class UtteranceBuilderTests(unittest.TestCase):
    """Exercise utterance construction, persistence, and fallback behavior."""

    def test_build_source_splits_segment_on_large_word_gap(self) -> None:
        """Aligned word gaps should create multiple utterances within one segment."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                utterance_max_gap_seconds=0.7,
            )
            builder = UtteranceBuilder(config)
            audio_source = self._build_audio_source(
                media_path,
                session_offset_seconds=10.0,
            )
            aligned_transcript = self._build_aligned_transcript(
                audio_source_id=audio_source.audio_source_id,
                media_path=media_path,
            )

            utterance_collection = builder.build_source(audio_source, aligned_transcript)

            self.assertEqual(len(utterance_collection.utterances), 2)
            self.assertEqual(
                utterance_collection.utterances[0].text,
                "Hello there",
            )
            self.assertEqual(
                utterance_collection.utterances[1].text,
                "General Kenobi",
            )
            self.assertEqual(
                utterance_collection.utterances[0].aligned_segment_id,
                f"{audio_source.audio_source_id}_aligned_segment_0001",
            )
            self.assertTrue(
                utterance_collection.utterances[0].utterance_id.startswith(
                    f"{audio_source.audio_source_id}_utterance_",
                ),
            )
            self.assertEqual(
                utterance_collection.utterances[0].start_word_index,
                1,
            )
            self.assertEqual(
                utterance_collection.utterances[0].end_word_index,
                2,
            )
            self.assertEqual(
                utterance_collection.utterances[0].source_word_ids,
                [
                    f"{audio_source.audio_source_id}_aligned_word_0001_0001",
                    f"{audio_source.audio_source_id}_aligned_word_0001_0002",
                ],
            )
            self.assertEqual(
                utterance_collection.utterances[0].session_start_seconds,
                10.0,
            )
            self.assertEqual(
                utterance_collection.utterances[0].session_end_seconds,
                10.5,
            )

            session = LectureSession(
                session_id="session_001",
                audio_sources=[audio_source],
                aligned_transcripts=[aligned_transcript],
                utterances=utterance_collection.utterances,
            )
            payload = session.to_dict()
            self.assertEqual(payload["transcript"]["utterance_count"], 2)
            self.assertEqual(len(payload["utterances"]), 2)

    def test_cached_utterances_rebind_current_source_ids(self) -> None:
        """A cached utterance artifact should remain reusable across source ids."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                utterance_max_gap_seconds=0.7,
            )
            builder = UtteranceBuilder(config)

            first_source = self._build_audio_source(media_path)
            first_aligned_transcript = self._build_aligned_transcript(
                audio_source_id=first_source.audio_source_id,
                media_path=media_path,
            )
            first_collection = builder.build_source(first_source, first_aligned_transcript)
            builder.cache_store.save_utterances(
                audio_source=first_source,
                aligned_transcript=first_aligned_transcript,
                utterance_collection=first_collection,
            )

            second_source = self._build_audio_source(
                media_path,
                audio_source_id="audio_source_099",
                session_offset_seconds=5.0,
            )
            second_aligned_transcript = self._build_aligned_transcript(
                audio_source_id=second_source.audio_source_id,
                media_path=media_path,
            )

            cached_utterances = builder.cache_store.load_utterances(
                audio_source=second_source,
                aligned_transcript=second_aligned_transcript,
            )

            self.assertIsNotNone(cached_utterances)
            assert cached_utterances is not None
            self.assertEqual(
                cached_utterances.utterance_collection.audio_source_id,
                "audio_source_099",
            )
            self.assertEqual(
                cached_utterances.utterance_collection.utterances[0].audio_source_id,
                "audio_source_099",
            )
            self.assertTrue(
                cached_utterances.utterance_collection.utterances[0].utterance_id.startswith(
                    "audio_source_099_utterance_",
                ),
            )
            self.assertEqual(
                cached_utterances.utterance_collection.utterances[0].aligned_segment_id,
                "audio_source_099_aligned_segment_0001",
            )
            self.assertEqual(
                cached_utterances.utterance_collection.utterances[0].transcript_chunk_id,
                "audio_source_099_chunk_0001",
            )
            self.assertEqual(
                cached_utterances.utterance_collection.utterances[0].source_word_ids,
                [
                    "audio_source_099_aligned_word_0001_0001",
                    "audio_source_099_aligned_word_0001_0002",
                ],
            )
            self.assertEqual(
                cached_utterances.utterance_collection.utterances[0].session_start_seconds,
                5.0,
            )

    def test_pipeline_marks_utterances_as_skipped_without_alignment(self) -> None:
        """The pipeline should expose a clear fallback when alignment is missing."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            pipeline = LectureProcessingPipeline(config)
            audio_source = self._build_audio_source(media_path)
            session = LectureSession(
                session_id="session_001",
                audio_sources=[audio_source],
            )

            pipeline.build_utterances(session)

            self.assertEqual(session.utterances, [])
            self.assertEqual(session.metadata["utterance_build_status"], "skipped")
            self.assertEqual(
                session.metadata["utterance_build_reason"],
                "aligned_transcripts_unavailable",
            )
            self.assertEqual(
                audio_source.metadata["utterances"]["status"],
                "skipped",
            )

    @staticmethod
    def _build_audio_source(
        media_path: Path,
        audio_source_id: str = "audio_source_001",
        session_offset_seconds: float | None = None,
    ) -> AudioSource:
        """Build an audio source linked to one original media file."""

        return AudioSource(
            audio_source_id=audio_source_id,
            input_source_id="source_001",
            audio_path=media_path,
            audio_format=media_path.suffix.lstrip("."),
            duration_seconds=4.0,
            order_index=1,
            session_offset_seconds=session_offset_seconds,
            metadata={"original_path": str(media_path)},
        )

    @staticmethod
    def _build_aligned_transcript(
        audio_source_id: str,
        media_path: Path,
    ) -> AlignedTranscript:
        """Build one aligned transcript with a deterministic large internal gap."""

        return AlignedTranscript(
            audio_source_id=audio_source_id,
            source_audio_path=media_path,
            detected_language="en",
            source_chunk_count=1,
            segments=[
                AlignedTranscriptSegment(
                    segment_id=f"{audio_source_id}_aligned_segment_0001",
                    audio_source_id=audio_source_id,
                    transcript_chunk_id=f"{audio_source_id}_chunk_0001",
                    start_seconds=0.0,
                    end_seconds=2.3,
                    text="Hello there General Kenobi",
                    detected_language="en",
                    words=[
                        AlignedWord(
                            word_id=f"{audio_source_id}_aligned_word_0001_0001",
                            text="Hello",
                            start_seconds=0.0,
                            end_seconds=0.2,
                        ),
                        AlignedWord(
                            word_id=f"{audio_source_id}_aligned_word_0001_0002",
                            text="there",
                            start_seconds=0.25,
                            end_seconds=0.5,
                        ),
                        AlignedWord(
                            word_id=f"{audio_source_id}_aligned_word_0001_0003",
                            text="General",
                            start_seconds=1.6,
                            end_seconds=1.9,
                        ),
                        AlignedWord(
                            word_id=f"{audio_source_id}_aligned_word_0001_0004",
                            text="Kenobi",
                            start_seconds=1.95,
                            end_seconds=2.3,
                        ),
                    ],
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
