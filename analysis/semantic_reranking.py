"""Optional semantic reranking helpers for QA answer ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class SemanticRerankingUnavailableError(RuntimeError):
    """Raised when the semantic reranking backend cannot be used."""


@dataclass(slots=True)
class SemanticRerankScore:
    """One semantic reranking score returned for a candidate answer."""

    candidate_index: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticRerankerBackend(Protocol):
    """Protocol implemented by semantic reranking backends."""

    backend_name: str
    model_name: str

    def score_pairs(
        self,
        *,
        query_text: str,
        passage_texts: Sequence[str],
        normalize_scores: bool = True,
    ) -> list[SemanticRerankScore]:
        """Return semantic relevance scores for query-passage pairs."""


class TransformersBGERerankerBackend:
    """Semantic reranker backed by a Hugging Face sequence classifier."""

    backend_name = "huggingface_transformers"

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        max_length: int = 512,
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self._tokenizer: Any | None = None
        self._model: Any | None = None

    def score_pairs(
        self,
        *,
        query_text: str,
        passage_texts: Sequence[str],
        normalize_scores: bool = True,
    ) -> list[SemanticRerankScore]:
        """Return semantic relevance scores for query-passage pairs."""

        if not passage_texts:
            return []

        tokenizer, model = self._load_components()
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRerankingUnavailableError(
                "Semantic reranking requires torch-compatible inference support.",
            ) from exc

        pairs = [[query_text, passage_text] for passage_text in passage_texts]
        try:
            inputs = tokenizer(
                pairs,
                padding=True,
                truncation=True,
                return_tensors="pt",
                max_length=self.max_length,
            )
            model.eval()
            with torch.no_grad():
                logits = model(**inputs, return_dict=True).logits.view(-1).float()
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticRerankingUnavailableError(
                f"Failed to score semantic reranking pairs with {self.model_name}.",
            ) from exc

        if normalize_scores:
            logits = torch.sigmoid(logits)

        return [
            SemanticRerankScore(
                candidate_index=index,
                score=float(logit_value),
                metadata={
                    "model_name": self.model_name,
                    "normalized": normalize_scores,
                    "max_length": self.max_length,
                },
            )
            for index, logit_value in enumerate(logits.tolist())
        ]

    def _load_components(self) -> tuple[Any, Any]:
        """Load and cache tokenizer/model lazily."""

        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model

        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRerankingUnavailableError(
                "transformers is not installed.",
            ) from exc
        except Exception as exc:  # pragma: no cover - runtime dependency guard
            raise SemanticRerankingUnavailableError(
                f"Unable to import transformers components for {self.model_name}.",
            ) from exc

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
            )
        except Exception as exc:  # pragma: no cover - runtime safety net
            raise SemanticRerankingUnavailableError(
                f"Unable to load semantic reranking model {self.model_name}.",
            ) from exc
        return self._tokenizer, self._model
