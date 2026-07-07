"""Optional QA speaker-change check based on local speaker embeddings."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Protocol, Sequence
import math
import re
import wave

import numpy as np

from lecture_analyzer.core.models import AudioSource, LectureSession, QAPairCandidate
from lecture_analyzer.core.models import TimeRange, Utterance


SPEAKER_CHECK_UNAVAILABLE = "speaker_check_unavailable"
SAME_SPEAKER_SUSPECTED = "same_speaker_suspected"
DIFFERENT_SPEAKER_LIKELY = "different_speaker_likely"
SPEAKER_CHECK_OVERLAPPING_SPANS = "speaker_check_overlapping_spans"
SPEAKER_CHECK_QUESTION_SPAN_EXTENDED = "speaker_check_question_span_extended"
SPEAKER_PENALTY_WAIVED_RESPONSIVE = "speaker_penalty_waived_responsive"
SPEAKER_RESCUED_CANDIDATE = "speaker_rescued_candidate"
SPEAKER_RESCUE_REJECTED_TEXT_QUALITY = "speaker_rescue_rejected_text_quality"
SPEAKER_RESCUE_REJECTED_CONVERSATIONAL = "speaker_rescue_rejected_conversational"

_SOCRATIC_REASON_RE = re.compile(
    r"(?:socratic|self_answer|self-answered|same_unit)",
    flags=re.IGNORECASE,
)
_DEFINITION_QUESTION_RE = re.compile(
    r"\b(?:"
    r"cosa\s+(?:vuol\s+dire|significa)|che\s+cos[' ]?e|"
    r"what\s+(?:does|do)\b.{0,80}\bmean|what\s+is|what\s+are"
    r")\b",
    flags=re.IGNORECASE,
)
_DEFINITION_ANSWER_RE = re.compile(
    r"^\s*(?:"
    r"significa|vuol\s+dire|cioe|cioè|"
    r"(?:it|this|that)\s+means|(?:it|this|that)\s+is|"
    r"[a-z][\w' -]{0,80}\s+(?:means|is|are)"
    r")\b",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class SpeakerCheckConfig:
    """Configuration for the optional QA speaker check."""

    enabled: bool = False
    model_path: Path | None = None
    min_span_seconds: float = 1.5
    same_speaker_threshold: float = 0.72
    same_speaker_full_penalty_threshold: float = 0.85
    different_speaker_threshold: float = 0.45
    same_speaker_penalty: float = 0.25
    different_speaker_bonus: float = 0.04
    max_span_extension_seconds: float = 3.0
    rescue_max_checks_per_run: int = 40
    rescue_max_candidates_per_run: int = 8
    rescue_min_confidence_margin: float = 0.08
    rescue_min_text_quality_score: float = 0.50


@dataclass(slots=True)
class SpeakerCheckResult:
    """Per-candidate result from the speaker check."""

    status: str
    similarity_score: float | None = None
    flags: list[str] = field(default_factory=list)
    check_seconds: float = 0.0
    question_duration_seconds: float | None = None
    answer_duration_seconds: float | None = None
    original_question_duration_seconds: float | None = None
    penalty_applied: float = 0.0
    bonus_applied: float = 0.0
    exempted_by_socratic_pattern: bool = False
    waived_by_responsive_pattern: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "status": self.status,
            "speaker_similarity_score": self.similarity_score,
            "flags": list(self.flags),
            "check_seconds": round(self.check_seconds, 4),
            "question_duration_seconds": self.question_duration_seconds,
            "answer_duration_seconds": self.answer_duration_seconds,
            "original_question_duration_seconds": (
                self.original_question_duration_seconds
            ),
            "penalty_applied": round(self.penalty_applied, 4),
            "bonus_applied": round(self.bonus_applied, 4),
            "exempted_by_socratic_pattern": self.exempted_by_socratic_pattern,
            "waived_by_responsive_pattern": self.waived_by_responsive_pattern,
            "note": self.note,
        }


@dataclass(slots=True)
class SpeakerCheckMetrics:
    """Aggregate metrics for the QA speaker check."""

    enabled: bool
    model_available: bool
    model_path: str | None
    model_load_seconds: float = 0.0
    total_check_seconds: float = 0.0
    per_candidate_check_seconds: dict[str, float] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)
    checked_candidate_count: int = 0
    unavailable_candidate_count: int = 0
    skipped_candidate_count: int = 0
    precomputed_candidate_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "enabled": self.enabled,
            "model_available": self.model_available,
            "model_path": self.model_path,
            "model_load_seconds": round(self.model_load_seconds, 4),
            "total_check_seconds": round(self.total_check_seconds, 4),
            "per_candidate_check_seconds": {
                key: round(value, 4)
                for key, value in sorted(self.per_candidate_check_seconds.items())
            },
            "checked_candidate_count": self.checked_candidate_count,
            "unavailable_candidate_count": self.unavailable_candidate_count,
            "skipped_candidate_count": self.skipped_candidate_count,
            "precomputed_candidate_count": self.precomputed_candidate_count,
            "flag_counts": dict(sorted(self.flag_counts.items())),
            "notes": list(self.notes),
        }


class SpeakerEmbeddingBackendProtocol(Protocol):
    """Minimal backend protocol used by the speaker check service."""

    model_name: str

    def is_available(self) -> bool:
        """Return whether the local embedding model can be used."""

    def embed(self, audio_path: Path) -> np.ndarray:
        """Return one embedding vector for the audio clip."""


class SpeechBrainSpeakerEmbeddingBackend:
    """SpeechBrain ECAPA backend loaded from an existing local model directory."""

    model_name = "speechbrain_ecapa_local"

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path.expanduser().resolve()
        self._classifier: Any | None = None
        self._load_error: str | None = None

    def is_available(self) -> bool:
        """Return whether the model directory exists and SpeechBrain can load it."""

        if not self.model_path.exists():
            self._load_error = "model_path_missing"
            return False
        try:
            self._load_classifier()
        except Exception as exc:  # pragma: no cover - depends on optional runtime
            self._load_error = str(exc)
            return False
        return True

    def embed(self, audio_path: Path) -> np.ndarray:
        """Return an embedding for one clip."""

        classifier = self._load_classifier()
        if hasattr(classifier, "encode_file"):
            embedding = classifier.encode_file(str(audio_path))
        else:
            # SpeechBrain >=1.0: Pretrained espone load_audio + encode_batch.
            waveform = classifier.load_audio(str(audio_path))
            if hasattr(waveform, "dim") and waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            embedding = classifier.encode_batch(waveform)
        if hasattr(embedding, "detach"):
            embedding = embedding.detach().cpu().numpy()
        return np.asarray(embedding, dtype=float).reshape(-1)

    @property
    def load_error(self) -> str | None:
        """Return the last backend loading error."""

        return self._load_error

    def _load_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except Exception:
            from speechbrain.pretrained import EncoderClassifier  # type: ignore

        self._classifier = EncoderClassifier.from_hparams(
            source=str(self.model_path),
            savedir=str(self.model_path),
            run_opts={"device": "cpu"},
        )
        return self._classifier


class QASpeakerCheckService:
    """Apply the optional speaker-change check to extracted QA candidates."""

    def __init__(
        self,
        config: SpeakerCheckConfig,
        *,
        backend: SpeakerEmbeddingBackendProtocol | None = None,
    ) -> None:
        self.config = config
        self._provided_backend = backend
        self._backend: SpeakerEmbeddingBackendProtocol | None = None
        self._backend_checked = False
        self._model_load_seconds = 0.0
        self._notes: list[str] = []

    @property
    def model_load_seconds(self) -> float:
        """Return time spent loading or probing the model."""

        self._ensure_backend()
        return self._model_load_seconds

    def model_available(self) -> bool:
        """Return whether embeddings can be computed."""

        return self._ensure_backend() is not None

    def notes(self) -> list[str]:
        """Return model/backend diagnostic notes."""

        self._ensure_backend()
        return list(self._notes)

    def check(
        self,
        candidate: QAPairCandidate,
        *,
        audio_sources_by_id: dict[str, AudioSource],
        utterances_by_id: dict[str, Utterance] | None = None,
        temp_directory: Path,
    ) -> SpeakerCheckResult:
        """Return speaker-check result for one candidate."""

        started = monotonic()
        if _candidate_has_overlapping_spans(candidate):
            return SpeakerCheckResult(
                status="skipped",
                flags=[SPEAKER_CHECK_OVERLAPPING_SPANS],
                check_seconds=monotonic() - started,
                question_duration_seconds=_timing_duration(candidate.question_timing),
                answer_duration_seconds=_timing_duration(candidate.answer_timing),
                note="overlapping_question_answer_spans",
            )

        question_timing = candidate.question_timing
        question_duration = _timing_duration(candidate.question_timing)
        answer_duration = _timing_duration(candidate.answer_timing)
        original_question_duration = question_duration
        span_flags: list[str] = []
        span_note: str | None = None
        if (
            question_duration is not None
            and question_duration < self.config.min_span_seconds
        ):
            expanded_timing = _expanded_question_audio_timing(
                candidate,
                utterances_by_id=utterances_by_id or {},
                max_extension_seconds=self.config.max_span_extension_seconds,
            )
            expanded_duration = _timing_duration(expanded_timing)
            if (
                expanded_timing is not None
                and expanded_duration is not None
                and expanded_duration >= self.config.min_span_seconds
            ):
                question_timing = expanded_timing
                question_duration = expanded_duration
                span_flags.append(SPEAKER_CHECK_QUESTION_SPAN_EXTENDED)
                span_note = "question_audio_span_extended_to_utterance"

        if (
            question_duration is None
            or answer_duration is None
            or question_duration < self.config.min_span_seconds
            or answer_duration < self.config.min_span_seconds
        ):
            return SpeakerCheckResult(
                status="unavailable",
                flags=[SPEAKER_CHECK_UNAVAILABLE],
                check_seconds=monotonic() - started,
                question_duration_seconds=question_duration,
                answer_duration_seconds=answer_duration,
                original_question_duration_seconds=original_question_duration,
                note="span_too_short_or_missing",
            )

        backend = self._ensure_backend()
        if backend is None:
            return SpeakerCheckResult(
                status="unavailable",
                flags=[SPEAKER_CHECK_UNAVAILABLE],
                check_seconds=monotonic() - started,
                question_duration_seconds=question_duration,
                answer_duration_seconds=answer_duration,
                original_question_duration_seconds=original_question_duration,
                note="model_not_available",
            )

        try:
            question_clip = _extract_candidate_clip(
                candidate,
                timing_kind="question",
                timing_override=question_timing,
                audio_sources_by_id=audio_sources_by_id,
                temp_directory=temp_directory,
            )
            answer_clip = _extract_candidate_clip(
                candidate,
                timing_kind="answer",
                timing_override=None,
                audio_sources_by_id=audio_sources_by_id,
                temp_directory=temp_directory,
            )
            question_embedding = backend.embed(question_clip)
            answer_embedding = backend.embed(answer_clip)
            similarity = _cosine_similarity(question_embedding, answer_embedding)
        except Exception as exc:
            return SpeakerCheckResult(
                status="unavailable",
                flags=[SPEAKER_CHECK_UNAVAILABLE],
                check_seconds=monotonic() - started,
                question_duration_seconds=question_duration,
                answer_duration_seconds=answer_duration,
                original_question_duration_seconds=original_question_duration,
                note=f"check_failed: {exc}",
            )

        flags: list[str] = list(span_flags)
        status = "checked"
        if similarity >= self.config.same_speaker_threshold:
            flags.append(SAME_SPEAKER_SUSPECTED)
            status = SAME_SPEAKER_SUSPECTED
        elif similarity <= self.config.different_speaker_threshold:
            flags.append(DIFFERENT_SPEAKER_LIKELY)
            status = DIFFERENT_SPEAKER_LIKELY

        exempted = _has_socratic_pattern(candidate)
        responsive_waiver = (
            SAME_SPEAKER_SUSPECTED in flags
            and not exempted
            and _has_responsive_definition_waiver(candidate)
        )
        penalty = 0.0
        if SAME_SPEAKER_SUSPECTED in flags and not exempted and not responsive_waiver:
            penalty = _same_speaker_penalty_for_similarity(
                similarity,
                config=self.config,
            )
        bonus = (
            self.config.different_speaker_bonus
            if DIFFERENT_SPEAKER_LIKELY in flags
            else 0.0
        )
        return SpeakerCheckResult(
            status=status,
            similarity_score=round(similarity, 4),
            flags=flags,
            check_seconds=monotonic() - started,
            question_duration_seconds=question_duration,
            answer_duration_seconds=answer_duration,
            original_question_duration_seconds=original_question_duration,
            penalty_applied=penalty,
            bonus_applied=bonus,
            exempted_by_socratic_pattern=exempted,
            waived_by_responsive_pattern=responsive_waiver,
            note=span_note,
        )

    def _ensure_backend(self) -> SpeakerEmbeddingBackendProtocol | None:
        if self._backend_checked:
            return self._backend
        self._backend_checked = True
        if not self.config.enabled:
            self._notes.append("speaker_check_disabled")
            return None
        if self._backend is not None:
            return self._backend

        started = monotonic()
        backend = self._provided_backend
        if backend is None and self.config.model_path is not None:
            backend = SpeechBrainSpeakerEmbeddingBackend(self.config.model_path)
        if backend is None:
            self._notes.append("model_not_configured_or_missing")
            self._model_load_seconds += monotonic() - started
            return None

        if not backend.is_available():
            self._notes.append("model_not_available")
            load_error = getattr(backend, "load_error", None)
            if load_error:
                self._notes.append(f"model_error: {load_error}")
            self._model_load_seconds += monotonic() - started
            return None

        self._backend = backend
        self._model_load_seconds += monotonic() - started
        return self._backend


def apply_qa_speaker_check(
    session: LectureSession,
    config: SpeakerCheckConfig,
    *,
    service: QASpeakerCheckService | None = None,
) -> SpeakerCheckMetrics:
    """Annotate session QA candidates with optional speaker-check diagnostics."""

    checker = service or QASpeakerCheckService(config)
    audio_sources_by_id = {
        audio_source.audio_source_id: audio_source
        for audio_source in session.audio_sources
    }
    utterances_by_id = {
        utterance.utterance_id: utterance
        for utterance in session.utterances
    }
    metrics = SpeakerCheckMetrics(
        enabled=config.enabled,
        model_available=checker.model_available(),
        model_path=str(config.model_path) if config.model_path is not None else None,
        model_load_seconds=checker.model_load_seconds,
        notes=checker.notes(),
    )
    flag_counter: Counter[str] = Counter()
    total_started = monotonic()

    if not config.enabled:
        session.metadata["qa_speaker_check"] = metrics.to_dict()
        return metrics

    with TemporaryDirectory(prefix="qa_speaker_check_") as temp_root:
        temp_directory = Path(temp_root)
        for candidate in session.qa_candidates:
            precomputed_result = _precomputed_result_from_candidate(candidate)
            if precomputed_result is not None:
                result = precomputed_result
                metrics.precomputed_candidate_count += 1
            else:
                result = checker.check(
                    candidate,
                    audio_sources_by_id=audio_sources_by_id,
                    utterances_by_id=utterances_by_id,
                    temp_directory=temp_directory,
                )
                _apply_result_to_candidate(candidate, result)
            metrics.per_candidate_check_seconds[candidate.qa_candidate_id] = (
                result.check_seconds
            )
            if result.status == "skipped":
                metrics.skipped_candidate_count += 1
            elif result.status == "unavailable":
                metrics.unavailable_candidate_count += 1
            else:
                metrics.checked_candidate_count += 1
            flag_counter.update(result.flags)

    metrics.total_check_seconds = monotonic() - total_started
    metrics.flag_counts = dict(flag_counter)
    session.metadata["qa_speaker_check"] = metrics.to_dict()
    coverage = session.metadata.get("qa_coverage")
    if isinstance(coverage, dict):
        coverage["speaker_check_flag_counts"] = dict(sorted(flag_counter.items()))
        coverage["speaker_check_checked_candidate_count"] = metrics.checked_candidate_count
        coverage["speaker_check_unavailable_candidate_count"] = (
            metrics.unavailable_candidate_count
        )
        coverage["speaker_check_skipped_candidate_count"] = metrics.skipped_candidate_count
        coverage["speaker_check_precomputed_candidate_count"] = (
            metrics.precomputed_candidate_count
        )
    return metrics


def _precomputed_result_from_candidate(
    candidate: QAPairCandidate,
) -> SpeakerCheckResult | None:
    payload = candidate.metadata.get("speaker_check")
    if not isinstance(payload, dict):
        return None
    if not candidate.metadata.get("speaker_check_precomputed"):
        return None
    flags = [str(flag) for flag in payload.get("flags") or [] if str(flag).strip()]
    return SpeakerCheckResult(
        status=str(payload.get("status") or "checked"),
        similarity_score=_safe_optional_float(payload.get("speaker_similarity_score")),
        flags=flags,
        check_seconds=float(payload.get("check_seconds") or 0.0),
        question_duration_seconds=_safe_optional_float(
            payload.get("question_duration_seconds"),
        ),
        answer_duration_seconds=_safe_optional_float(
            payload.get("answer_duration_seconds"),
        ),
        original_question_duration_seconds=_safe_optional_float(
            payload.get("original_question_duration_seconds"),
        ),
        penalty_applied=float(payload.get("penalty_applied") or 0.0),
        bonus_applied=float(payload.get("bonus_applied") or 0.0),
        exempted_by_socratic_pattern=bool(
            payload.get("exempted_by_socratic_pattern"),
        ),
        waived_by_responsive_pattern=bool(
            payload.get("waived_by_responsive_pattern"),
        ),
        note=(
            str(payload.get("note"))
            if payload.get("note") is not None
            else None
        ),
    )


def _safe_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_result_to_candidate(
    candidate: QAPairCandidate,
    result: SpeakerCheckResult,
) -> None:
    candidate.metadata["speaker_check"] = result.to_dict()
    if result.similarity_score is not None:
        candidate.metadata["speaker_similarity_score"] = result.similarity_score
    if result.flags:
        candidate.review_flags = _unique_strings(
            list(candidate.review_flags) + result.flags,
        )
        candidate.reason_codes = _unique_strings(
            list(candidate.reason_codes) + result.flags,
        )
    if result.waived_by_responsive_pattern:
        candidate.reason_codes = _unique_strings(
            list(candidate.reason_codes) + [SPEAKER_PENALTY_WAIVED_RESPONSIVE],
        )
    if result.penalty_applied > 0.0:
        original_confidence = float(candidate.confidence)
        candidate.confidence = round(
            max(0.0, original_confidence - result.penalty_applied),
            4,
        )
        candidate.confidence_score = candidate.confidence
        candidate.confidence_label = _confidence_label(candidate.confidence)
        candidate.metadata["speaker_check"]["original_confidence"] = round(
            original_confidence,
            4,
        )
        candidate.metadata["speaker_check"]["final_confidence"] = round(
            candidate.confidence,
            4,
        )
    elif result.bonus_applied > 0.0:
        original_confidence = float(candidate.confidence)
        candidate.confidence = round(
            min(1.0, original_confidence + result.bonus_applied),
            4,
        )
        candidate.confidence_score = candidate.confidence
        candidate.confidence_label = _confidence_label(candidate.confidence)
        candidate.metadata["speaker_check"]["original_confidence"] = round(
            original_confidence,
            4,
        )
        candidate.metadata["speaker_check"]["final_confidence"] = round(
            candidate.confidence,
            4,
        )


def _extract_candidate_clip(
    candidate: QAPairCandidate,
    *,
    timing_kind: str,
    timing_override: TimeRange | None,
    audio_sources_by_id: dict[str, AudioSource],
    temp_directory: Path,
) -> Path:
    timing = (
        timing_override
        or (candidate.question_timing if timing_kind == "question" else candidate.answer_timing)
    )
    if timing is None or timing.audio_source_id is None:
        raise ValueError(f"{timing_kind}_timing_missing_audio_source")
    audio_source = audio_sources_by_id.get(timing.audio_source_id)
    if audio_source is None:
        raise ValueError(f"audio_source_missing: {timing.audio_source_id}")
    output_path = temp_directory / f"{candidate.qa_candidate_id}_{timing_kind}.wav"
    _extract_wav_span(
        audio_source.audio_path.expanduser().resolve(),
        output_path,
        start_seconds=max(0.0, float(timing.start_seconds)),
        end_seconds=max(0.0, float(timing.end_seconds)),
    )
    return output_path


def _extract_wav_span(
    source_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    end_seconds: float,
) -> None:
    if end_seconds <= start_seconds:
        raise ValueError("invalid_span_timing")
    with wave.open(str(source_path), "rb") as source:
        params = source.getparams()
        frame_rate = source.getframerate()
        start_frame = max(0, int(start_seconds * frame_rate))
        end_frame = min(source.getnframes(), int(math.ceil(end_seconds * frame_rate)))
        frame_count = max(0, end_frame - start_frame)
        if frame_count <= 0:
            raise ValueError("empty_audio_span")
        source.setpos(start_frame)
        frames = source.readframes(frame_count)
    with wave.open(str(output_path), "wb") as target:
        target.setparams(params)
        target.writeframes(frames)


def _timing_duration(timing: Any) -> float | None:
    if timing is None:
        return None
    return max(0.0, float(timing.end_seconds) - float(timing.start_seconds))


def _candidate_has_overlapping_spans(candidate: QAPairCandidate) -> bool:
    """Return whether question/answer spans share the same textual or audio span."""

    if set(candidate.question_sentence_ids) & set(candidate.answer_sentence_ids):
        return True
    if set(candidate.question_unit_ids) & set(candidate.answer_unit_ids):
        return True
    question_timing = candidate.question_timing
    answer_timing = candidate.answer_timing
    if (
        question_timing is None
        or answer_timing is None
        or question_timing.audio_source_id != answer_timing.audio_source_id
    ):
        return False
    return (
        min(float(question_timing.end_seconds), float(answer_timing.end_seconds))
        > max(float(question_timing.start_seconds), float(answer_timing.start_seconds))
    )


def _expanded_question_audio_timing(
    candidate: QAPairCandidate,
    *,
    utterances_by_id: dict[str, Utterance],
    max_extension_seconds: float,
) -> TimeRange | None:
    """Return an audio-only question span expanded within its containing utterance."""

    timing = candidate.question_timing
    if timing is None or timing.audio_source_id is None:
        return None
    containers = [
        utterances_by_id[utterance_id]
        for utterance_id in candidate.question_source_utterance_ids
        if utterance_id in utterances_by_id
    ]
    if not containers:
        return None
    start_floor = min(float(utterance.start_seconds) for utterance in containers)
    end_ceiling = max(float(utterance.end_seconds) for utterance in containers)
    original_start = float(timing.start_seconds)
    original_end = float(timing.end_seconds)
    expanded_start = max(start_floor, original_start - max_extension_seconds)
    expanded_end = min(end_ceiling, original_end + max_extension_seconds)
    padded_timing = _padded_question_audio_timing(
        candidate,
        max_extension_seconds=max_extension_seconds,
    )
    if padded_timing is not None and (
        expanded_end <= expanded_start
        or _timing_duration(padded_timing) > max(0.0, expanded_end - expanded_start)
    ):
        return padded_timing
    if expanded_end <= expanded_start:
        return padded_timing
    return TimeRange(
        start_seconds=expanded_start,
        end_seconds=expanded_end,
        audio_source_id=timing.audio_source_id,
        session_start_seconds=(
            None
            if timing.session_start_seconds is None
            else max(
                float(timing.session_start_seconds) - (original_start - expanded_start),
                min(
                    (float(utterance.session_start_seconds) for utterance in containers if utterance.session_start_seconds is not None),
                    default=float(timing.session_start_seconds),
                ),
            )
        ),
        session_end_seconds=(
            None
            if timing.session_end_seconds is None
            else min(
                float(timing.session_end_seconds) + (expanded_end - original_end),
                max(
                    (float(utterance.session_end_seconds) for utterance in containers if utterance.session_end_seconds is not None),
                    default=float(timing.session_end_seconds),
                ),
            )
        ),
    )


def _padded_question_audio_timing(
    candidate: QAPairCandidate,
    *,
    max_extension_seconds: float,
) -> TimeRange | None:
    """Return a bounded audio-only padding span that avoids answer overlap."""

    timing = candidate.question_timing
    if timing is None or timing.audio_source_id is None:
        return None
    original_start = float(timing.start_seconds)
    original_end = float(timing.end_seconds)
    expanded_start = max(0.0, original_start - max_extension_seconds)
    expanded_end = original_end + max_extension_seconds
    answer_timing = candidate.answer_timing
    if (
        answer_timing is not None
        and answer_timing.audio_source_id == timing.audio_source_id
        and float(answer_timing.start_seconds) > original_end
    ):
        expanded_end = min(expanded_end, float(answer_timing.start_seconds))
    if expanded_end <= expanded_start:
        return None
    return TimeRange(
        start_seconds=expanded_start,
        end_seconds=expanded_end,
        audio_source_id=timing.audio_source_id,
        session_start_seconds=(
            None
            if timing.session_start_seconds is None
            else max(0.0, float(timing.session_start_seconds) - max_extension_seconds)
        ),
        session_end_seconds=(
            None
            if timing.session_end_seconds is None
            else float(timing.session_end_seconds) + (expanded_end - original_end)
        ),
    )


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_vector = np.asarray(left, dtype=float).reshape(-1)
    right_vector = np.asarray(right, dtype=float).reshape(-1)
    denominator = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
    if denominator <= 0.0:
        raise ValueError("zero_embedding_norm")
    return float(np.dot(left_vector, right_vector) / denominator)


def _has_socratic_pattern(candidate: QAPairCandidate) -> bool:
    reason_set = set(candidate.reason_codes)
    if reason_set & {
        "socratic_short_answer_support",
        "same_unit_local_answer",
        "same_sentence_answer",
        "answer_in_same_sentence",
    }:
        return True
    values: list[str] = [
        candidate.question_type,
        *candidate.reason_codes,
        str(candidate.metadata.get("input_layer") or ""),
    ]
    for key in (
        "question_debug",
        "answer_debug",
        "pairing_debug",
        "quality_features",
    ):
        values.extend(_flatten_strings(candidate.metadata.get(key)))
    return any(_SOCRATIC_REASON_RE.search(value) for value in values if value)


def _has_responsive_definition_waiver(candidate: QAPairCandidate) -> bool:
    """Return whether a same-speaker hit is a legitimate self-answered definition."""

    reason_set = set(candidate.reason_codes)
    if not {
        "answer_responsiveness_strong",
        "answer_responsiveness_anchor",
    }.issubset(reason_set):
        return False
    normalized_question = str(candidate.question_text or "").strip().lower()
    normalized_answer = str(candidate.answer_text or "").strip().lower()
    if not normalized_question or not normalized_answer:
        return False
    if _DEFINITION_QUESTION_RE.search(normalized_question):
        return bool(
            _DEFINITION_ANSWER_RE.search(normalized_answer)
            or reason_set
            & {
                "answer_it_cioe",
                "answer_it_cioè",
                "answer_definition_cue",
            }
        )
    return False


def _same_speaker_penalty_for_similarity(
    similarity: float,
    *,
    config: SpeakerCheckConfig,
) -> float:
    """Return a linear same-speaker penalty between threshold and full threshold."""

    same_threshold = float(config.same_speaker_threshold)
    full_threshold = max(
        same_threshold,
        float(config.same_speaker_full_penalty_threshold),
    )
    penalty = float(config.same_speaker_penalty)
    if similarity < same_threshold or penalty <= 0.0:
        return 0.0
    if similarity >= full_threshold or full_threshold <= same_threshold:
        return penalty
    ratio = (similarity - same_threshold) / (full_threshold - same_threshold)
    return round(max(0.0, min(1.0, ratio)) * penalty, 4)


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        flattened: list[str] = []
        for item in value.values():
            flattened.extend(_flatten_strings(item))
        return flattened
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        flattened = []
        for item in value:
            flattened.extend(_flatten_strings(item))
        return flattened
    return []


def _unique_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"
