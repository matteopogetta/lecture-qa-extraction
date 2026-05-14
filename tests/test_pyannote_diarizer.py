"""Tests for the pyannote diarization layer."""

from __future__ import annotations

import json
import os
import pickle
import tempfile
import unittest
from pathlib import Path
import wave
import warnings
from unittest.mock import patch

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import AudioSource, LectureSession
from lecture_analyzer.transcription.cache_store import TranscriptionCacheStore
from lecture_analyzer.transcription.pyannote_diarizer import (
    DiarizationUnavailableError,
    PyannoteDiarizer,
)


class _FakeTimeSegment:
    """Small pyannote-like time segment test double."""

    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _FakeAnnotation:
    """Small pyannote-like annotation test double."""

    def __init__(self, tracks: list[tuple[float, float, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        """Yield pyannote-like track tuples."""

        for index, (start_seconds, end_seconds, speaker_label) in enumerate(
            self._tracks,
            start=1,
        ):
            item = (_FakeTimeSegment(start_seconds, end_seconds), index)
            if yield_label:
                yield item[0], item[1], speaker_label
            else:
                yield item


class _FakeDiarizeOutput:
    """Small pyannote 4.x-like diarization output test double."""

    def __init__(
        self,
        tracks: list[tuple[float, float, str]],
        exclusive_tracks: list[tuple[float, float, str]] | None = None,
    ) -> None:
        self.speaker_diarization = _FakeAnnotation(tracks)
        if exclusive_tracks is not None:
            self.exclusive_speaker_diarization = _FakeAnnotation(exclusive_tracks)


class _PipelineAcceptsToken:
    """Small pipeline stub that accepts the modern `token` keyword."""

    calls: list[tuple[str, dict[str, str | None]]] = []

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs):
        """Record the call and return a sentinel object."""

        cls.calls.append((model_name, kwargs))
        return {"model_name": model_name, "kwargs": kwargs}


class _PipelineAcceptsLegacyToken:
    """Small pipeline stub that only accepts the legacy token keyword."""

    calls: list[tuple[str, dict[str, str | None]]] = []

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs):
        """Raise on modern token usage and accept the legacy variant."""

        if "token" in kwargs:
            raise TypeError("Unexpected keyword argument 'token'")
        cls.calls.append((model_name, kwargs))
        return {"model_name": model_name, "kwargs": kwargs}


class _PipelineReturnsNone:
    """Small pipeline stub that mimics a failed gated-model load."""

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs):
        """Return no pipeline object, like a failed loader path can do."""

        del model_name, kwargs
        return None


