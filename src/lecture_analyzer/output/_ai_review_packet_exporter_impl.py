"""Markdown review packets for external QA/C quality assessment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lecture_analyzer.core.models import LectureSession


def export_ai_review_packet(
    session: LectureSession,
    output_path: str | Path,
    *,
    source_json_path: str | Path | None = None,
) -> Path:
    """Write a self-contained Markdown packet for human or chatbot review."""

    target_path = Path(output_path).expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        build_ai_review_packet(
            session=session,
            source_json_path=source_json_path,
        ),
        encoding="utf-8",
    )
    return target_path


def build_ai_review_packet(
    session: LectureSession,
    *,
    source_json_path: str | Path | None = None,
) -> str:
    """Return a Markdown review packet with instructions, transcript, and QA/C."""

    payload = session.to_dict()
    session_metadata = _as_dict(payload.get("session_metadata"))
    metadata = _as_dict(session_metadata.get("metadata"))
    transcript = _as_dict(payload.get("transcript"))
    timing = _as_dict(payload.get("pipeline_timing"))
    timing_summary = _as_dict(timing.get("summary"))
    timing_stages = [
        stage
        for stage in timing.get("stages") or []
        if isinstance(stage, dict)
    ]
    qa_candidates = list(payload.get("qa_candidates") or [])
    qa_coverage = _as_dict(metadata.get("qa_coverage"))
    total_duration_seconds = _safe_float(
        timing_summary.get("total_duration_seconds"),
    )

    lines: list[str] = [
        "# QA/C Review Packet",
        "",
        "## Reviewer Instructions",
        "",
        (
            "You are reviewing lecture-derived question/answer/context "
            "candidates. Evaluate semantic quality as a careful human reviewer, "
            "using only the transcript and candidate evidence in this packet."
        ),
        "",
        "Score every candidate from 1 to 5 for:",
        "",
        "- question_quality: clear, useful, and didactically relevant",
        "- answer_quality: correct, complete, and grounded in the transcript",
        "- context_quality: enough surrounding context to understand the pair",
        "- grounding_quality: timing/provenance supports the candidate",
        "- keep_decision: keep, revise, or reject",
        "",
        "Also provide a short reason for each score and a final summary with:",
        "",
        "- strongest candidates",
        "- weakest candidates",
        "- recurring failure modes",
        "- whether the pipeline profile seems worth its runtime cost",
        "- whether runtime is a cold-run cost or partly due to cache/artifact reuse",
        "",
        "Return structured JSON using this shape:",
        "",
        _fenced(
            """{
  "overall": {
    "quality_score": 1,
    "runtime_value_score": 1,
    "summary": ""
  },
  "candidates": [
    {
      "qa_candidate_id": "",
      "question_quality": 1,
      "answer_quality": 1,
      "context_quality": 1,
      "grounding_quality": 1,
      "keep_decision": "keep|revise|reject",
      "reason": ""
    }
  ],
  "failure_modes": [],
  "recommendations": []
}""",
            "json",
        ),
        "",
        "## Run Metadata",
        "",
        f"- session_id: `{session_metadata.get('session_id', '')}`",
        f"- pipeline_profile: `{metadata.get('pipeline_profile', '')}`",
        f"- pipeline_execution_mode: `{metadata.get('pipeline_execution_mode', '')}`",
        f"- segmentation_mode: `{metadata.get('segmentation_mode', '')}`",
        f"- qa_candidate_count: `{len(qa_candidates)}`",
        f"- source_json_path: `{source_json_path or ''}`",
        "",
        "## Timing Summary",
        "",
        f"- total_duration_seconds: `{timing_summary.get('total_duration_seconds', '')}`",
        f"- stage_count: `{timing_summary.get('stage_count', '')}`",
        f"- most_expensive_stage_name: `{timing_summary.get('most_expensive_stage_name', '')}`",
        f"- most_expensive_stage_duration_seconds: `{timing_summary.get('most_expensive_stage_duration_seconds', '')}`",
        f"- executed_stage_count: `{timing_summary.get('executed_stage_count', '')}`",
        f"- skipped_stage_count: `{timing_summary.get('skipped_stage_count', '')}`",
        f"- disabled_stage_count: `{timing_summary.get('disabled_stage_count', '')}`",
        f"- reused_cache_stage_count: `{timing_summary.get('reused_cache_stage_count', '')}`",
        f"- reused_artifact_stage_count: `{timing_summary.get('reused_artifact_stage_count', '')}`",
        f"- forced_recompute_stage_count: `{timing_summary.get('forced_recompute_stage_count', '')}`",
        f"- any_cache_hit: `{timing_summary.get('any_cache_hit', '')}`",
        f"- any_artifact_reuse: `{timing_summary.get('any_artifact_reuse', '')}`",
        f"- full_recompute_requested: `{timing_summary.get('full_recompute_requested', '')}`",
        "",
        "When comparing profiles, treat cache or artifact reuse explicitly. A "
        "stage with near-zero duration may mean previous artifacts were reused, "
        "not that the underlying cold-run stage is free.",
        "",
        "## Coverage Summary",
        "",
        _format_coverage_summary(
            qa_coverage,
            emitted_candidate_count=len(qa_candidates),
        ),
        "",
        "## Timing Stage Details",
        "",
        _format_timing_stage_table(
            timing_stages,
            total_duration_seconds=total_duration_seconds,
        ),
        "",
        "## Runtime Review Signals",
        "",
        _format_runtime_review_signals(
            timing_summary=timing_summary,
            timing_stages=timing_stages,
        ),
        "",
        "## Transcript",
        "",
        _fenced(str(transcript.get("full_text") or "")),
        "",
        "## QA/C Candidates",
        "",
    ]

    if not qa_candidates:
        lines.extend(["No QA/C candidates were exported.", ""])
    else:
        for index, candidate in enumerate(qa_candidates, start=1):
            candidate_payload = _as_dict(candidate)
            lines.extend(_format_candidate(index, candidate_payload))

    return "\n".join(lines).rstrip() + "\n"


def _format_candidate(index: int, candidate: dict[str, Any]) -> list[str]:
    """Return Markdown lines for one QA/C candidate."""

    metadata = _as_dict(candidate.get("metadata"))
    pairing_debug = _as_dict(metadata.get("pairing_debug"))
    answer_debug = _as_dict(metadata.get("answer_debug"))
    speaker_check = _as_dict(metadata.get("speaker_check"))

    return [
        f"### Candidate {index}: `{candidate.get('qa_candidate_id', '')}`",
        "",
        f"- confidence: `{candidate.get('confidence', '')}`",
        f"- confidence_label: `{candidate.get('confidence_label', '')}`",
        f"- question_type: `{candidate.get('question_type', '')}`",
        f"- question_timing: `{_format_timing(candidate.get('question_timing'))}`",
        f"- answer_timing: `{_format_timing(candidate.get('answer_timing'))}`",
        f"- question_sentence_ids: `{', '.join(candidate.get('question_sentence_ids') or [])}`",
        f"- answer_sentence_ids: `{', '.join(candidate.get('answer_sentence_ids') or [])}`",
        f"- context_sentence_ids: `{', '.join(candidate.get('context_sentence_ids') or [])}`",
        f"- context_strategy: `{candidate.get('context_strategy', '')}`",
        f"- context_confidence: `{candidate.get('context_confidence', '')}`",
        f"- reason_codes: `{', '.join(candidate.get('reason_codes') or [])}`",
        f"- review_flags: `{', '.join(candidate.get('review_flags') or [])}`",
        f"- speaker_similarity_score: `{speaker_check.get('speaker_similarity_score', '')}`",
        f"- speaker_check_flags: `{', '.join(speaker_check.get('flags') or [])}`",
        f"- speaker_check_note: `{speaker_check.get('note', '')}`",
        f"- search_strategy: `{pairing_debug.get('effective_search_strategy') or pairing_debug.get('search_strategy') or ''}`",
        f"- ranking_strategy: `{pairing_debug.get('effective_ranking_strategy') or pairing_debug.get('ranking_strategy') or ''}`",
        f"- answer_candidate_channel: `{_as_dict(answer_debug.get('search_signals')).get('candidate_channel', '')}`",
        "",
        "**Question**",
        "",
        _fenced(str(candidate.get("question_text") or "")),
        "",
        "**Answer**",
        "",
        _fenced(str(candidate.get("answer_text") or "")),
        "",
        "**Context**",
        "",
        _fenced(str(candidate.get("context_text") or "")),
        "",
    ]


def _format_timing_stage_table(
    stages: list[dict[str, Any]],
    *,
    total_duration_seconds: float | None,
) -> str:
    """Return a Markdown table with per-stage runtime and reuse details."""

    if not stages:
        return "No detailed timing stages were exported."

    lines = [
        "| stage | status | seconds | share | cache | artifact | forced | note |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for stage in stages:
        if stage.get("stage_name") == "total_pipeline_execution":
            continue
        duration = _safe_float(stage.get("duration_seconds"))
        share = _safe_ratio(duration, total_duration_seconds)
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_table(str(stage.get("stage_name") or "")),
                    _escape_table(str(stage.get("status") or "")),
                    _format_float(duration),
                    _format_percent(share),
                    _format_bool(stage.get("used_cache")),
                    _format_bool(stage.get("used_existing_artifact")),
                    _format_bool(stage.get("forced_recompute")),
                    _escape_table(_format_stage_note(stage)),
                ],
            )
            + " |",
        )
    return "\n".join(lines)


def _format_runtime_review_signals(
    *,
    timing_summary: dict[str, Any],
    timing_stages: list[dict[str, Any]],
) -> str:
    """Return compact reviewer guidance derived from timing and reuse fields."""

    reused_cache_count = int(_safe_float(timing_summary.get("reused_cache_stage_count"), 0.0) or 0)
    reused_artifact_count = int(
        _safe_float(timing_summary.get("reused_artifact_stage_count"), 0.0) or 0,
    )
    forced_count = int(
        _safe_float(timing_summary.get("forced_recompute_stage_count"), 0.0) or 0,
    )
    zeroish_reused_stages = [
        str(stage.get("stage_name") or "")
        for stage in timing_stages
        if (
            (_safe_float(stage.get("duration_seconds"), 0.0) or 0.0) <= 0.001
            and (stage.get("used_cache") or stage.get("used_existing_artifact"))
        )
    ]

    lines = [
        f"- cache_reused_stage_count: `{reused_cache_count}`",
        f"- artifact_reused_stage_count: `{reused_artifact_count}`",
        f"- forced_recompute_stage_count: `{forced_count}`",
        f"- zero_or_near_zero_reused_stages: `{', '.join(zeroish_reused_stages)}`",
    ]
    if reused_cache_count or reused_artifact_count:
        lines.append(
            "- interpretation: this is at least partly a warm/reuse run; compare "
            "quality and speed against other profiles only with this noted.",
        )
    else:
        lines.append(
            "- interpretation: no cache/artifact reuse was reported in timing summary.",
        )
    return "\n".join(lines)


def _format_coverage_summary(
    qa_coverage: dict[str, Any],
    *,
    emitted_candidate_count: int,
) -> str:
    """Return compact QA coverage counters for external review."""

    interrogative_count = int(
        _safe_float(qa_coverage.get("interrogative_sentence_count"), 0.0) or 0,
    )
    emitted_count = int(
        _safe_float(
            qa_coverage.get("emitted_candidate_count"),
            float(emitted_candidate_count),
        )
        or 0,
    )
    suppressed_reasons = _as_dict(qa_coverage.get("suppressed_by_gate_reasons"))
    rescued_reasons = _as_dict(qa_coverage.get("rescued_by_gate_reasons"))
    rescue_rejected_reasons = _as_dict(
        qa_coverage.get("speaker_rescue_rejected_reasons"),
    )
    speaker_check_flag_counts = _as_dict(
        qa_coverage.get("speaker_check_flag_counts"),
    )
    rescued_count = int(
        _safe_float(qa_coverage.get("rescued_candidate_count"), 0.0) or 0,
    )
    suppressed_count = int(
        _safe_float(
            qa_coverage.get("suppressed_by_gate_count"),
            float(
                sum(
                    int(_safe_float(count, 0.0) or 0)
                    for count in suppressed_reasons.values()
                ),
            ),
        )
        or 0,
    )
    coverage_ratio = (
        round(emitted_count / interrogative_count, 4)
        if interrogative_count
        else 0
    )
    reason_text = ", ".join(
        f"{reason}={int(_safe_float(count, 0.0) or 0)}"
        for reason, count in sorted(suppressed_reasons.items())
    )
    if not reason_text:
        reason_text = "none"
    rescued_reason_text = ", ".join(
        f"{reason}={int(_safe_float(count, 0.0) or 0)}"
        for reason, count in sorted(rescued_reasons.items())
    )
    if not rescued_reason_text:
        rescued_reason_text = "none"
    rescue_rejected_reason_text = ", ".join(
        f"{reason}={int(_safe_float(count, 0.0) or 0)}"
        for reason, count in sorted(rescue_rejected_reasons.items())
    )
    if not rescue_rejected_reason_text:
        rescue_rejected_reason_text = "none"
    speaker_flag_text = ", ".join(
        f"{flag}={int(_safe_float(count, 0.0) or 0)}"
        for flag, count in sorted(speaker_check_flag_counts.items())
    )
    if not speaker_flag_text:
        speaker_flag_text = "none"

    return "\n".join(
        [
            f"- interrogative_sentence_count: `{interrogative_count}`",
            f"- emitted_candidate_count: `{emitted_count}`",
            f"- coverage_ratio: `{coverage_ratio}`",
            f"- suppressed_by_gate_count: `{suppressed_count}`",
            f"- suppressed_by_gate_reasons: `{reason_text}`",
            f"- rescued_candidate_count: `{rescued_count}`",
            f"- rescued_by_gate_reasons: `{rescued_reason_text}`",
            f"- speaker_rescue_attempted_candidate_count: `{qa_coverage.get('speaker_rescue_attempted_candidate_count', 0)}`",
            f"- speaker_rescue_checked_candidate_count: `{qa_coverage.get('speaker_rescue_checked_candidate_count', 0)}`",
            f"- speaker_rescue_rejected_candidate_count: `{qa_coverage.get('speaker_rescue_rejected_candidate_count', 0)}`",
            f"- speaker_rescue_rejected_reasons: `{rescue_rejected_reason_text}`",
            f"- speaker_rescue_total_check_seconds: `{qa_coverage.get('speaker_rescue_total_check_seconds', 0)}`",
            f"- speaker_check_flag_counts: `{speaker_flag_text}`",
            f"- speaker_check_checked_candidate_count: `{qa_coverage.get('speaker_check_checked_candidate_count', 0)}`",
            f"- speaker_check_unavailable_candidate_count: `{qa_coverage.get('speaker_check_unavailable_candidate_count', 0)}`",
            f"- speaker_check_skipped_candidate_count: `{qa_coverage.get('speaker_check_skipped_candidate_count', 0)}`",
            f"- speaker_check_precomputed_candidate_count: `{qa_coverage.get('speaker_check_precomputed_candidate_count', 0)}`",
        ],
    )


def _format_timing(value: object) -> str:
    """Return a compact timing label for a serialized TimeRange."""

    timing = _as_dict(value)
    if not timing:
        return ""
    start = timing.get("start_seconds")
    end = timing.get("end_seconds")
    if start is None or end is None:
        return ""
    return f"{start}-{end}s"


def _format_stage_note(stage: dict[str, Any]) -> str:
    """Return a compact note string for one timing stage."""

    notes: list[str] = []
    note = str(stage.get("note") or "").strip()
    if note:
        notes.append(note)
    metadata = _as_dict(stage.get("metadata"))
    if metadata:
        notes.append(
            ", ".join(
                f"{key}={metadata[key]}"
                for key in sorted(metadata)
            ),
        )
    return " | ".join(notes)


def _safe_float(value: object, fallback: float | None = None) -> float | None:
    """Return a float when possible."""

    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_ratio(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    """Return a ratio or None when it cannot be computed."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _format_float(value: float | None) -> str:
    """Return a compact float label."""

    if value is None:
        return ""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _format_percent(value: float | None) -> str:
    """Return a compact percent label."""

    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def _format_bool(value: object) -> str:
    """Return a compact bool label."""

    return "yes" if bool(value) else "no"


def _escape_table(value: str) -> str:
    """Escape Markdown table separators."""

    return value.replace("|", "\\|").replace("\n", " ")


def _as_dict(value: object) -> dict[str, Any]:
    """Return a dict when possible, otherwise an empty dict."""

    return value if isinstance(value, dict) else {}


def _fenced(value: str, language: str = "") -> str:
    """Return text in a Markdown fence, avoiding accidental fence closure."""

    safe_value = value.replace("```", "` ` `")
    return f"```{language}\n{safe_value}\n```"
