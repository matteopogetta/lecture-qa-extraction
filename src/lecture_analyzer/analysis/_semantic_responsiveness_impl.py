"""Optional local embedding scorer for QA semantic responsiveness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, Sequence


class SemanticResponsivenessUnavailableError(RuntimeError):
    """Raised when the local semantic responsiveness backend cannot be used."""


@dataclass(slots=True)
class SemanticResponsivenessInput:
    """One already-extracted QA candidate to score semantically."""

    candidate_index: int
    question_text: str
    answer_text: str
    continuation_text: str | None = None


@dataclass(slots=True)
class SemanticResponsivenessScore:
    """Semantic responsiveness score for one already-extracted candidate."""

    candidate_index: int
    score: float
    question_answer_similarity: float
    answer_continuation_similarity: float | None = None
    echo_penalty: float = 0.0
    continuation_penalty: float = 0.0
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticResponsivenessBackend(Protocol):
    """Protocol implemented by local embedding responsiveness backends."""

    backend_name: str
    model_name: str

    @property
    def load_seconds(self) -> float | None:
        """Return model load seconds after lazy initialization."""

    @property
    def model_footprint_bytes(self) -> int | None:
        """Return local model footprint bytes when known."""

    def score_candidates(
        self,
        candidates: Sequence[SemanticResponsivenessInput],
    ) -> list[SemanticResponsivenessScore]:
        """Return semantic responsiveness scores for extracted candidates only."""


class SentenceTransformerResponsivenessBackend:
    """Local sentence-transformers embedding backend with no runtime downloads."""

    backend_name = "sentence_transformers"

    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        *,
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any | None = None
        self._resolved_model_path: Path | None = None
        self._load_seconds: float | None = None
        self._model_footprint_bytes: int | None = None

    @property
    def load_seconds(self) -> float | None:
        """Return model load seconds after lazy initialization."""

        return self._load_seconds

    @property
    def model_footprint_bytes(self) -> int | None:
        """Return local model footprint bytes when known."""

        return self._model_footprint_bytes

    def score_candidates(
        self,
        candidates: Sequence[SemanticResponsivenessInput],
    ) -> list[SemanticResponsivenessScore]:
        """Score already-extracted candidates with local sentence embeddings."""

        if not candidates:
            return []
        model = self._load_model()
        texts = self._unique_texts(candidates)
        if not texts:
            return []

        try:
            embeddings = model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticResponsivenessUnavailableError(
                f"Failed to encode responsiveness texts with {self.model_name}.",
            ) from exc

        vector_by_text = {
            text: embeddings[index]
            for index, text in enumerate(texts)
        }
        scores: list[SemanticResponsivenessScore] = []
        for candidate in candidates:
            started_at = perf_counter()
            question_answer_similarity = self._cosine(
                vector_by_text.get(candidate.question_text),
                vector_by_text.get(candidate.answer_text),
            )
            continuation_similarity = (
                self._cosine(
                    vector_by_text.get(candidate.answer_text),
                    vector_by_text.get(candidate.continuation_text or ""),
                )
                if candidate.continuation_text
                else None
            )
            echo_penalty = self._echo_penalty(
                question_text=candidate.question_text,
                answer_text=candidate.answer_text,
                question_answer_similarity=question_answer_similarity,
            )
            continuation_penalty = self._continuation_penalty(
                question_answer_similarity=question_answer_similarity,
                answer_continuation_similarity=continuation_similarity,
            )
            score = max(
                0.0,
                min(
                    1.0,
                    0.18
                    + (0.82 * question_answer_similarity)
                    - echo_penalty
                    - continuation_penalty,
                ),
            )
            scores.append(
                SemanticResponsivenessScore(
                    candidate_index=candidate.candidate_index,
                    score=round(score, 4),
                    question_answer_similarity=round(question_answer_similarity, 4),
                    answer_continuation_similarity=(
                        round(continuation_similarity, 4)
                        if continuation_similarity is not None
                        else None
                    ),
                    echo_penalty=round(echo_penalty, 4),
                    continuation_penalty=round(continuation_penalty, 4),
                    elapsed_seconds=round(perf_counter() - started_at, 6),
                    metadata={
                        "model_name": self.model_name,
                        "backend": self.backend_name,
                        "resolved_model_path": (
                            str(self._resolved_model_path)
                            if self._resolved_model_path is not None
                            else None
                        ),
                    },
                ),
            )
        return scores

    def _load_model(self) -> Any:
        """Load the model from an existing local path/cache only."""

        if self._model is not None:
            return self._model

        started_at = perf_counter()
        resolved_path = self._resolve_local_model_path()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise SemanticResponsivenessUnavailableError(
                "sentence-transformers is not installed.",
            ) from exc

        try:
            self._model = SentenceTransformer(str(resolved_path), device=self.device)
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticResponsivenessUnavailableError(
                f"Unable to load local semantic responsiveness model {self.model_name}.",
            ) from exc
        self._resolved_model_path = resolved_path
        self._model_footprint_bytes = self._directory_size(resolved_path)
        self._load_seconds = round(perf_counter() - started_at, 6)
        return self._model

    def _resolve_local_model_path(self) -> Path:
        """Resolve a local model path without triggering network downloads."""

        direct_path = Path(self.model_name).expanduser()
        if direct_path.exists():
            return direct_path.resolve()

        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise SemanticResponsivenessUnavailableError(
                "huggingface_hub is not installed, and model_name is not a local path.",
            ) from exc

        try:
            return Path(
                snapshot_download(
                    repo_id=self.model_name,
                    local_files_only=True,
                ),
            ).resolve()
        except Exception as exc:
            raise SemanticResponsivenessUnavailableError(
                f"Local semantic responsiveness model not found: {self.model_name}.",
            ) from exc

    @staticmethod
    def _unique_texts(
        candidates: Sequence[SemanticResponsivenessInput],
    ) -> list[str]:
        """Return non-empty unique texts from candidate-local fields only."""

        texts: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            for text in (
                candidate.question_text,
                candidate.answer_text,
                candidate.continuation_text or "",
            ):
                stripped = text.strip()
                if not stripped or stripped in seen:
                    continue
                seen.add(stripped)
                texts.append(stripped)
        return texts

    @staticmethod
    def _cosine(left: Any, right: Any) -> float:
        """Return normalized cosine similarity for already-normalized vectors."""

        if left is None or right is None:
            return 0.0
        try:
            similarity = float(left @ right)
        except Exception:
            return 0.0
        return max(0.0, min(1.0, similarity))

    @staticmethod
    def _echo_penalty(
        *,
        question_text: str,
        answer_text: str,
        question_answer_similarity: float,
    ) -> float:
        """Return penalty for answers that mostly echo the question."""

        question_tokens = _content_tokens(question_text)
        answer_tokens = _content_tokens(answer_text)
        if not question_tokens or not answer_tokens:
            return 0.0
        overlap_ratio = len(question_tokens & answer_tokens) / max(1, len(answer_tokens))
        if question_answer_similarity >= 0.88 and overlap_ratio >= 0.72:
            return 0.24
        if question_answer_similarity >= 0.82 and overlap_ratio >= 0.62:
            return 0.14
        return 0.0

    @staticmethod
    def _continuation_penalty(
        *,
        question_answer_similarity: float,
        answer_continuation_similarity: float | None,
    ) -> float:
        """Return penalty when an answer looks more like local continuation."""

        if answer_continuation_similarity is None:
            return 0.0
        margin = answer_continuation_similarity - question_answer_similarity
        if margin >= 0.12:
            return 0.22
        if margin >= 0.06:
            return 0.12
        return 0.0

    @staticmethod
    def _directory_size(path: Path) -> int | None:
        """Return recursive local model footprint in bytes when available."""

        if not path.exists():
            return None
        if path.is_file():
            return path.stat().st_size
        total = 0
        try:
            for child in path.rglob("*"):
                if child.is_file():
                    total += child.stat().st_size
        except OSError:
            return None
        return total


def _content_tokens(text: str) -> set[str]:
    """Return coarse content tokens without importing QA rule internals."""

    import re

    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "che",
        "di",
        "do",
        "does",
        "e",
        "for",
        "how",
        "il",
        "in",
        "is",
        "it",
        "la",
        "of",
        "on",
        "or",
        "per",
        "the",
        "to",
        "un",
        "una",
        "what",
        "when",
        "where",
        "why",
    }
    return {
        token
        for token in re.findall(r"[\w']+", text.lower())
        if len(token) > 1 and token not in stopwords
    }
