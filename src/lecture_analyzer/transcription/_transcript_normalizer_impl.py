"""Conservative transcript text normalization utilities.

The normalizer intentionally avoids paraphrasing, semantic cleanup, or speaker
inference. It only applies lightweight formatting fixes so later analysis
receives more consistent text while raw transcript content remains traceable.
"""

from __future__ import annotations

import re
from dataclasses import replace

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import MergedTranscript, MergedTranscriptUnit


class TranscriptNormalizer:
    """Apply light normalization to a merged transcript."""

    _WHITESPACE_RE = re.compile(r"\s+")
    _SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?])")
    _SPACE_AFTER_OPENING_BRACKET_RE = re.compile(r"([(\[{])\s+")
    _SPACE_BEFORE_CLOSING_BRACKET_RE = re.compile(r"\s+([)\]}])")
    _MISSING_SPACE_AFTER_PUNCTUATION_RE = re.compile(r"([,;:!?])(?=[^\W\d_])")

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def normalize(self, transcript: MergedTranscript) -> MergedTranscript:
        """Return a merged transcript with conservative text normalization."""

        normalized_units: list[MergedTranscriptUnit] = []
        dropped_chunk_ids: list[str] = []

        for unit in transcript.units:
            # Prefer the preserved raw text when available so normalization is
            # always applied from the least processed representation.
            raw_text = unit.raw_text if unit.raw_text is not None else unit.text
            normalized_text = self.normalize_text(raw_text)

            if not normalized_text and self.config.transcript_drop_empty_chunks:
                dropped_chunk_ids.append(unit.chunk_id)
                continue

            normalized_units.append(
                replace(
                    unit,
                    text=normalized_text,
                    raw_text=raw_text if self.config.transcript_keep_raw_text else None,
                ),
            )

        metadata = dict(transcript.metadata)
        metadata["normalization"] = {
            "drop_empty_chunks": self.config.transcript_drop_empty_chunks,
            "keep_raw_text": self.config.transcript_keep_raw_text,
            "normalize_whitespace": self.config.transcript_normalize_whitespace,
            "normalize_punctuation_spacing": (
                self.config.transcript_normalize_punctuation_spacing
            ),
            "dropped_chunk_ids": dropped_chunk_ids,
        }
        detected_languages = sorted(
            {
                unit.detected_language
                for unit in normalized_units
                if unit.detected_language
            },
        )

        return MergedTranscript(
            session_id=transcript.session_id,
            units=normalized_units,
            full_text=self._build_full_text(normalized_units),
            detected_languages=detected_languages,
            metadata=metadata,
        )

    def normalize_text(self, text: str | None) -> str:
        """Normalize transcript text without changing meaning.

        The current rules stay intentionally narrow:
        trim leading and trailing whitespace, collapse repeated whitespace when
        configured, and fix a few clearly safe punctuation-spacing patterns.
        """

        if text is None:
            return ""

        normalized = text.strip()
        if not normalized:
            return ""

        if self.config.transcript_normalize_whitespace:
            normalized = self._WHITESPACE_RE.sub(" ", normalized)

        if self.config.transcript_normalize_punctuation_spacing:
            # Apply only spacing fixes that are low-risk and language-agnostic.
            normalized = self._SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", normalized)
            normalized = self._SPACE_AFTER_OPENING_BRACKET_RE.sub(r"\1", normalized)
            normalized = self._SPACE_BEFORE_CLOSING_BRACKET_RE.sub(r"\1", normalized)
            normalized = self._MISSING_SPACE_AFTER_PUNCTUATION_RE.sub(r"\1 ", normalized)

        return normalized.strip()

    @staticmethod
    def _build_full_text(units: list[MergedTranscriptUnit]) -> str:
        """Join normalized unit text into a session-level transcript string."""

        return "\n".join(unit.text for unit in units if unit.text.strip())
