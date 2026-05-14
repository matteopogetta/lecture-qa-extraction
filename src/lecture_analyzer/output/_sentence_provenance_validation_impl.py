"""Structural validation helpers for sentence provenance diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class SentenceProvenanceValidation:
    """Derived provenance diagnostics for one serialized sentence layer."""

    utterance_to_sentence_ids: dict[str, list[str]] = field(default_factory=dict)
    sentence_to_utterance_keys: dict[str, list[str]] = field(default_factory=dict)
    sentence_to_overlap_sentence_ids: dict[str, list[str]] = field(default_factory=dict)
    utterance_without_sentence_count: int = 0
    utterances_assigned_to_multiple_sentences: int = 0
    shared_source_utterance_count: int = 0
    max_sentence_reuse_per_utterance: int = 0
    sentence_assignment_total: int = 0
    duplicate_source_utterance_id_count: int = 0
    sentences_with_duplicate_source_utterance_ids: int = 0
    sentences_with_empty_source_utterance_ids: int = 0
    sentences_with_provenance_overlap_count: int = 0
    all_sentences_have_provenance_overlap: bool = False
    unknown_source_utterance_reference_count: int = 0
    provenance_anomaly_count: int = 0
    mapping_conflict_examples: list[str] = field(default_factory=list)
    overlap_examples: list[str] = field(default_factory=list)
    duplicate_source_examples: list[str] = field(default_factory=list)
    empty_source_examples: list[str] = field(default_factory=list)
    unknown_reference_examples: list[str] = field(default_factory=list)


def scoped_utterance_key(audio_source_id: str | None, utterance_id: str | None) -> str:
    """Return a source-scoped utterance key for structural comparisons."""

    normalized_audio_source_id = str(audio_source_id or "").strip()
    normalized_utterance_id = str(utterance_id or "").strip()
    if normalized_audio_source_id and normalized_utterance_id:
        return f"{normalized_audio_source_id}::{normalized_utterance_id}"
    return normalized_utterance_id


def validate_sentence_provenance(
    *,
    utterances: Sequence[dict[str, Any]],
    sentences: Sequence[dict[str, Any]],
    max_examples: int = 5,
) -> SentenceProvenanceValidation:
    """Return structural provenance diagnostics for serialized sentences."""

    known_utterance_keys = {
        scoped_utterance_key(
            utterance.get("audio_source_id"),
            utterance.get("utterance_id"),
        )
        for utterance in utterances
        if str(utterance.get("utterance_id") or "").strip()
    }
    utterance_to_sentence_ids: dict[str, list[str]] = {
        utterance_key: []
        for utterance_key in known_utterance_keys
    }
    sentence_to_utterance_keys: dict[str, list[str]] = {}
    duplicate_source_examples: list[str] = []
    empty_source_examples: list[str] = []
    unknown_reference_examples: list[str] = []
    duplicate_source_utterance_id_count = 0
    sentences_with_duplicate_source_utterance_ids = 0
    sentences_with_empty_source_utterance_ids = 0
    unknown_source_utterance_reference_count = 0

    for sentence in sentences:
        sentence_id = str(sentence.get("sentence_id") or "").strip()
        if not sentence_id:
            continue
        audio_source_id = str(sentence.get("audio_source_id") or "").strip()
        raw_source_utterance_ids = sentence.get("source_utterance_ids", [])
        if not isinstance(raw_source_utterance_ids, list):
            raw_source_utterance_ids = []

        scoped_source_keys: list[str] = []
        seen_source_keys: set[str] = set()
        sentence_duplicate_count = 0
        for raw_utterance_id in raw_source_utterance_ids:
            normalized_utterance_id = str(raw_utterance_id or "").strip()
            if not normalized_utterance_id:
                continue
            source_key = scoped_utterance_key(audio_source_id, normalized_utterance_id)
            if source_key in seen_source_keys:
                sentence_duplicate_count += 1
                continue
            seen_source_keys.add(source_key)
            scoped_source_keys.append(source_key)
            utterance_to_sentence_ids.setdefault(source_key, [])
            if sentence_id not in utterance_to_sentence_ids[source_key]:
                utterance_to_sentence_ids[source_key].append(sentence_id)
            if (
                source_key not in known_utterance_keys
                and len(unknown_reference_examples) < max_examples
            ):
                unknown_reference_examples.append(f"{sentence_id} -> {source_key}")
            if source_key not in known_utterance_keys:
                unknown_source_utterance_reference_count += 1

        sentence_to_utterance_keys[sentence_id] = scoped_source_keys
        if sentence_duplicate_count > 0:
            duplicate_source_utterance_id_count += sentence_duplicate_count
            sentences_with_duplicate_source_utterance_ids += 1
            if len(duplicate_source_examples) < max_examples:
                duplicate_source_examples.append(
                    f"{sentence_id} duplicates={sentence_duplicate_count}",
                )

        if not scoped_source_keys and str(sentence.get("text") or "").strip():
            sentences_with_empty_source_utterance_ids += 1
            if len(empty_source_examples) < max_examples:
                empty_source_examples.append(sentence_id)

    overlap_lookup: dict[str, set[str]] = {}
    mapping_conflict_examples: list[str] = []
    for source_key, sentence_ids in utterance_to_sentence_ids.items():
        if len(sentence_ids) <= 1:
            continue
        if len(mapping_conflict_examples) < max_examples:
            mapping_conflict_examples.append(
                f"{source_key} -> {', '.join(sentence_ids)}",
            )
        for sentence_id in sentence_ids:
            overlap_lookup.setdefault(sentence_id, set()).update(
                overlapping_sentence_id
                for overlapping_sentence_id in sentence_ids
                if overlapping_sentence_id != sentence_id
            )

    sentence_to_overlap_sentence_ids = {
        sentence_id: sorted(overlapping_sentence_ids)
        for sentence_id, overlapping_sentence_ids in overlap_lookup.items()
        if overlapping_sentence_ids
    }
    overlap_examples = [
        f"{sentence_id} -> {', '.join(overlapping_sentence_ids)}"
        for sentence_id, overlapping_sentence_ids in list(
            sentence_to_overlap_sentence_ids.items(),
        )[:max_examples]
    ]

    utterance_without_sentence_count = sum(
        1
        for source_key in known_utterance_keys
        if not utterance_to_sentence_ids.get(source_key)
    )
    utterances_assigned_to_multiple_sentences = sum(
        1
        for source_key in known_utterance_keys
        if len(utterance_to_sentence_ids.get(source_key, [])) > 1
    )
    shared_source_utterance_count = sum(
        1
        for sentence_ids in utterance_to_sentence_ids.values()
        if len(sentence_ids) > 1
    )
    sentence_assignment_total = sum(
        len(source_keys)
        for source_keys in sentence_to_utterance_keys.values()
    )
    max_sentence_reuse_per_utterance = max(
        (len(sentence_ids) for sentence_ids in utterance_to_sentence_ids.values()),
        default=0,
    )
    sentences_with_provenance_overlap_count = len(sentence_to_overlap_sentence_ids)
    all_sentences_have_provenance_overlap = bool(sentences) and (
        sentences_with_provenance_overlap_count == len(sentences)
    )
    provenance_anomaly_count = (
        utterances_assigned_to_multiple_sentences
        + duplicate_source_utterance_id_count
        + sentences_with_empty_source_utterance_ids
        + unknown_source_utterance_reference_count
    )

    return SentenceProvenanceValidation(
        utterance_to_sentence_ids=utterance_to_sentence_ids,
        sentence_to_utterance_keys=sentence_to_utterance_keys,
        sentence_to_overlap_sentence_ids=sentence_to_overlap_sentence_ids,
        utterance_without_sentence_count=utterance_without_sentence_count,
        utterances_assigned_to_multiple_sentences=(
            utterances_assigned_to_multiple_sentences
        ),
        shared_source_utterance_count=shared_source_utterance_count,
        max_sentence_reuse_per_utterance=max_sentence_reuse_per_utterance,
        sentence_assignment_total=sentence_assignment_total,
        duplicate_source_utterance_id_count=duplicate_source_utterance_id_count,
        sentences_with_duplicate_source_utterance_ids=(
            sentences_with_duplicate_source_utterance_ids
        ),
        sentences_with_empty_source_utterance_ids=(
            sentences_with_empty_source_utterance_ids
        ),
        sentences_with_provenance_overlap_count=(
            sentences_with_provenance_overlap_count
        ),
        all_sentences_have_provenance_overlap=all_sentences_have_provenance_overlap,
        unknown_source_utterance_reference_count=(
            unknown_source_utterance_reference_count
        ),
        provenance_anomaly_count=provenance_anomaly_count,
        mapping_conflict_examples=mapping_conflict_examples,
        overlap_examples=overlap_examples,
        duplicate_source_examples=duplicate_source_examples,
        empty_source_examples=empty_source_examples,
        unknown_reference_examples=unknown_reference_examples,
    )
