"""Pyannote-based speaker diarization layered on top of normalized audio."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import wave
from typing import Any
import warnings

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.errors import IngestionError
from lecture_analyzer.core.models import (
    AudioSource,
    DiarizationResult,
    DiarizationSegment,
    LectureSession,
)
from lecture_analyzer.transcription.cache_store import (
    CachedDiarization,
    DiarizationPaths,
    TranscriptionCacheStore,
)


LOGGER = logging.getLogger(__name__)


class DiarizationError(IngestionError):
    """Base exception for diarization-layer failures."""


class DiarizationUnavailableError(DiarizationError):
    """Raised when pyannote or one of its runtime dependencies is unavailable."""


class PyannoteDiarizer:
    """Run speaker diarization on normalized audio and persist the result."""

    _PYANNOTE_TORCHCODEC_WARNING_PATTERN = (
        r"\s*torchcodec is not installed correctly so built-in audio decoding "
        r"will fail\..*"
    )

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.cache_store = TranscriptionCacheStore(config)
        self._pipeline_cache: dict[tuple[str, str, str | None], Any] = {}

    def diarize_session(self, session: LectureSession) -> list[DiarizationSegment]:
        """Diarize every normalized audio source without mutating utterances."""

        if not self.config.diarization_enabled:
            session.diarization_segments = []
            session.metadata["diarization_enabled"] = False
            session.metadata["diarization_status"] = "disabled"
            session.metadata["diarization_segment_count"] = 0
            session.metadata["diarization_speaker_count"] = 0
            for audio_source in session.audio_sources:
                self._apply_diarization_metadata(
                    audio_source=audio_source,
                    diarization_result=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="disabled",
                    reason="diarization_disabled",
                )
            return []

        diarization_segments: list[DiarizationSegment] = []
        diarized_sources = 0
        failed_sources: list[str] = []

        for audio_source in self._ordered_audio_sources(session.audio_sources):
            try:
                diarization_result = self.diarize_source(audio_source)
            except DiarizationError as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.warning(
                    "Diarization skipped for %s: %s",
                    audio_source.audio_source_id,
                    error,
                )
                self._apply_diarization_metadata(
                    audio_source=audio_source,
                    diarization_result=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error),
                )
                continue
            except Exception as error:
                failed_sources.append(audio_source.audio_source_id)
                LOGGER.exception(
                    "Unexpected diarization failure for %s.",
                    audio_source.audio_source_id,
                )
                self._apply_diarization_metadata(
                    audio_source=audio_source,
                    diarization_result=None,
                    cache_record=None,
                    artifact_paths=None,
                    status="failed",
                    reason=str(error) or "unexpected_diarization_error",
                )
                continue

            diarization_segments.extend(diarization_result.segments)
            diarized_sources += 1

        session.diarization_segments = diarization_segments
        session.metadata["diarization_enabled"] = True
        session.metadata["diarization_status"] = self._resolve_session_status(
            total_sources=len(session.audio_sources),
            diarized_sources=diarized_sources,
            failed_sources=failed_sources,
        )
        session.metadata["diarization_source_count"] = len(session.audio_sources)
        session.metadata["diarization_diarized_source_count"] = diarized_sources
        session.metadata["diarization_failed_sources"] = failed_sources
        session.metadata["diarization_segment_count"] = len(diarization_segments)
        session.metadata["diarization_speaker_count"] = len(
            {
                segment.speaker_id
                for segment in diarization_segments
                if segment.speaker_id
            },
        )
        return diarization_segments

    def diarize_source(self, audio_source: AudioSource) -> DiarizationResult:
        """Diarize one source-local normalized audio file."""

        audio_path = audio_source.audio_path.expanduser().resolve()
        if not audio_path.exists():
            raise DiarizationError(
                f"Normalized audio source not found for diarization: '{audio_path}'.",
            )

        artifact_found = self.cache_store.has_diarization_artifact(audio_source)
        cached_diarization = self.cache_store.load_diarization(audio_source)
        if cached_diarization is not None:
            cached_diarization.diarization_result.metadata["cache_hit"] = True
            cached_diarization.diarization_result.metadata["used_cache"] = False
            cached_diarization.diarization_result.metadata["used_existing_artifact"] = True
            cached_diarization.diarization_result.metadata["artifact_reuse_enabled"] = True
            cached_diarization.diarization_result.metadata["artifact_found"] = True
            cached_diarization.diarization_result.metadata["artifact_reused"] = True
            cached_diarization.diarization_result.metadata[
                "artifact_ignored_due_to_force_recompute"
            ] = False
            cached_diarization.diarization_result.metadata["recomputed"] = False
            cached_diarization.diarization_result.metadata["forced_recompute"] = False
            cached_diarization.diarization_result.metadata["artifact_manifest_path"] = str(
                cached_diarization.paths.manifest_path,
            )
            self._apply_diarization_metadata(
                audio_source=audio_source,
                diarization_result=cached_diarization.diarization_result,
                cache_record=cached_diarization,
                artifact_paths=cached_diarization.paths,
                status="available",
                reason=None,
            )
            return cached_diarization.diarization_result

        if self.config.force_recompute and artifact_found:
            LOGGER.info(
                "Ignoring diarization artifact for %s because run mode is from scratch.",
                audio_source.audio_source_id,
            )

        diarization_output = self._run_with_pyannote(audio_path)
        diarization_result = self._build_diarization_result(
            audio_source=audio_source,
            diarization_output=diarization_output,
            cache_hit=False,
        )
        diarization_result.metadata["used_cache"] = False
        diarization_result.metadata["used_existing_artifact"] = False
        diarization_result.metadata["artifact_reuse_enabled"] = (
            self.config.intermediate_artifact_reuse_enabled
        )
        diarization_result.metadata["artifact_found"] = artifact_found
        diarization_result.metadata["artifact_reused"] = False
        diarization_result.metadata["artifact_ignored_due_to_force_recompute"] = (
            artifact_found and self.config.force_recompute
        )
        diarization_result.metadata["recomputed"] = True
        diarization_result.metadata["forced_recompute"] = self.config.force_recompute
        artifact_paths = self.cache_store.save_diarization(audio_source, diarization_result)
        diarization_result.metadata["artifact_manifest_path"] = str(
            artifact_paths.manifest_path,
        )
        self._apply_diarization_metadata(
            audio_source=audio_source,
            diarization_result=diarization_result,
            cache_record=None,
            artifact_paths=artifact_paths,
            status="available",
            reason=None,
        )
        return diarization_result

    def _run_with_pyannote(self, audio_path: Path) -> Any:
        """Call pyannote diarization primitives with the current audio source."""

        pipeline = self._get_pipeline()
        inference_kwargs = self._build_inference_kwargs()
        pipeline_input = self._build_pipeline_audio_input(audio_path)
        try:
            diarization_output = pipeline(pipeline_input, **inference_kwargs)
        except TypeError:
            diarization_output = pipeline(pipeline_input)
        except Exception as error:
            raise DiarizationError(
                f"pyannote diarization failed for '{audio_path.name}': {error}",
            ) from error

        track_selection = self._resolve_track_selection(diarization_output)
        if track_selection is None:
            raise DiarizationError(
                "pyannote returned an unsupported diarization payload.",
            )
        return diarization_output

    def _build_pipeline_audio_input(self, audio_path: Path) -> Any:
        """Prefer waveform dictionaries for local PCM WAV files."""

        waveform_input = self._load_waveform_input(audio_path)
        if waveform_input is not None:
            return waveform_input
        return str(audio_path)

    @staticmethod
    def _load_waveform_input(audio_path: Path) -> dict[str, Any] | None:
        """Load a normalized WAV file into the in-memory pyannote input shape."""

        if audio_path.suffix.lower() != ".wav":
            return None

        try:
            import numpy as np
            import torch
        except ImportError:
            return None

        try:
            with wave.open(str(audio_path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channel_count = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                frame_count = wav_file.getnframes()
                raw_frames = wav_file.readframes(frame_count)
        except (wave.Error, OSError):
            return None

        dtype_map = {
            1: np.uint8,
            2: np.int16,
            4: np.int32,
        }
        sample_dtype = dtype_map.get(sample_width)
        if sample_dtype is None:
            return None

        waveform = np.frombuffer(raw_frames, dtype=sample_dtype)
        if waveform.size == 0:
            return None

        if channel_count > 1:
            waveform = waveform.reshape(-1, channel_count).T
        else:
            waveform = waveform.reshape(1, -1)

        if sample_width == 1:
            waveform = (waveform.astype(np.float32) - 128.0) / 128.0
        elif sample_width == 2:
            waveform = waveform.astype(np.float32) / 32768.0
        else:
            waveform = waveform.astype(np.float32) / 2147483648.0

        return {
            "waveform": torch.from_numpy(waveform.copy()),
            "sample_rate": int(sample_rate),
        }

    def _build_diarization_result(
        self,
        audio_source: AudioSource,
        diarization_output: Any,
        cache_hit: bool,
    ) -> DiarizationResult:
        """Normalize pyannote output into stable project diarization models."""

        track_selection = self._resolve_track_selection(diarization_output)
        if track_selection is None:
            raise DiarizationError(
                "pyannote returned an unsupported diarization payload.",
            )
        track_container, segment_source, available_sources = track_selection

        raw_segments = sorted(
            list(track_container.itertracks(yield_label=True)),
            key=lambda item: (
                float(getattr(item[0], "start", 0.0)),
                float(getattr(item[0], "end", 0.0)),
                str(item[2]),
            ),
        )

        speaker_aliases: dict[str, str] = {}
        segments: list[DiarizationSegment] = []
        for segment_index, (time_segment, _track, raw_speaker_label) in enumerate(
            raw_segments,
            start=1,
        ):
            start_seconds = self._coerce_float(getattr(time_segment, "start", None))
            end_seconds = self._coerce_float(getattr(time_segment, "end", None))
            if start_seconds is None or end_seconds is None:
                continue
            if end_seconds < start_seconds:
                end_seconds = start_seconds

            speaker_id = speaker_aliases.setdefault(
                str(raw_speaker_label),
                f"SPEAKER_{len(speaker_aliases):02d}",
            )
            segments.append(
                DiarizationSegment(
                    diarization_segment_id=(
                        f"{audio_source.audio_source_id}"
                        f"_diarization_segment_{segment_index:04d}"
                    ),
                    audio_source_id=audio_source.audio_source_id,
                    speaker_id=speaker_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    segment_source=segment_source,
                    session_start_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        start_seconds,
                    ),
                    session_end_seconds=self._build_session_time(
                        audio_source.session_offset_seconds,
                        end_seconds,
                    ),
                    metadata={"raw_speaker_label": str(raw_speaker_label)},
                ),
            )

        return DiarizationResult(
            audio_source_id=audio_source.audio_source_id,
            source_audio_path=audio_source.audio_path,
            preferred_segment_source=segment_source,
            available_segment_sources=available_sources,
            speaker_ids=list(speaker_aliases.values()),
            segments=segments,
            metadata={
                "backend": "pyannote",
                "model_name": self.config.diarization_model_name,
                "device": self.config.diarization_device,
                "cache_hit": cache_hit,
                "preferred_segment_source": segment_source,
                "available_segment_sources": available_sources,
                "output_container_type": type(diarization_output).__name__,
                "track_container_type": type(track_container).__name__,
            },
        )

    def _get_pipeline(self) -> Any:
        """Load and cache the pyannote pipeline for the current configuration."""

        token = self._resolve_auth_token()
        cache_key = (
            self.config.diarization_model_name,
            self.config.diarization_device,
            token,
        )
        if cache_key in self._pipeline_cache:
            return self._pipeline_cache[cache_key]

        pipeline_class, torch_module = self._import_pyannote_pipeline()

        try:
            pipeline = self._load_pipeline(
                pipeline_class=pipeline_class,
                model_name=self.config.diarization_model_name,
                token=token,
            )
        except Exception as error:
            if self._is_weights_only_load_error(error):
                with self._force_legacy_torch_load():
                    try:
                        pipeline = self._load_pipeline(
                            pipeline_class=pipeline_class,
                            model_name=self.config.diarization_model_name,
                            token=token,
                        )
                    except Exception as retry_error:
                        error = retry_error
                    else:
                        error = None
            if error is not None:
                detail = str(error).strip() or error.__class__.__name__
                raise DiarizationUnavailableError(
                    "pyannote diarization could not be loaded. Install "
                    "`pyannote.audio`, ensure the model is accessible, and provide "
                    "a Hugging Face token via `PipelineConfig.diarization_auth_token`, "
                    "`HUGGINGFACE_HUB_TOKEN`, or `HF_TOKEN` when required. "
                    f"Original error: {detail}",
                ) from error

        if pipeline is None:
            raise DiarizationUnavailableError(
                "pyannote diarization could not be loaded. The pretrained "
                "pipeline returned no object, which usually means the model "
                "download was blocked by missing authentication or unaccepted "
                "gated-model conditions.",
            )

        try:
            pipeline.to(torch_module.device(self.config.diarization_device))
        except Exception as error:
            raise DiarizationUnavailableError(
                f"pyannote diarization device '{self.config.diarization_device}' "
                "is not available.",
            ) from error

        self._pipeline_cache[cache_key] = pipeline
        return pipeline

    @staticmethod
    def _load_pipeline(
        pipeline_class: Any,
        model_name: str,
        token: str | None,
    ) -> Any:
        """Load a pyannote pipeline across token API variants."""

        if token is None:
            return pipeline_class.from_pretrained(model_name)

        try:
            return pipeline_class.from_pretrained(
                model_name,
                token=token,
            )
        except TypeError:
            return pipeline_class.from_pretrained(
                model_name,
                use_auth_token=token,
            )

    @staticmethod
    def _is_weights_only_load_error(error: Exception) -> bool:
        """Return whether the failure matches PyTorch's weights-only change."""

        message = str(error)
        return (
            "Weights only load failed" in message
            or "weights_only" in message
            or "add_safe_globals" in message
        )

    @staticmethod
    @contextmanager
    def _force_legacy_torch_load():
        """Temporarily restore pre-2.6 torch.load behavior for trusted models."""

        env_var = "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"
        previous_value = os.environ.get(env_var)
        os.environ[env_var] = "1"
        try:
            yield
        finally:
            if previous_value is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous_value

    def _build_inference_kwargs(self) -> dict[str, int]:
        """Return the configured optional speaker-count hints."""

        inference_kwargs: dict[str, int] = {}
        if self.config.diarization_num_speakers is not None:
            inference_kwargs["num_speakers"] = self.config.diarization_num_speakers
            return inference_kwargs
        if self.config.diarization_min_speakers is not None:
            inference_kwargs["min_speakers"] = self.config.diarization_min_speakers
        if self.config.diarization_max_speakers is not None:
            inference_kwargs["max_speakers"] = self.config.diarization_max_speakers
        return inference_kwargs

    def _resolve_track_selection(
        self,
        diarization_output: Any,
    ) -> tuple[Any, str, list[str]] | None:
        """Return the preferred track container and the available source kinds."""

        candidates: list[tuple[str, Any]] = []

        exclusive_diarization = getattr(
            diarization_output,
            "exclusive_speaker_diarization",
            None,
        )
        if hasattr(exclusive_diarization, "itertracks"):
            candidates.append(("exclusive", exclusive_diarization))

        regular_diarization = getattr(diarization_output, "speaker_diarization", None)
        if hasattr(regular_diarization, "itertracks"):
            candidates.append(("regular", regular_diarization))

        if hasattr(diarization_output, "itertracks"):
            candidates.append(("regular", diarization_output))

        if not candidates:
            return None

        available_sources: list[str] = []
        selected_track_container: Any | None = None
        selected_source: str | None = None
        for source_name, track_container in candidates:
            if source_name not in available_sources:
                available_sources.append(source_name)
            if selected_track_container is not None:
                continue
            if self.config.diarization_prefer_exclusive and source_name == "exclusive":
                selected_track_container = track_container
                selected_source = source_name
            elif not self.config.diarization_prefer_exclusive and source_name == "regular":
                selected_track_container = track_container
                selected_source = source_name

        if selected_track_container is None:
            selected_source, selected_track_container = candidates[0]

        return selected_track_container, selected_source, available_sources

    def _resolve_auth_token(self) -> str | None:
        """Return the configured Hugging Face token when one is available."""

        if self.config.diarization_auth_token is not None:
            return self.config.diarization_auth_token
        for env_var in ("HUGGINGFACE_HUB_TOKEN", "HF_TOKEN"):
            env_value = os.environ.get(env_var, "").strip()
            if env_value:
                return env_value
        return None

    def _apply_diarization_metadata(
        self,
        audio_source: AudioSource,
        diarization_result: DiarizationResult | None,
        cache_record: CachedDiarization | None,
        artifact_paths: DiarizationPaths | None,
        status: str,
        reason: str | None,
    ) -> None:
        """Attach diarization state to source metadata for traceability."""

        metadata: dict[str, Any] = {
            "enabled": self.config.diarization_enabled,
            "attempted": status not in {"disabled", "skipped"},
            "status": status,
            "cache_hit": cache_record is not None,
            "used_cache": False,
            "used_existing_artifact": cache_record is not None,
            "artifact_reuse_enabled": self.config.intermediate_artifact_reuse_enabled,
            "forced_recompute": self.config.force_recompute,
        }
        if reason is not None:
            metadata["reason"] = reason
        if diarization_result is not None:
            metadata["diarization_segment_count"] = len(diarization_result.segments)
            metadata["speaker_count"] = len(diarization_result.speaker_ids)
            metadata["speaker_ids"] = diarization_result.speaker_ids
            metadata["preferred_segment_source"] = (
                diarization_result.preferred_segment_source
            )
            metadata["available_segment_sources"] = (
                diarization_result.available_segment_sources
            )
            metadata["artifact_found"] = diarization_result.metadata.get(
                "artifact_found",
            )
            metadata["artifact_reused"] = diarization_result.metadata.get(
                "artifact_reused",
            )
            metadata["artifact_ignored_due_to_force_recompute"] = (
                diarization_result.metadata.get(
                    "artifact_ignored_due_to_force_recompute",
                )
            )
            metadata["recomputed"] = diarization_result.metadata.get("recomputed")
        if artifact_paths is not None:
            metadata["artifact_manifest_path"] = str(artifact_paths.manifest_path)
        audio_source.metadata["diarization"] = metadata

    @staticmethod
    def _ordered_audio_sources(
        audio_sources: Sequence[AudioSource],
    ) -> list[AudioSource]:
        """Return audio sources in deterministic processing order."""

        return sorted(
            audio_sources,
            key=lambda source: (
                source.order_index is None,
                source.order_index or 0,
                source.audio_source_id,
            ),
        )

    @staticmethod
    def _resolve_session_status(
        total_sources: int,
        diarized_sources: int,
        failed_sources: Sequence[str],
    ) -> str:
        """Return a compact session-level diarization status label."""

        if total_sources == 0:
            return "empty"
        if diarized_sources == 0 and failed_sources:
            return "failed"
        if diarized_sources == total_sources:
            return "ready"
        if diarized_sources > 0:
            return "partial"
        return "skipped"

    @staticmethod
    def _build_session_time(
        session_offset_seconds: float | None,
        source_time_seconds: float | None,
    ) -> float | None:
        """Translate source-local timing into session timing when possible."""

        if session_offset_seconds is None or source_time_seconds is None:
            return None
        return float(session_offset_seconds) + float(source_time_seconds)

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        """Return a float when the input can be interpreted safely."""

        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _import_pyannote_pipeline() -> tuple[Any, Any]:
        """Import pyannote and torch lazily so the feature remains optional."""

        try:
            with warnings.catch_warnings():
                PyannoteDiarizer._install_pyannote_warning_filter()
                from pyannote.audio import Pipeline
        except ImportError as error:
            raise DiarizationUnavailableError(
                "pyannote diarization is not installed. Install the "
                "`pyannote.audio` package to enable speaker diarization.",
            ) from error
        try:
            import torch
        except ImportError as error:
            raise DiarizationUnavailableError(
                "PyTorch is required by pyannote diarization but is not installed.",
            ) from error
        return Pipeline, torch

    @classmethod
    def _install_pyannote_warning_filter(cls) -> None:
        """Ignore the known torchcodec import warning once we preload waveforms."""

        warnings.filterwarnings(
            "ignore",
            message=cls._PYANNOTE_TORCHCODEC_WARNING_PATTERN,
            category=UserWarning,
            module=r"pyannote\.audio\.core\.io",
        )
