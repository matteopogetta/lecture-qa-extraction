"""Tests for the WhisperX alignment refinement layer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import PipelineConfig
from core.models import AudioSource, LectureSession, TranscriptChunk
from transcription.cache_store import TranscriptionCacheStore
from transcription.whisperx_aligner import (
    AlignmentUnavailableError,
    WhisperXAligner,
)


class _StubWhisperXAligner(WhisperXAligner):
    """Test double that bypasses the real WhisperX dependency."""

    def __init__(
        self,
        config: PipelineConfig,
        payload: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(config)
        self.payload = payload or {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.8,
                    "text": "Aligned segment",
                    "words": [
                        {
                            "word": "Aligned",
                            "start": 0.0,
                            "end": 0.7,
                            "score": 0.96,
                        },
                        {
                            "word": "segment",
                            "start": 0.8,
                            "end": 1.8,
                            "score": 0.93,
                        },
                    ],
                },
            ],
        }
        self.error = error
        self.calls = 0

    def _align_with_whisperx(
        self,
        audio_path: Path,
        transcript_chunks: list[TranscriptChunk],
        language: str,
    ) -> dict[str, object]:
        """Return a predefined alignment payload instead of calling WhisperX."""

        del audio_path, transcript_chunks, language
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


class WhisperXAlignerTests(unittest.TestCase):
    """Exercise alignment persistence, reuse, and fallback behavior."""

    def test_align_source_persists_artifact_and_updates_metadata(self) -> None:
        """A fresh alignment should create a reusable JSON artifact."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            aligner = _StubWhisperXAligner(config)
            audio_source = self._build_audio_source(media_path)
            transcript_chunks = self._build_transcript_chunks(audio_source.audio_source_id)

            aligned_transcript = aligner.align_source(audio_source, transcript_chunks)

            self.assertEqual(aligner.calls, 1)
            self.assertEqual(len(aligned_transcript.segments), 1)
            self.assertEqual(len(aligned_transcript.segments[0].words), 2)
            self.assertEqual(
                audio_source.metadata["alignment"]["status"],
                "available",
            )
            self.assertFalse(audio_source.metadata["alignment"]["cache_hit"])

            artifact_path = Path(
                audio_source.metadata["alignment"]["artifact_manifest_path"],
            )
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact_payload["artifact_type"], "whisperx_alignment")
            self.assertEqual(artifact_payload["transcript_reference"]["chunk_count"], 1)

    def test_align_source_reuses_cached_artifact_and_rebinds_current_ids(self) -> None:
        """A compatible alignment artifact should be reusable across source ids."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            first_aligner = _StubWhisperXAligner(config)
            first_source = self._build_audio_source(media_path)
            first_chunks = self._build_transcript_chunks(first_source.audio_source_id)
            first_aligner.align_source(first_source, first_chunks)

            second_source = self._build_audio_source(
                media_path,
                audio_source_id="audio_source_099",
            )
            second_chunks = self._build_transcript_chunks(second_source.audio_source_id)
            cached_aligner = _StubWhisperXAligner(
                config,
                error=AssertionError("The cached artifact should avoid runtime alignment."),
            )

            aligned_transcript = cached_aligner.align_source(second_source, second_chunks)

            self.assertEqual(cached_aligner.calls, 0)
            self.assertEqual(aligned_transcript.audio_source_id, "audio_source_099")
            self.assertEqual(
                aligned_transcript.segments[0].transcript_chunk_id,
                "audio_source_099_chunk_0001",
            )
            self.assertTrue(second_source.metadata["alignment"]["cache_hit"])

    def test_align_session_falls_back_cleanly_on_manageable_failure(self) -> None:
        """Alignment failures should not remove the transcript or crash the session."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            aligner = _StubWhisperXAligner(
                config,
                error=AlignmentUnavailableError("WhisperX missing"),
            )
            audio_source = self._build_audio_source(media_path)
            transcript_chunks = self._build_transcript_chunks(audio_source.audio_source_id)
            session = LectureSession(
                session_id="session_001",
                audio_sources=[audio_source],
                transcript_chunks=transcript_chunks,
            )

            aligned_transcripts = aligner.align_session(session)

            self.assertEqual(aligned_transcripts, [])
            self.assertEqual(session.aligned_transcripts, [])
            self.assertEqual(session.metadata["transcript_alignment_status"], "failed")
            self.assertEqual(audio_source.metadata["alignment"]["status"], "failed")
            self.assertEqual(transcript_chunks[0].text, "Aligned segment")

    def test_alignment_cache_uses_deterministic_path_layout(self) -> None:
        """Alignment artifacts should resolve under the dedicated workdir layer."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            store = TranscriptionCacheStore(config)
            audio_source = self._build_audio_source(media_path)

            paths = store.resolve_alignment_paths(audio_source)

            self.assertEqual(paths.manifest_path.suffixes[-2:], [".alignment", ".json"])
            self.assertIn(
                str(config.alignment_artifacts_directory),
                str(paths.manifest_path),
            )

    @staticmethod
    def _build_audio_source(
        media_path: Path,
        audio_source_id: str = "audio_source_001",
    ) -> AudioSource:
        """Build an audio source linked to one original media file."""

        return AudioSource(
            audio_source_id=audio_source_id,
            input_source_id="source_001",
            audio_path=media_path,
            audio_format=media_path.suffix.lstrip("."),
            duration_seconds=4.0,
            order_index=1,
            metadata={"original_path": str(media_path)},
        )

    @staticmethod
    def _build_transcript_chunks(audio_source_id: str) -> list[TranscriptChunk]:
        """Build one transcript chunk compatible with the stub alignment payload."""

        return [
            TranscriptChunk(
                chunk_id=f"{audio_source_id}_chunk_0001",
                audio_source_id=audio_source_id,
                start_seconds=0.0,
                end_seconds=1.8,
                text="Aligned segment",
                detected_language="en",
            ),
        ]


if __name__ == "__main__":
    unittest.main()
