"""Backend-specific transcription helpers.

This module keeps speech-to-text dependencies isolated from the rest of the
prototype. The initial implementation uses `faster-whisper` because it can
return segment-level timestamps and source-level language metadata while
remaining practical for multilingual lecture audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lecture_analyzer.core.config import PipelineConfig


@dataclass(slots=True)
class BackendSegment:
    """A raw timed segment returned by a transcription backend."""

    start_seconds: float
    end_seconds: float
    text: str
    detected_language: str | None = None
    speaker_label: str | None = None
    transcription_confidence: float | None = None
    language_confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TranscriptionBackend(Protocol):
    """Minimal protocol used by the transcriber."""

    backend_name: str

    def transcribe(
        self,
        audio_path: Path,
    ) -> tuple[list[BackendSegment], dict[str, Any]]:
        """Return timed backend segments and source-level metadata."""


class FasterWhisperBackend:
    """Transcribe audio with `faster-whisper`.

    The backend is initialized lazily so configuration errors are raised only
    when transcription is actually requested. The current setup uses Whisper's
    automatic language detection by default because it works reasonably well
    for Italian, English, and mixed lecture speech, even though per-segment
    language switching is still limited.
    """

    backend_name = "faster-whisper"

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._model = None

    def transcribe(
        self,
        audio_path: Path,
    ) -> tuple[list[BackendSegment], dict[str, Any]]:
        """Return timed segments for a normalized audio file."""

        model = self._get_model()
        language = self._resolve_language()

        # Request backend-native timing information and keep decoding settings
        # explicit so runs remain explainable and reproducible.
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=self.config.transcription_beam_size,
            language=language,
            task="transcribe",
        )

        detected_language = getattr(info, "language", None)
        language_confidence = getattr(info, "language_probability", None)
        backend_segments = [
            BackendSegment(
                start_seconds=float(getattr(segment, "start", 0.0)),
                end_seconds=float(getattr(segment, "end", 0.0)),
                text=str(getattr(segment, "text", "")).strip(),
                detected_language=(
                    getattr(segment, "language", None) or detected_language
                ),
                speaker_label=getattr(segment, "speaker", None),
                transcription_confidence=getattr(segment, "confidence", None),
                language_confidence=language_confidence,
                metadata=self._build_segment_metadata(segment),
            )
            for segment in segments
        ]
        backend_metadata = {
            "backend": self.backend_name,
            "model_name": self.config.transcription_model_name,
            "compute_type": self.config.transcription_compute_type,
            "language_mode": self.config.transcription_language_mode,
            "detected_language": detected_language,
            "language_confidence": language_confidence,
        }
        return backend_segments, backend_metadata

    def _get_model(self) -> Any:
        """Return the lazily initialized Whisper model instance."""

        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "The configured transcription backend requires the "
                "`faster-whisper` package. Install it to enable lecture "
                "transcription.",
            ) from error

        model_kwargs: dict[str, Any] = {}
        if self.config.transcription_compute_type != "auto":
            model_kwargs["compute_type"] = self.config.transcription_compute_type
        self._model = WhisperModel(
            self.config.transcription_model_name,
            **model_kwargs,
        )
        return self._model

    def _resolve_language(self) -> str | None:
        """Translate the configuration into a backend language argument."""

        mode = self.config.transcription_language_mode.strip().lower()
        if mode == "auto":
            return None
        if mode == "fixed":
            language = self.config.transcription_language
            if language is None or not language.strip():
                raise ValueError(
                    "A fixed transcription language requires "
                    "PipelineConfig.transcription_language to be set.",
                )
            return language.strip().lower()
        raise ValueError(
            "Unsupported transcription language mode. "
            "Use 'auto' or 'fixed'.",
        )

    @staticmethod
    def _build_segment_metadata(segment: Any) -> dict[str, Any]:
        """Preserve backend metadata that may be useful later."""

        metadata: dict[str, Any] = {}
        avg_logprob = getattr(segment, "avg_logprob", None)
        no_speech_prob = getattr(segment, "no_speech_prob", None)
        compression_ratio = getattr(segment, "compression_ratio", None)

        if avg_logprob is not None:
            metadata["avg_logprob"] = float(avg_logprob)
        if no_speech_prob is not None:
            metadata["no_speech_probability"] = float(no_speech_prob)
        if compression_ratio is not None:
            metadata["compression_ratio"] = float(compression_ratio)
        return metadata


def build_transcription_backend(config: PipelineConfig) -> TranscriptionBackend:
    """Instantiate the configured transcription backend."""

    backend_name = config.transcription_backend.strip().lower()
    if backend_name == FasterWhisperBackend.backend_name:
        return FasterWhisperBackend(config)

    raise ValueError(
        f"Unsupported transcription backend '{config.transcription_backend}'.",
    )
