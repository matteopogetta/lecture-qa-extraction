"""Persistence helpers for normalized audio sidecar metadata."""

from __future__ import annotations

import json
from pathlib import Path

from core.config import PipelineConfig
from core.errors import AudioMetadataError
from core.models import NormalizedAudioAssetMetadata


class NormalizedAudioMetadataStore:
    """Load and save minimal metadata for normalized audio artifacts."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def build_metadata_path(self, audio_path: Path) -> Path:
        """Return the deterministic sidecar path for one audio artifact."""

        suffix = f"{audio_path.suffix}{self.config.normalized_audio_metadata_extension}"
        return audio_path.with_suffix(suffix)

    def load(self, audio_path: Path) -> NormalizedAudioAssetMetadata | None:
        """Return metadata for one artifact, or `None` when no sidecar exists."""

        metadata_path = self.build_metadata_path(audio_path)
        if not metadata_path.exists():
            return None

        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AudioMetadataError(
                f"Failed to load normalized audio metadata from '{metadata_path}'.",
            ) from error

        try:
            return NormalizedAudioAssetMetadata.from_dict(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise AudioMetadataError(
                "Normalized audio metadata is incomplete or malformed for "
                f"'{metadata_path}'.",
            ) from error

    def save(
        self,
        audio_path: Path,
        metadata: NormalizedAudioAssetMetadata,
    ) -> Path:
        """Persist metadata next to one normalized audio artifact."""

        metadata_path = self.build_metadata_path(audio_path)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            metadata_path.write_text(
                json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as error:
            raise AudioMetadataError(
                f"Failed to save normalized audio metadata to '{metadata_path}'.",
            ) from error
        return metadata_path
