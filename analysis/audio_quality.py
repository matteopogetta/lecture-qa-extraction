"""Deterministic local audio-quality heuristics for speaker attribution."""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
import math
from pathlib import Path
import wave

from core.config import PipelineConfig
from core.models import AudioSource, Utterance


@dataclass(slots=True)
class AudioQualityAssessment:
    """Quality indicators computed for one utterance-sized audio window."""

    status: str
    is_degraded: bool = False
    rms_ratio: float | None = None
    zero_crossing_rate: float | None = None
    degraded_reasons: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation of the assessment."""

        return {
            "status": self.status,
            "is_degraded": self.is_degraded,
            "rms_ratio": (
                round(self.rms_ratio, 4) if self.rms_ratio is not None else None
            ),
            "zero_crossing_rate": (
                round(self.zero_crossing_rate, 4)
                if self.zero_crossing_rate is not None
                else None
            ),
            "degraded_reasons": self.degraded_reasons,
            "reason": self.reason,
        }


@dataclass(slots=True)
class _AudioSignal:
    """Minimal in-memory audio representation for repeated window lookups."""

    sample_rate: int
    normalized_samples: tuple[float, ...]
    reference_rms: float


class AudioQualityAnalyzer:
    """Compute local quality heuristics from normalized audio files."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._signal_cache: dict[Path, _AudioSignal | None] = {}

    def assess_utterance(
        self,
        utterance: Utterance,
        audio_source: AudioSource | None,
    ) -> AudioQualityAssessment:
        """Return lightweight quality features for one utterance span."""

        if audio_source is None:
            return AudioQualityAssessment(
                status="unavailable",
                reason="audio_source_missing",
            )

        audio_path = audio_source.audio_path.expanduser().resolve()
        signal = self._load_signal(audio_path)
        if signal is None:
            return AudioQualityAssessment(
                status="unavailable",
                reason="audio_features_unavailable",
            )

        start_index = max(0, int(math.floor(utterance.start_seconds * signal.sample_rate)))
        end_index = max(start_index, int(math.ceil(utterance.end_seconds * signal.sample_rate)))
        window_samples = signal.normalized_samples[start_index:end_index]
        if not window_samples:
            return AudioQualityAssessment(
                status="unavailable",
                reason="empty_audio_window",
            )

        window_rms = self._compute_rms(window_samples)
        rms_ratio = (
            window_rms / signal.reference_rms
            if signal.reference_rms > 0.0
            else 0.0
        )
        zero_crossing_rate = self._compute_zero_crossing_rate(window_samples)

        degraded_reasons: list[str] = []
        low_energy_threshold = self.config.speaker_attribution_low_energy_ratio_threshold
        if (
            low_energy_threshold is not None
            and rms_ratio < low_energy_threshold
        ):
            degraded_reasons.append("low_energy")

        high_zcr_threshold = (
            self.config.speaker_attribution_high_zero_crossing_rate_threshold
        )
        if (
            high_zcr_threshold is not None
            and zero_crossing_rate > high_zcr_threshold
        ):
            degraded_reasons.append("high_zero_crossing_rate")

        return AudioQualityAssessment(
            status="available",
            is_degraded=bool(degraded_reasons),
            rms_ratio=rms_ratio,
            zero_crossing_rate=zero_crossing_rate,
            degraded_reasons=degraded_reasons,
        )

    def _load_signal(self, audio_path: Path) -> _AudioSignal | None:
        """Return cached audio samples when the normalized file is supported."""

        if audio_path in self._signal_cache:
            return self._signal_cache[audio_path]

        signal = self._read_wave_signal(audio_path)
        self._signal_cache[audio_path] = signal
        return signal

    @staticmethod
    def _read_wave_signal(audio_path: Path) -> _AudioSignal | None:
        """Load a PCM wave file into a compact normalized float buffer."""

        try:
            with wave.open(str(audio_path), "rb") as audio_file:
                sample_rate = audio_file.getframerate()
                channel_count = audio_file.getnchannels()
                sample_width = audio_file.getsampwidth()
                raw_frames = audio_file.readframes(audio_file.getnframes())
        except (OSError, wave.Error):
            return None

        normalized_samples = AudioQualityAnalyzer._decode_pcm_samples(
            raw_frames=raw_frames,
            sample_width=sample_width,
            channel_count=channel_count,
        )
        if not normalized_samples:
            return None

        reference_rms = AudioQualityAnalyzer._compute_rms(normalized_samples)
        return _AudioSignal(
            sample_rate=sample_rate,
            normalized_samples=normalized_samples,
            reference_rms=reference_rms,
        )

    @staticmethod
    def _decode_pcm_samples(
        raw_frames: bytes,
        sample_width: int,
        channel_count: int,
    ) -> tuple[float, ...]:
        """Decode a mono view of PCM frames into normalized float samples."""

        if channel_count <= 0:
            return ()

        if sample_width == 1:
            decoded_samples = array("B")
            decoded_samples.frombytes(raw_frames)
            normalized = tuple(
                (decoded_samples[index] - 128.0) / 128.0
                for index in range(0, len(decoded_samples), channel_count)
            )
            return normalized

        if sample_width == 2:
            decoded_samples = array("h")
            decoded_samples.frombytes(raw_frames)
            normalized = tuple(
                decoded_samples[index] / 32768.0
                for index in range(0, len(decoded_samples), channel_count)
            )
            return normalized

        if sample_width == 4:
            decoded_samples = array("i")
            decoded_samples.frombytes(raw_frames)
            normalized = tuple(
                decoded_samples[index] / 2147483648.0
                for index in range(0, len(decoded_samples), channel_count)
            )
            return normalized

        return ()

    @staticmethod
    def _compute_rms(samples: tuple[float, ...] | list[float]) -> float:
        """Return the root-mean-square amplitude for the provided samples."""

        if not samples:
            return 0.0
        mean_square = sum(sample * sample for sample in samples) / len(samples)
        return math.sqrt(mean_square)

    @staticmethod
    def _compute_zero_crossing_rate(
        samples: tuple[float, ...] | list[float],
    ) -> float:
        """Return the share of adjacent samples that cross zero."""

        if len(samples) < 2:
            return 0.0

        crossing_count = 0
        previous_sign = 1 if samples[0] >= 0.0 else -1
        for sample in samples[1:]:
            current_sign = 1 if sample >= 0.0 else -1
            if current_sign != previous_sign:
                crossing_count += 1
            previous_sign = current_sign
        return crossing_count / (len(samples) - 1)