class _PipelineRetriesWhenWeightsOnlyBreaks:
    """Small pipeline stub that succeeds after disabling weights-only loads."""

    calls: list[str | None] = []

    @classmethod
    def from_pretrained(cls, model_name: str, **kwargs):
        """Fail first, then succeed when the compatibility env var is active."""

        del model_name, kwargs
        cls.calls.append(os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"))
        if os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD") == "1":
            return _PipelineWithDevice()
        raise pickle.UnpicklingError(
            "Weights only load failed. Please use add_safe_globals if trusted.",
        )


class _PipelineWithDevice:
    """Small pipeline stub exposing the `to` method expected by the loader."""

    def to(self, device: str):
        """Pretend moving the pipeline to the requested device succeeded."""

        del device
        return self


class _StubPyannoteDiarizer(PyannoteDiarizer):
    """Test double that bypasses the real pyannote dependency."""

    def __init__(
        self,
        config: PipelineConfig,
        tracks: list[tuple[float, float, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(config)
        self.tracks = tracks or [
            (0.0, 0.9, "A"),
            (0.9, 1.8, "B"),
        ]
        self.error = error
        self.calls = 0

    def _run_with_pyannote(self, audio_path: Path) -> _FakeAnnotation:
        """Return predefined diarization data instead of calling pyannote."""

        del audio_path
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _FakeAnnotation(self.tracks)


class _StubPyannoteDiarizerV4(_StubPyannoteDiarizer):
    """Test double returning the pyannote 4.x `DiarizeOutput` shape."""

    def __init__(
        self,
        config: PipelineConfig,
        tracks: list[tuple[float, float, str]] | None = None,
        exclusive_tracks: list[tuple[float, float, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(config, tracks=tracks, error=error)
        self.exclusive_tracks = exclusive_tracks

    def _run_with_pyannote(self, audio_path: Path) -> _FakeDiarizeOutput:
        """Return predefined pyannote 4.x-style diarization output."""

        del audio_path
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _FakeDiarizeOutput(
            self.tracks,
            exclusive_tracks=self.exclusive_tracks,
        )


class PyannoteDiarizerTests(unittest.TestCase):
    """Exercise diarization persistence, reuse, and fallback behavior."""

    def test_install_pyannote_warning_filter_ignores_only_decoder_noise(self) -> None:
        """The targeted filter should hide only the known torchcodec warning."""

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            PyannoteDiarizer._install_pyannote_warning_filter()
            warnings.warn_explicit(
                "\ntorchcodec is not installed correctly so built-in audio decoding "
                "will fail. fallback details",
                UserWarning,
                filename="pyannote/audio/core/io.py",
                lineno=47,
                module="pyannote.audio.core.io",
            )
            warnings.warn_explicit(
                "different pyannote warning",
                UserWarning,
                filename="pyannote/audio/core/io.py",
                lineno=99,
                module="pyannote.audio.core.io",
            )

        self.assertEqual(len(caught), 1)
        self.assertEqual(str(caught[0].message), "different pyannote warning")

    def test_diarize_source_persists_artifact_and_updates_metadata(self) -> None:
        """A fresh diarization should create a reusable JSON artifact."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            diarizer = _StubPyannoteDiarizer(config)
            audio_source = self._build_audio_source(media_path)

            diarization_result = diarizer.diarize_source(audio_source)

            self.assertEqual(diarizer.calls, 1)
            self.assertEqual(len(diarization_result.segments), 2)
            self.assertEqual(diarization_result.segments[0].speaker_id, "SPEAKER_00")
            self.assertEqual(
                audio_source.metadata["diarization"]["status"],
                "available",
            )
            self.assertFalse(audio_source.metadata["diarization"]["cache_hit"])

            artifact_path = Path(
                audio_source.metadata["diarization"]["artifact_manifest_path"],
            )
            self.assertTrue(artifact_path.exists())
            artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact_payload["artifact_type"], "diarization")
            self.assertEqual(
                artifact_payload["diarization_result"]["speaker_ids"],
                ["SPEAKER_00", "SPEAKER_01"],
            )

    def test_diarize_source_accepts_pyannote_v4_diarize_output(self) -> None:
        """The adapter should accept pyannote 4.x `DiarizeOutput` objects."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            diarizer = _StubPyannoteDiarizerV4(config)
            audio_source = self._build_audio_source(media_path)

            diarization_result = diarizer.diarize_source(audio_source)

            self.assertEqual(diarizer.calls, 1)
            self.assertEqual(len(diarization_result.segments), 2)
            self.assertEqual(diarization_result.segments[0].speaker_id, "SPEAKER_00")
            self.assertEqual(diarization_result.preferred_segment_source, "regular")
            self.assertEqual(
                diarization_result.segments[0].segment_source,
                "regular",
            )
            self.assertEqual(
                diarization_result.metadata["track_container_type"],
                "_FakeAnnotation",
            )

    def test_diarize_source_prefers_exclusive_segments_when_available(self) -> None:
        """Exclusive diarization should be preferred when the output exposes it."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
                diarization_prefer_exclusive=True,
            )
            diarizer = _StubPyannoteDiarizerV4(
                config,
                tracks=[(0.0, 0.5, "A"), (0.5, 1.0, "B")],
                exclusive_tracks=[(0.0, 1.0, "A")],
            )
            audio_source = self._build_audio_source(media_path)

            diarization_result = diarizer.diarize_source(audio_source)

            self.assertEqual(diarization_result.preferred_segment_source, "exclusive")
            self.assertEqual(diarization_result.available_segment_sources, ["exclusive", "regular"])
            self.assertEqual(len(diarization_result.segments), 1)
            self.assertEqual(diarization_result.segments[0].segment_source, "exclusive")

    def test_run_with_pyannote_prefers_preloaded_waveform_input(self) -> None:
        """The diarizer should avoid fragile runtime decoders for WAV inputs."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            self._write_test_wave(media_path)

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            diarizer = PyannoteDiarizer(config)

            captured_calls: list[tuple[object, dict[str, int]]] = []

            def fake_pipeline(input_value: object, **kwargs: int) -> _FakeAnnotation:
                captured_calls.append((input_value, kwargs))
                return _FakeAnnotation([(0.0, 1.0, "A")])

            diarizer._get_pipeline = lambda: fake_pipeline  # type: ignore[method-assign]

            fake_torch = type(
                "_FakeTorch",
                (),
                {"from_numpy": staticmethod(lambda array: array)},
            )
            with patch.dict("sys.modules", {"torch": fake_torch}):
                result = diarizer._run_with_pyannote(media_path)

            self.assertIsInstance(result, _FakeAnnotation)
            self.assertEqual(len(captured_calls), 1)
            pipeline_input, kwargs = captured_calls[0]
            self.assertIsInstance(pipeline_input, dict)
            self.assertIn("waveform", pipeline_input)
            self.assertEqual(pipeline_input["sample_rate"], 16000)
            self.assertEqual(kwargs, {})

    def test_diarize_source_reuses_cached_artifact_and_rebinds_current_ids(self) -> None:
        """A compatible diarization artifact should be reusable across source ids."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            first_diarizer = _StubPyannoteDiarizer(config)
            first_source = self._build_audio_source(media_path)
            first_diarizer.diarize_source(first_source)

            second_source = self._build_audio_source(
                media_path,
                audio_source_id="audio_source_099",
                session_offset_seconds=5.0,
            )
            cached_diarizer = _StubPyannoteDiarizer(
                config,
                error=AssertionError("The cached artifact should avoid runtime diarization."),
            )

            diarization_result = cached_diarizer.diarize_source(second_source)

            self.assertEqual(cached_diarizer.calls, 0)
            self.assertEqual(diarization_result.audio_source_id, "audio_source_099")
            self.assertEqual(
                diarization_result.segments[0].audio_source_id,
                "audio_source_099",
            )
            self.assertEqual(
                diarization_result.segments[0].diarization_segment_id,
                "audio_source_099_diarization_segment_0001",
            )
            self.assertEqual(
                diarization_result.segments[0].session_start_seconds,
                5.0,
            )
            self.assertTrue(second_source.metadata["diarization"]["cache_hit"])

    def test_diarize_session_falls_back_cleanly_on_manageable_failure(self) -> None:
        """Diarization failures should not crash the session."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            diarizer = _StubPyannoteDiarizer(
                config,
                error=DiarizationUnavailableError("pyannote missing"),
            )
            audio_source = self._build_audio_source(media_path)
            session = LectureSession(
                session_id="session_001",
                audio_sources=[audio_source],
            )

            diarization_segments = diarizer.diarize_session(session)

            self.assertEqual(diarization_segments, [])
            self.assertEqual(session.diarization_segments, [])
            self.assertEqual(session.metadata["diarization_status"], "failed")
            self.assertEqual(audio_source.metadata["diarization"]["status"], "failed")

    def test_diarization_cache_uses_deterministic_path_layout(self) -> None:
        """Diarization artifacts should resolve under the dedicated workdir layer."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                diarization_enabled=True,
            )
            store = TranscriptionCacheStore(config)
            audio_source = self._build_audio_source(media_path)

            paths = store.resolve_diarization_paths(audio_source)

            self.assertEqual(paths.manifest_path.suffixes[-2:], [".diarization", ".json"])
            self.assertIn(
                str(config.diarization_artifacts_directory),
                str(paths.manifest_path),
            )

    def test_load_pipeline_prefers_modern_token_keyword(self) -> None:
        """The loader should support the pyannote 4.x `token` keyword."""

        _PipelineAcceptsToken.calls = []

        pipeline = PyannoteDiarizer._load_pipeline(
            pipeline_class=_PipelineAcceptsToken,
            model_name="pyannote/speaker-diarization-3.1",
            token="hf_test",
        )

        self.assertEqual(
            _PipelineAcceptsToken.calls,
            [
                (
                    "pyannote/speaker-diarization-3.1",
                    {"token": "hf_test"},
                ),
            ],
        )
        self.assertEqual(pipeline["kwargs"]["token"], "hf_test")

    def test_load_pipeline_falls_back_to_legacy_token_keyword(self) -> None:
        """The loader should remain compatible with older pyannote APIs."""

        _PipelineAcceptsLegacyToken.calls = []

        pipeline = PyannoteDiarizer._load_pipeline(
            pipeline_class=_PipelineAcceptsLegacyToken,
            model_name="pyannote/speaker-diarization-3.1",
            token="hf_test",
        )

        self.assertEqual(
            _PipelineAcceptsLegacyToken.calls,
            [
                (
                    "pyannote/speaker-diarization-3.1",
                    {"use_auth_token": "hf_test"},
                ),
            ],
        )
        self.assertEqual(pipeline["kwargs"]["use_auth_token"], "hf_test")

    def test_get_pipeline_rejects_missing_loader_result(self) -> None:
        """The loader should not mask a missing pipeline as a device error."""

        config = PipelineConfig(diarization_enabled=True)
        diarizer = PyannoteDiarizer(config)

        class _TorchStub:
            @staticmethod
            def device(value: str) -> str:
                return value

        with (
            patch.object(
                diarizer,
                "_import_pyannote_pipeline",
                return_value=(_PipelineReturnsNone, _TorchStub),
            ),
            self.assertRaises(DiarizationUnavailableError) as context,
        ):
            diarizer._get_pipeline()

        self.assertIn("returned no object", str(context.exception))

    def test_get_pipeline_retries_when_torch_weights_only_breaks(self) -> None:
        """The loader should recover from PyTorch 2.6+ weights-only defaults."""

        config = PipelineConfig(diarization_enabled=True)
        diarizer = PyannoteDiarizer(config)
        _PipelineRetriesWhenWeightsOnlyBreaks.calls = []

        class _TorchStub:
            @staticmethod
            def device(value: str) -> str:
                return value

        with patch.object(
            diarizer,
            "_import_pyannote_pipeline",
            return_value=(_PipelineRetriesWhenWeightsOnlyBreaks, _TorchStub),
        ):
            pipeline = diarizer._get_pipeline()

        self.assertIsInstance(pipeline, _PipelineWithDevice)
        self.assertEqual(
            _PipelineRetriesWhenWeightsOnlyBreaks.calls,
            [None, "1"],
        )
        self.assertIsNone(os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"))

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
    def _write_test_wave(audio_path: Path) -> None:
        """Write a short mono PCM WAV file for decoder-path tests."""

        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * 160)


if __name__ == "__main__":
    unittest.main()
