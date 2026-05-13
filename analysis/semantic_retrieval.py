"""Optional semantic retrieval helpers for QA answer search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class SemanticRetrievalUnavailableError(RuntimeError):
    """Raised when the semantic retrieval backend cannot be used."""


@dataclass(slots=True)
class SemanticSearchHit:
    """One semantic retrieval hit returned for a candidate passage."""

    passage_index: int
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticRetrieverBackend(Protocol):
    """Protocol implemented by semantic retrieval backends."""

    backend_name: str
    model_name: str

    def retrieve(
        self,
        *,
        query_text: str,
        passage_texts: Sequence[str],
        top_k: int,
        min_similarity: float | None = None,
    ) -> list[SemanticSearchHit]:
        """Return the top semantic hits for the provided passages."""


class SentenceTransformersE5Backend:
    """Semantic retriever backed by a multilingual-e5 SentenceTransformer."""

    backend_name = "sentence_transformers"

    def __init__(self, model_name: str = "intfloat/multilingual-e5-base") -> None:
        self.model_name = model_name
        self._model: Any | None = None

    def retrieve(
        self,
        *,
        query_text: str,
        passage_texts: Sequence[str],
        top_k: int,
        min_similarity: float | None = None,
    ) -> list[SemanticSearchHit]:
        """Return semantic hits using cosine similarity on E5 embeddings."""

        if not passage_texts:
            return []

        model = self._load_model()
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRetrievalUnavailableError(
                "Semantic retrieval requires numpy-compatible embeddings support.",
            ) from exc

        prefixed_query = [self._prefix_query(query_text)]
        prefixed_passages = [
            self._prefix_passage(passage_text) for passage_text in passage_texts
        ]

        try:
            query_embedding = model.encode(
                prefixed_query,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            passage_embeddings = model.encode(
                prefixed_passages,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticRetrievalUnavailableError(
                f"Failed to encode semantic retrieval inputs with {self.model_name}.",
            ) from exc

        scores = np.matmul(passage_embeddings, query_embedding[0])
        ranked_indexes = sorted(
            range(len(passage_texts)),
            key=lambda passage_index: float(scores[passage_index]),
            reverse=True,
        )

        hits: list[SemanticSearchHit] = []
        similarity_threshold = (
            float(min_similarity) if min_similarity is not None else None
        )
        for passage_index in ranked_indexes:
            score = float(scores[passage_index])
            if similarity_threshold is not None and score < similarity_threshold:
                continue
            hits.append(
                SemanticSearchHit(
                    passage_index=passage_index,
                    score=score,
                    text=str(passage_texts[passage_index]),
                    metadata={"model_name": self.model_name},
                ),
            )
            if len(hits) >= max(1, int(top_k)):
                break
        return hits

    def _load_model(self) -> Any:
        """Load and cache the configured SentenceTransformer model lazily."""

        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRetrievalUnavailableError(
                "sentence-transformers is not installed.",
            ) from exc
        except Exception as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRetrievalUnavailableError(
                f"Unable to import sentence-transformers for {self.model_name}.",
            ) from exc

        try:
            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticRetrievalUnavailableError(
                f"Unable to load semantic retrieval model {self.model_name}.",
            ) from exc
        return self._model

    @staticmethod
    def _prefix_query(text: str) -> str:
        """Return the multilingual-e5 query-prefixed text."""

        return f"query: {text.strip()}"

    @staticmethod
    def _prefix_passage(text: str) -> str:
        """Return the multilingual-e5 passage-prefixed text."""

        return f"passage: {text.strip()}"
