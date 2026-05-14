"""Tests for normalized audio artifact generation and reuse."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.errors import AudioValidationError
from lecture_analyzer.core.models import InputSource
from lecture_analyzer.core.types import MediaType
from lecture_analyzer.preprocessing.audio_normalizer import (
    AudioNormalizer,
    ProbedAudioInfo,
)


class _TestAudioNormalizer(AudioNormalizer):
    """Audio normalizer test double with replaceable conversion hooks."""

    def __init__(
        self,
        config: PipelineConfig,
        probe_results: list[ProbedAudioInfo] | None = None,
    ) -> None:
        super().__init__(config)
        self.convert_calls = 0
        self.probe_calls = 0
        self.probe_results = probe_results or []

    def _convert_to_normalized_audio(
        self,
        source_path: Path,
        output_path: Path,
    ) -> None:
        """Create a placeholder artifact instead of calling ffmpeg."""

        self.convert_calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"normalized-audio")

    def _probe_audio_file(self, audio_path: Path) -> ProbedAudioInfo:
        """Return queued probe data instead of calling ffprobe."""

        self.probe_calls += 1
        if self.probe_results:
            return self.probe_results.pop(0)
        return ProbedAudioInfo(
            codec_name=self._resolve_audio_codec(),
            sample_rate=self.config.normalized_audio_sample_rate,
            channels=self.config.normalized_audio_channels,
            bit_depth=self.config.normalized_audio_bit_depth,
            duration_seconds=12.5,
        )


class AudioNormalizerTests(unittest.TestCase):
    """Verify deterministic normalization and cache reuse behavior."""

    def test_normalize_source_creates_metadata_for_processing_asset(self) -> None:
        """A new normalization should create both audio and sidecar metadata."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            normalizer = _TestAudioNormalizer(config)
            source_path = Path(temp_directory) / "Lesson 01.mp4"
            source_path.write_bytes(b"video")

            audio_source = normalizer.normalize_source(
                self._build_input_source(source_path, media_type=MediaType.VIDEO),
            )

            self.assertEqual(normalizer.convert_calls, 1)
            self.assertEqual(
                audio_source.audio_path.name,
                "001_lesson_01_mono_16000hz.wav",
            )
            self.assertTrue(audio_source.audio_path.exists())
            self.assertEqual(audio_source.audio_format, "wav")
            self.assertTrue(audio_source.extracted_from_video)
            self.assertFalse(
                audio_source.metadata["normalization"]["cache_hit"],
            )

            metadata_path = Path(
                audio_source.metadata["normalized_audio_metadata_path"],
            )
            self.assertTrue(metadata_path.exists())

            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_filename"], "Lesson 01.mp4")
            self.assertEqual(payload["output_format"], "wav")
            self.assertEqual(payload["sample_rate"], 16000)
            self.assertEqual(payload["channels"], 1)
            self.assertEqual(payload["bit_depth"], 16)

    def test_normalize_source_reuses_cached_artifact_when_source_is_unchanged(self) -> None:
        """A valid sidecar and artifact should skip regeneration."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            source_path = Path(temp_directory) / "lesson.mp3"
            source_path.write_bytes(b"audio")

            first_normalizer = _TestAudioNormalizer(config)
            first_audio_source = first_normalizer.normalize_source(
                self._build_input_source(source_path),
            )
            self.assertEqual(first_normalizer.convert_calls, 1)

            second_normalizer = _TestAudioNormalizer(config)
            cached_audio_source = second_normalizer.normalize_source(
                self._build_input_source(source_path),
            )

            self.assertEqual(second_normalizer.convert_calls, 0)
            self.assertEqual(first_audio_source.audio_path, cached_audio_source.audio_path)
            self.assertTrue(
                cached_audio_source.metadata["normalization"]["cache_hit"],
            )

    def test_normalize_source_regenerates_when_source_metadata_changes(self) -> None:
        """A source mtime change should invalidate the cached derivative."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            source_path = Path(temp_directory) / "lesson.mp3"
            source_path.write_bytes(b"audio-v1")

            first_normalizer = _TestAudioNormalizer(config)
            first_normalizer.normalize_source(self._build_input_source(source_path))
            self.assertEqual(first_normalizer.convert_calls, 1)

            source_path.write_bytes(b"audio-v2")

            second_normalizer = _TestAudioNormalizer(config)
            refreshed_audio_source = second_normalizer.normalize_source(
                self._build_input_source(source_path),
            )

            self.assertEqual(second_normalizer.convert_calls, 1)
            self.assertFalse(
                refreshed_audio_source.metadata["normalization"]["cache_hit"],
            )

    def test_normalize_source_raises_when_output_is_not_in_expected_format(self) -> None:
        """An invalid normalized artifact should fail validation cleanly."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(working_directory=Path(temp_directory) / "artifacts")
            source_path = Path(temp_directory) / "lesson.mp3"
            source_path.write_bytes(b"audio")

            normalizer = _TestAudioNormalizer(
                config,
                probe_results=[
                    ProbedAudioInfo(
                        codec_name="pcm_s16le",
                        sample_rate=8000,
                        channels=1,
                        bit_depth=16,
                        duration_seconds=3.0,
                    ),
                ],
            )

            with self.assertRaises(AudioValidationError):
                normalizer.normalize_source(self._build_input_source(source_path))

    @staticmethod
    def _build_input_source(
        source_path: Path,
        media_type: MediaType = MediaType.AUDIO,
    ) -> InputSource:
        """Create a deterministic input source for tests."""

        return InputSource(
            source_id="source_001",
            original_path=source_path,
            media_type=media_type,
            order_index=1,
            original_filename=source_path.name,
        )
