"""Tests for reusable transcription cache behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.config import PipelineConfig
from core.models import AudioSource
from transcription.backend import BackendSegment
from transcription.transcriber import Transcriber


class _FakeBackend:
    """Minimal backend stub used for deterministic transcription tests."""

    def __init__(
        self,
        segments: list[BackendSegment] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.segments = segments or []
        self.metadata = metadata or {}
        self.calls = 0

    def transcribe(
        self,
        audio_path: Path,
    ) -> tuple[list[BackendSegment], dict[str, object]]:
        """Return predefined transcription data and count invocations."""

        self.calls += 1
        return self.segments, self.metadata


class _FailingBackend:
    """Backend stub that fails when transcription should have been skipped."""

    def transcribe(
        self,
        audio_path: Path,
    ) -> tuple[list[BackendSegment], dict[str, object]]:
        """Raise immediately because the cache should satisfy the request."""

        raise AssertionError(f"Unexpected backend call for '{audio_path}'.")


class TranscriptionCacheTests(unittest.TestCase):
    """Exercise save and reload behavior for transcript caches."""

    def test_transcribe_source_saves_text_and_manifest_next_to_media(self) -> None:
        """A fresh transcription should create both cache artifacts."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.mp3"
            media_path.write_bytes(b"placeholder")
            audio_source = self._build_audio_source(media_path)

            backend = _FakeBackend(
                segments=[
                    BackendSegment(
                        start_seconds=0.0,
                        end_seconds=1.5,
                        text="First line",
                    ),
                    BackendSegment(
                        start_seconds=1.5,
                        end_seconds=3.0,
                        text="Second line",
                    ),
                ],
                metadata={
                    "backend": "fake-backend",
                    "detected_language": "it",
                    "language_confidence": 0.91,
                },
            )
            transcriber = self._build_transcriber(temp_directory, backend)

            chunks = transcriber.transcribe_source(audio_source)

            # Assert both the immediate return value and the reusable cache
            # artifacts because both are part of the cache contract.
            self.assertEqual(backend.calls, 1)
            self.assertEqual(len(chunks), 2)
            self.assertEqual(
                media_path.with_suffix(".txt").read_text(encoding="utf-8"),
                "First line\nSecond line",
            )

            manifest_path = media_path.with_suffix(".transcription.json")
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["cache_version"], 1)
            self.assertEqual(
                manifest_payload["transcript_text"],
                "First line\nSecond line",
            )
            self.assertEqual(len(manifest_payload["chunks"]), 2)
            self.assertFalse(audio_source.metadata["transcription"]["cache_hit"])

    def test_transcribe_source_reuses_manifest_cache_without_backend(self) -> None:
        """A manifest cache hit should rebuild chunks for the current source."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.mp3"
            media_path.write_bytes(b"placeholder")

            backend = _FakeBackend(
                segments=[
                    BackendSegment(
                        start_seconds=1.0,
                        end_seconds=3.0,
                        text="Reusable line",
                    ),
                ],
                metadata={
                    "backend": "fake-backend",
                    "detected_language": "en",
                },
            )
            first_transcriber = self._build_transcriber(temp_directory, backend)
            first_audio_source = self._build_audio_source(media_path)
            first_transcriber.transcribe_source(first_audio_source)
            self.assertEqual(backend.calls, 1)

            cached_audio_source = self._build_audio_source(
                media_path,
                audio_source_id="audio_source_099",
                session_offset_seconds=10.0,
            )
            second_transcriber = self._build_transcriber(
                temp_directory,
                _FailingBackend(),
            )

            cached_chunks = second_transcriber.transcribe_source(cached_audio_source)

            # Cache hits must rebuild chunk identities and session-relative
            # timing for the current audio source rather than replaying them verbatim.
            self.assertEqual(len(cached_chunks), 1)
            self.assertEqual(
                cached_chunks[0].chunk_id,
                "audio_source_099_chunk_0001",
            )
            self.assertEqual(
                cached_chunks[0].audio_source_id,
                "audio_source_099",
            )
            self.assertEqual(cached_chunks[0].session_start_seconds, 11.0)
            self.assertTrue(
                cached_audio_source.metadata["transcription"]["cache_hit"],
            )
            self.assertEqual(
                cached_audio_source.metadata["transcription"]["cache_format"],
                "manifest",
            )
            self.assertTrue(
                cached_audio_source.metadata["transcription"][
                    "cache_lookup_performed"
                ],
            )
            self.assertFalse(
                cached_audio_source.metadata["transcription"][
                    "transcription_recomputed"
                ],
            )

    def test_transcribe_source_uses_text_only_cache_when_manifest_is_missing(self) -> None:
        """A plain-text cache should still avoid a backend call."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.mp3"
            media_path.write_bytes(b"placeholder")
            media_path.with_suffix(".txt").write_text(
                "Recovered transcript",
                encoding="utf-8",
            )

            audio_source = self._build_audio_source(
                media_path,
                duration_seconds=42.0,
                session_offset_seconds=3.0,
            )
            transcriber = self._build_transcriber(
                temp_directory,
                _FailingBackend(),
            )

            chunks = transcriber.transcribe_source(audio_source)

            # Text-only fallback keeps the pipeline operational even without a
            # manifest, but timing becomes synthetic by design.
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].text, "Recovered transcript")
            self.assertEqual(chunks[0].start_seconds, 0.0)
            self.assertEqual(chunks[0].end_seconds, 42.0)
            self.assertEqual(chunks[0].session_start_seconds, 3.0)
            self.assertEqual(chunks[0].session_end_seconds, 45.0)
            self.assertEqual(
                audio_source.metadata["transcription"]["cache_format"],
                "text_only",
            )

    def test_transcribe_source_from_scratch_ignores_cache_and_overwrites_outputs(self) -> None:
        """Force recompute should bypass cache reuse while still saving fresh outputs."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.mp3"
            media_path.write_bytes(b"placeholder")

            first_backend = _FakeBackend(
                segments=[
                    BackendSegment(
                        start_seconds=0.0,
                        end_seconds=1.0,
                        text="Cached transcript",
                    ),
                ],
                metadata={"backend": "fake-backend", "detected_language": "it"},
            )
            first_transcriber = self._build_transcriber(temp_directory, first_backend)
            first_audio_source = self._build_audio_source(media_path)
            first_transcriber.transcribe_source(first_audio_source)

            forced_backend = _FakeBackend(
                segments=[
                    BackendSegment(
                        start_seconds=0.0,
                        end_seconds=2.0,
                        text="Fresh transcript",
                    ),
                ],
                metadata={"backend": "fake-backend", "detected_language": "en"},
            )
            forced_transcriber = self._build_transcriber(
                temp_directory,
                forced_backend,
                force_recompute=True,
            )
            forced_audio_source = self._build_audio_source(media_path)

            chunks = forced_transcriber.transcribe_source(forced_audio_source)

            self.assertEqual(forced_backend.calls, 1)
            self.assertEqual(chunks[0].text, "Fresh transcript")
            self.assertFalse(forced_audio_source.metadata["transcription"]["cache_hit"])
            self.assertTrue(
                forced_audio_source.metadata["transcription"][
                    "cache_artifact_found"
                ],
            )
            self.assertTrue(
                forced_audio_source.metadata["transcription"][
                    "cache_ignored_due_to_force_recompute"
                ],
            )
            self.assertTrue(
                forced_audio_source.metadata["transcription"][
                    "transcription_recomputed"
                ],
            )
            self.assertTrue(
                forced_audio_source.metadata["transcription"][
                    "transcription_forced_recompute"
                ],
            )
            self.assertFalse(
                forced_audio_source.metadata["transcription"][
                    "cache_lookup_performed"
                ],
            )
            self.assertEqual(
                media_path.with_suffix(".txt").read_text(encoding="utf-8"),
                "Fresh transcript",
            )
            manifest_payload = json.loads(
                media_path.with_suffix(".transcription.json").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(manifest_payload["transcript_text"], "Fresh transcript")

    def _build_transcriber(
        self,
        temp_directory: str,
        backend: object,
        *,
        force_recompute: bool = False,
    ) -> Transcriber:
        """Create a transcriber with a replaceable backend stub."""

        config = PipelineConfig(
            working_directory=Path(temp_directory) / "artifacts",
            force_recompute=force_recompute,
        )
        transcriber = Transcriber(config)
        transcriber.backend = backend
        return transcriber

    @staticmethod
    def _build_audio_source(
        media_path: Path,
        audio_source_id: str = "audio_source_001",
        duration_seconds: float | None = None,
        session_offset_seconds: float | None = None,
    ) -> AudioSource:
        """Build an audio source linked to one original media file."""

        return AudioSource(
            audio_source_id=audio_source_id,
            input_source_id="source_001",
            audio_path=media_path,
            audio_format=media_path.suffix.lstrip("."),
            duration_seconds=duration_seconds,
            order_index=1,
            session_offset_seconds=session_offset_seconds,
            metadata={
                "original_path": str(media_path),
            },
        )
