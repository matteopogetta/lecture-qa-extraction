"""Normalize source media into deterministic audio artifacts for processing."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from core.config import PipelineConfig
from core.errors import (
    AudioExtractionError,
    AudioValidationError,
    MissingInputError,
    UnsupportedMediaError,
)
from core.models import AudioSource, InputSource, NormalizedAudioAssetMetadata
from core.types import MediaType, ProcessingStatus
from preprocessing.normalized_audio_metadata_store import (
    NormalizedAudioMetadataStore,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProbedAudioInfo:
    """Technical audio properties discovered by probing an audio file."""

    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_depth: int | None = None
    duration_seconds: float | None = None


class AudioNormalizer:
    """Normalize original media files into reusable audio processing assets."""

    _SAFE_NAME_RE = re.compile(r"[^a-z0-9]+")
    _SAMPLE_FMT_BIT_DEPTHS = {
        "u8": 8,
        "s16": 16,
        "s32": 32,
        "s64": 64,
        "flt": 32,
        "dbl": 64,
    }

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.metadata_store = NormalizedAudioMetadataStore(config)

    def normalize_sources(
        self,
        input_sources: Sequence[InputSource],
    ) -> list[AudioSource]:
        """Normalize all session inputs in deterministic order."""

        return [self.normalize_source(source) for source in input_sources]

    def normalize_source(self, source: InputSource) -> AudioSource:
        """Normalize one input source into the shared processing format."""

        if source.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
            raise UnsupportedMediaError(
                f"Cannot normalize unsupported media source '{source.original_path.name}'.",
            )

        source_path = source.original_path.expanduser().resolve()
        if not source_path.exists():
            raise MissingInputError(f"Input path does not exist: '{source_path}'.")

        output_path = self._build_output_path(source)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        expected_metadata = self._build_expected_metadata(source, output_path)
        artifact_found = output_path.exists()

        if self.config.force_recompute and artifact_found:
            LOGGER.info(
                "Ignoring existing normalized audio '%s' because run mode is from scratch.",
                output_path.name,
            )
        elif self.config.overwrite_normalized_audio and artifact_found:
            LOGGER.info(
                "Regenerating normalized audio '%s' because overwrite was requested.",
                output_path.name,
            )
        elif not self.config.overwrite_normalized_audio:
            cached_audio_source = self._reuse_existing_artifact(
                source=source,
                output_path=output_path,
                expected_metadata=expected_metadata,
            )
            if cached_audio_source is not None:
                return cached_audio_source

        LOGGER.info(
            "Normalizing media '%s' to '%s'.",
            source_path.name,
            output_path.name,
        )
        self._convert_to_normalized_audio(source_path, output_path)
        validated_metadata = self._validate_and_build_metadata(
            source=source,
            output_path=output_path,
            expected_metadata=expected_metadata,
        )
        metadata_path = self.metadata_store.save(output_path, validated_metadata)
        return self._build_audio_source(
            source=source,
            audio_path=output_path,
            normalized_asset=validated_metadata,
            metadata_path=metadata_path,
            cache_hit=False,
            artifact_found=artifact_found,
        )

    def _reuse_existing_artifact(
        self,
        source: InputSource,
        output_path: Path,
        expected_metadata: NormalizedAudioAssetMetadata,
    ) -> AudioSource | None:
        """Return a reusable normalized audio source when the cache is valid."""

        if not output_path.exists():
            return None

        try:
            cached_metadata = self.metadata_store.load(output_path)
            if cached_metadata is None:
                LOGGER.info(
                    "Skipping normalized audio cache reuse for '%s' because the metadata sidecar is missing.",
                    source.original_path.name,
                )
                return None

            if not self._metadata_matches(expected_metadata, cached_metadata):
                LOGGER.info(
                    "Skipping normalized audio cache reuse for '%s' because source or config metadata changed.",
                    source.original_path.name,
                )
                return None

            validated_metadata = self._validate_and_build_metadata(
                source=source,
                output_path=output_path,
                expected_metadata=expected_metadata,
            )
        except AudioValidationError as error:
            LOGGER.warning(
                "Regenerating normalized audio '%s' because validation failed: %s",
                output_path.name,
                error,
            )
            return None

        metadata_path = self.metadata_store.save(output_path, validated_metadata)
        LOGGER.info(
            "Reusing normalized audio '%s' for source '%s'.",
            output_path.name,
            source.original_path.name,
        )
        return self._build_audio_source(
            source=source,
            audio_path=output_path,
            normalized_asset=validated_metadata,
            metadata_path=metadata_path,
            cache_hit=True,
            artifact_found=True,
        )

    def _convert_to_normalized_audio(
        self,
        source_path: Path,
        output_path: Path,
    ) -> None:
        """Run the configured conversion backend to build the normalized asset."""

        command = [
            self.config.ffmpeg_executable,
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            str(self.config.normalized_audio_channels),
            "-ar",
            str(self.config.normalized_audio_sample_rate),
            "-sample_fmt",
            f"s{self.config.normalized_audio_bit_depth}",
            "-c:a",
            self._resolve_audio_codec(),
            str(output_path),
        ]

        try:
            completed_process = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as error:
            raise AudioExtractionError(
                "Audio normalization requires an ffmpeg-compatible executable. "
                "Install ffmpeg or update PipelineConfig.ffmpeg_executable.",
            ) from error

        if completed_process.returncode != 0:
            error_message = (
                completed_process.stderr.strip()
                or completed_process.stdout.strip()
                or "Unknown ffmpeg error."
            )
            raise AudioExtractionError(
                f"Failed to normalize '{source_path.name}': {error_message}",
            )

    def _validate_and_build_metadata(
        self,
        source: InputSource,
        output_path: Path,
        expected_metadata: NormalizedAudioAssetMetadata,
    ) -> NormalizedAudioAssetMetadata:
        """Probe a normalized artifact and ensure it matches the current contract."""

        if not output_path.exists():
            raise AudioValidationError(
                f"Normalized audio artifact is missing: '{output_path}'.",
            )

        probe = self._probe_audio_file(output_path)
        self._assert_probe_matches_contract(output_path, probe)
        return NormalizedAudioAssetMetadata(
            source_path=expected_metadata.source_path,
            source_filename=expected_metadata.source_filename,
            source_last_modified_ns=expected_metadata.source_last_modified_ns,
            output_format=expected_metadata.output_format,
            sample_rate=probe.sample_rate or expected_metadata.sample_rate,
            channels=probe.channels or expected_metadata.channels,
            bit_depth=probe.bit_depth or expected_metadata.bit_depth,
            derived_path=str(output_path),
            duration_seconds=probe.duration_seconds or source.duration_seconds,
        )

    def _probe_audio_file(self, audio_path: Path) -> ProbedAudioInfo:
        """Return technical audio properties using `ffprobe`."""

        command = [
            self.config.ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            (
                "stream=codec_name,sample_rate,channels,bits_per_sample,"
                "bits_per_raw_sample,sample_fmt:format=duration"
            ),
            "-of",
            "json",
            str(audio_path),
        ]

        try:
            completed_process = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as error:
            raise AudioValidationError(
                "Normalized audio validation requires ffprobe. "
                "Install ffprobe or update PipelineConfig.ffprobe_executable.",
            ) from error

        if completed_process.returncode != 0:
            error_message = (
                completed_process.stderr.strip()
                or completed_process.stdout.strip()
                or "Unknown ffprobe error."
            )
            raise AudioValidationError(
                f"Failed to validate normalized audio '{audio_path.name}': {error_message}",
            )

        try:
            payload = json.loads(completed_process.stdout)
        except json.JSONDecodeError as error:
            raise AudioValidationError(
                f"ffprobe returned invalid JSON for '{audio_path.name}'.",
            ) from error

        streams = payload.get("streams") or []
        if not streams:
            raise AudioValidationError(
                f"No audio stream was found in normalized artifact '{audio_path.name}'.",
            )

        stream = streams[0]
        return ProbedAudioInfo(
            codec_name=stream.get("codec_name"),
            sample_rate=self._safe_int(stream.get("sample_rate")),
            channels=self._safe_int(stream.get("channels")),
            bit_depth=self._resolve_bit_depth(stream),
            duration_seconds=self._safe_float((payload.get("format") or {}).get("duration")),
        )

    def _assert_probe_matches_contract(
        self,
        output_path: Path,
        probe: ProbedAudioInfo,
    ) -> None:
        """Raise when a probed artifact does not match the configured contract."""

        if probe.sample_rate != self.config.normalized_audio_sample_rate:
            raise AudioValidationError(
                f"Normalized audio '{output_path.name}' has sample rate "
                f"{probe.sample_rate}, expected {self.config.normalized_audio_sample_rate}.",
            )
        if probe.channels != self.config.normalized_audio_channels:
            raise AudioValidationError(
                f"Normalized audio '{output_path.name}' has {probe.channels} channels, "
                f"expected {self.config.normalized_audio_channels}.",
            )
        if (
            probe.bit_depth is not None
            and probe.bit_depth != self.config.normalized_audio_bit_depth
        ):
            raise AudioValidationError(
                f"Normalized audio '{output_path.name}' has bit depth "
                f"{probe.bit_depth}, expected {self.config.normalized_audio_bit_depth}.",
            )

        expected_codec = self._resolve_audio_codec()
        if probe.codec_name is None:
            raise AudioValidationError(
                f"Normalized audio '{output_path.name}' does not expose a codec name.",
            )
        if self.config.normalized_audio_format == "wav":
            if not probe.codec_name.startswith("pcm_s"):
                raise AudioValidationError(
                    f"Normalized audio '{output_path.name}' uses codec "
                    f"'{probe.codec_name}', expected PCM WAV.",
                )
            return
        if probe.codec_name != expected_codec:
            raise AudioValidationError(
                f"Normalized audio '{output_path.name}' uses codec "
                f"'{probe.codec_name}', expected '{expected_codec}'.",
            )

    def _build_audio_source(
        self,
        source: InputSource,
        audio_path: Path,
        normalized_asset: NormalizedAudioAssetMetadata,
        metadata_path: Path,
        cache_hit: bool,
        artifact_found: bool,
    ) -> AudioSource:
        """Create the internal audio source model used downstream."""

        forced_recompute = self.config.force_recompute or self.config.overwrite_normalized_audio
        return AudioSource(
            audio_source_id=f"audio_{source.source_id}",
            input_source_id=source.source_id,
            audio_path=audio_path,
            normalized_asset=normalized_asset,
            audio_format=normalized_asset.output_format,
            duration_seconds=normalized_asset.duration_seconds,
            order_index=source.order_index,
            extracted_from_video=source.media_type == MediaType.VIDEO,
            processing_status=ProcessingStatus.READY,
            metadata={
                "original_media_type": source.media_type.value,
                "original_path": str(source.original_path),
                "normalized_audio_path": str(audio_path),
                "normalized_audio_metadata_path": str(metadata_path),
                "normalization": {
                    **normalized_asset.to_dict(),
                    "cache_hit": cache_hit,
                    "used_cache": False,
                    "used_existing_artifact": cache_hit,
                    "artifact_reuse_enabled": (
                        not self.config.force_recompute
                        and not self.config.overwrite_normalized_audio
                    ),
                    "artifact_found": artifact_found,
                    "artifact_reused": cache_hit,
                    "artifact_ignored_due_to_force_recompute": (
                        artifact_found and forced_recompute and not cache_hit
                    ),
                    "recomputed": not cache_hit,
                    "forced_recompute": forced_recompute,
                    "status": (
                        "reused_from_artifact"
                        if cache_hit
                        else ("executed_forced" if forced_recompute else "executed")
                    ),
                },
            },
        )

    def _build_expected_metadata(
        self,
        source: InputSource,
        output_path: Path,
    ) -> NormalizedAudioAssetMetadata:
        """Return the metadata fingerprint expected for the current source."""

        source_path = source.original_path.expanduser().resolve()
        return NormalizedAudioAssetMetadata(
            source_path=str(source_path),
            source_filename=source_path.name,
            source_last_modified_ns=source_path.stat().st_mtime_ns,
            output_format=self.config.normalized_audio_format,
            sample_rate=self.config.normalized_audio_sample_rate,
            channels=self.config.normalized_audio_channels,
            bit_depth=self.config.normalized_audio_bit_depth,
            derived_path=str(output_path),
        )

    def _build_output_path(self, source: InputSource) -> Path:
        """Return the deterministic path for one normalized artifact."""

        order_prefix = source.order_index or 0
        safe_stem = self._sanitize_name(source.original_path.stem)
        filename = (
            f"{order_prefix:03d}_{safe_stem}_mono_"
            f"{self.config.normalized_audio_sample_rate}hz"
            f"{self.config.normalized_audio_extension}"
        )
        return self.config.audio_artifacts_directory / filename

    def _metadata_matches(
        self,
        expected: NormalizedAudioAssetMetadata,
        cached: NormalizedAudioAssetMetadata,
    ) -> bool:
        """Return whether cached metadata still matches source and config."""

        return (
            expected.source_path == cached.source_path
            and expected.source_filename == cached.source_filename
            and (
                expected.source_last_modified_ns
                == cached.source_last_modified_ns
            )
            and expected.output_format == cached.output_format
            and expected.sample_rate == cached.sample_rate
            and expected.channels == cached.channels
            and expected.bit_depth == cached.bit_depth
        )

    def _resolve_audio_codec(self) -> str:
        """Return the ffmpeg codec name for the configured output format."""

        if self.config.normalized_audio_format == "flac":
            return "flac"
        return f"pcm_s{self.config.normalized_audio_bit_depth}le"

    def _resolve_bit_depth(self, stream: dict[str, Any]) -> int | None:
        """Return the bit depth reported by ffprobe, when available."""

        for key in ("bits_per_raw_sample", "bits_per_sample"):
            value = self._safe_int(stream.get(key))
            if value is not None and value > 0:
                return value

        sample_format = str(stream.get("sample_fmt") or "").strip().lower()
        if sample_format in self._SAMPLE_FMT_BIT_DEPTHS:
            return self._SAMPLE_FMT_BIT_DEPTHS[sample_format]
        return None

    def _sanitize_name(self, value: str) -> str:
        """Return a conservative filesystem-safe stem."""

        normalized = value.strip().lower()
        normalized = self._SAFE_NAME_RE.sub("_", normalized)
        normalized = normalized.strip("_")
        return normalized or "source"

    @staticmethod
    def _safe_int(value: object) -> int | None:
        """Convert a probe value to `int` when possible."""

        if value in {None, ""}:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_float(value: object) -> float | None:
        """Convert a probe value to `float` when possible."""

        if value in {None, ""}:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
