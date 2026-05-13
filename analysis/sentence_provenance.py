"""Structural validation helpers for final sentence provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from core.models import Sentence, Utterance


@dataclass(slots=True)
class SentenceStructureValidation:
    """Derived diagnostics for utterance-to-sentence structural invariants."""

    utterance_to_sentence_ids: dict[str, list[str]] = field(default_factory=dict)
    sentence_to_source_utterance_ids: dict[str, list[str]] = field(default_factory=dict)
    sentence_to_overlap_sentence_ids: dict[str, list[str]] = field(default_factory=dict)
    utterance_without_sentence_count: int = 0
    utterances_assigned_to_multiple_sentences: int = 0
    sentences_with_duplicate_source_utterance_ids: int = 0
    sentences_with_empty_source_utterance_ids: int = 0
    sentences_with_provenance_overlap_count: int = 0
    max_sentence_reuse_per_utterance: int = 0
    sentence_assignment_total: int = 0
    all_sentences_have_provenance_overlap: bool = False
    provenance_anomaly_count: int = 0
    mapping_conflict_examples: list[str] = field(default_factory=list)
    overlap_examples: list[str] = field(default_factory=list)
    duplicate_source_examples: list[str] = field(default_factory=list)
    empty_source_examples: list[str] = field(default_factory=list)


def validate_sentence_structure(
    *,
    utterances: Sequence[Utterance],
    sentences: Sequence[Sentence],
    max_examples: int = 5,
) -> SentenceStructureValidation:
    """Return structural diagnostics for final sentence assignments."""

    known_utterance_ids = [
        utterance.utterance_id
        for utterance in utterances
        if utterance.utterance_id.strip()
    ]
    utterance_to_sentence_ids: dict[str, list[str]] = {
        utterance_id: []
        for utterance_id in known_utterance_ids
    }
    sentence_to_source_utterance_ids: dict[str, list[str]] = {}
    duplicate_source_examples: list[str] = []
    empty_source_examples: list[str] = []

    sentences_with_duplicate_source_utterance_ids = 0
    sentences_with_empty_source_utterance_ids = 0

    for sentence in sentences:
        if not sentence.sentence_id.strip():
            continue
        source_utterance_ids: list[str] = []
        seen_utterance_ids: set[str] = set()
        duplicate_count = 0

        for utterance_id in sentence.source_utterance_ids:
            normalized_utterance_id = str(utterance_id or "").strip()
            if not normalized_utterance_id:
                continue
            if normalized_utterance_id in seen_utterance_ids:
                duplicate_count += 1
                continue
            seen_utterance_ids.add(normalized_utterance_id)
            source_utterance_ids.append(normalized_utterance_id)
            utterance_to_sentence_ids.setdefault(normalized_utterance_id, [])
            if sentence.sentence_id not in utterance_to_sentence_ids[normalized_utterance_id]:
                utterance_to_sentence_ids[normalized_utterance_id].append(
                    sentence.sentence_id,
                )

        sentence_to_source_utterance_ids[sentence.sentence_id] = source_utterance_ids
        if duplicate_count > 0:
            sentences_with_duplicate_source_utterance_ids += 1
            if len(duplicate_source_examples) < max_examples:
                duplicate_source_examples.append(
                    f"{sentence.sentence_id} duplicates={duplicate_count}",
                )
        if not source_utterance_ids and sentence.text.strip():
            sentences_with_empty_source_utterance_ids += 1
            if len(empty_source_examples) < max_examples:
                empty_source_examples.append(sentence.sentence_id)

    overlap_lookup: dict[str, set[str]] = {}
    mapping_conflict_examples: list[str] = []
    for utterance_id, sentence_ids in utterance_to_sentence_ids.items():
        if len(sentence_ids) <= 1:
            continue
        if len(mapping_conflict_examples) < max_examples:
            mapping_conflict_examples.append(
                f"{utterance_id} -> {', '.join(sentence_ids)}",
            )
        for sentence_id in sentence_ids:
            overlap_lookup.setdefault(sentence_id, set()).update(
                other_sentence_id
                for other_sentence_id in sentence_ids
                if other_sentence_id != sentence_id
            )

    sentence_to_overlap_sentence_ids = {
        sentence_id: sorted(overlap_sentence_ids)
        for sentence_id, overlap_sentence_ids in overlap_lookup.items()
        if overlap_sentence_ids
    }
    overlap_examples = [
        f"{sentence_id} -> {', '.join(overlap_sentence_ids)}"
        for sentence_id, overlap_sentence_ids in list(
            sentence_to_overlap_sentence_ids.items(),
        )[:max_examples]
    ]

    utterance_without_sentence_count = sum(
        1
        for utterance_id in known_utterance_ids
        if not utterance_to_sentence_ids.get(utterance_id)
    )
    utterances_assigned_to_multiple_sentences = sum(
        1
        for utterance_id in known_utterance_ids
        if len(utterance_to_sentence_ids.get(utterance_id, [])) > 1
    )
    max_sentence_reuse_per_utterance = max(
        (len(sentence_ids) for sentence_ids in utterance_to_sentence_ids.values()),
        default=0,
    )
    sentences_with_provenance_overlap_count = len(sentence_to_overlap_sentence_ids)
    sentence_assignment_total = sum(
        len(source_utterance_ids)
        for source_utterance_ids in sentence_to_source_utterance_ids.values()
    )
    all_sentences_have_provenance_overlap = bool(sentences) and (
        sentences_with_provenance_overlap_count == len(sentences)
    )
    provenance_anomaly_count = (
        utterances_assigned_to_multiple_sentences
        + sentences_with_duplicate_source_utterance_ids
        + sentences_with_empty_source_utterance_ids
        + utterance_without_sentence_count
    )

    return SentenceStructureValidation(
        utterance_to_sentence_ids=utterance_to_sentence_ids,
        sentence_to_source_utterance_ids=sentence_to_source_utterance_ids,
        sentence_to_overlap_sentence_ids=sentence_to_overlap_sentence_ids,
        utterance_without_sentence_count=utterance_without_sentence_count,
        utterances_assigned_to_multiple_sentences=(
            utterances_assigned_to_multiple_sentences
        ),
        sentences_with_duplicate_source_utterance_ids=(
            sentences_with_duplicate_source_utterance_ids
        ),
        sentences_with_empty_source_utterance_ids=(
            sentences_with_empty_source_utterance_ids
        ),
        sentences_with_provenance_overlap_count=(
            sentences_with_provenance_overlap_count
        ),
        max_sentence_reuse_per_utterance=max_sentence_reuse_per_utterance,
        sentence_assignment_total=sentence_assignment_total,
        all_sentences_have_provenance_overlap=all_sentences_have_provenance_overlap,
        provenance_anomaly_count=provenance_anomaly_count,
        mapping_conflict_examples=mapping_conflict_examples,
        overlap_examples=overlap_examples,
        duplicate_source_examples=duplicate_source_examples,
        empty_source_examples=empty_source_examples,
    )
