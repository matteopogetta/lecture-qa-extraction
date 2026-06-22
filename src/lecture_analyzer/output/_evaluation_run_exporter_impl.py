"""Persistent local evaluation-run export for QA/C quality review."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from lecture_analyzer.core.models import LectureSession
from lecture_analyzer.output.ai_review_packet_exporter import build_ai_review_packet


_SAFE_NAME_RE = re.compile(r"[^a-z0-9._-]+")


def export_evaluation_run(
    session: LectureSession,
    *,
    source_json_path: str | Path,
    evaluation_root: str | Path,
    input_label: str | None = None,
    run_label: str | None = None,
    pipeline_config: object | None = None,
    code_root: str | Path | None = None,
) -> Path:
    """Write a persistent local evaluation run directory."""

    source_path = Path(source_json_path).expanduser().resolve()
    root_path = Path(evaluation_root).expanduser().resolve()
    resolved_input_label = _sanitize_name(
        input_label or _derive_input_label(session),
        fallback="session",
    )
    resolved_run_label = _resolve_unique_run_label(
        root_path / resolved_input_label / "runs",
        run_label or _derive_run_label(session),
    )
    run_directory = root_path / resolved_input_label / "runs" / resolved_run_label
    run_directory.mkdir(parents=True, exist_ok=False)

    session_target_path = run_directory / "session.json"
    if source_path.is_file():
        shutil.copy2(source_path, session_target_path)
    else:
        session_target_path.write_text(
            json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    review_packet_path = run_directory / "review_packet.md"
    review_packet_path.write_text(
        build_ai_review_packet(
            session,
            source_json_path=session_target_path,
        ),
        encoding="utf-8",
    )

    metrics = build_evaluation_metrics(
        session=session,
        evaluation_root=root_path,
        input_label=resolved_input_label,
        run_label=resolved_run_label,
        run_directory=run_directory,
        session_json_path=session_target_path,
        review_packet_path=review_packet_path,
        pipeline_config=pipeline_config,
        code_root=code_root,
    )
    metrics_path = run_directory / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    ai_review_path = run_directory / "ai_review.json"
    ai_review_path.write_text(
        json.dumps(
            build_ai_review_template(metrics),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return run_directory


def build_evaluation_metrics(
    *,
    session: LectureSession,
    evaluation_root: Path,
    input_label: str,
    run_label: str,
    run_directory: Path,
    session_json_path: Path,
    review_packet_path: Path,
    pipeline_config: object | None = None,
    code_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return objective metrics and identity fields for one evaluation run."""

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
    qa_candidates = [
        candidate
        for candidate in payload.get("qa_candidates") or []
        if isinstance(candidate, dict)
    ]

    run_id = f"{input_label}/{run_label}"
    total_duration_seconds = _safe_float(timing_summary.get("total_duration_seconds"))
    qa_candidate_count = len(qa_candidates)

    return {
        "schema_version": "1.0",
        "run_identity": {
            "run_id": run_id,
            "input_label": input_label,
            "run_label": run_label,
            "session_id": session_metadata.get("session_id"),
            "created_at": _now_iso(),
            "pipeline_profile": metadata.get("pipeline_profile"),
            "pipeline_execution_mode": metadata.get("pipeline_execution_mode"),
            "segmentation_mode": metadata.get("segmentation_mode"),
        },
        "privacy": {
            "storage_scope": "local_filesystem",
            "external_upload_performed_by_pipeline": False,
            "ai_review_file": "ai_review.json",
            "notes": (
                "Paste a manual external AI or human review response into "
                "ai_review.json only when privacy rules allow it."
            ),
        },
        "code_snapshot": _build_code_snapshot(code_root),
        "pipeline_configuration": _serialize_config(pipeline_config),
        "paths": {
            "evaluation_root": str(evaluation_root),
            "run_directory": str(run_directory),
            "session_json": str(session_json_path),
            "review_packet": str(review_packet_path),
            "ai_review": str(run_directory / "ai_review.json"),
            "metrics": str(run_directory / "metrics.json"),
        },
        "input_sources": _input_source_metrics(payload.get("input_sources") or []),
        "objective_metrics": {
            "qa_candidate_count": qa_candidate_count,
            "qa_candidates_with_answer_count": sum(
                1 for candidate in qa_candidates if candidate.get("answer_text")
            ),
            "qa_candidates_with_context_count": sum(
                1 for candidate in qa_candidates if candidate.get("context_text")
            ),
            "qa_candidates_with_review_flags_count": sum(
                1 for candidate in qa_candidates if candidate.get("review_flags")
            ),
            "transcript_word_count": _word_count(
                str(transcript.get("full_text") or ""),
            ),
            "segment_count": len(payload.get("segments") or []),
            "sentence_count": len(payload.get("sentences") or []),
        },
        "timing_summary": timing_summary,
        "runtime_metrics": {
            "total_duration_seconds": total_duration_seconds,
            "qa_candidates_per_runtime_second": _safe_ratio(
                qa_candidate_count,
                total_duration_seconds,
            ),
            "any_cache_hit": timing_summary.get("any_cache_hit"),
            "any_artifact_reuse": timing_summary.get("any_artifact_reuse"),
            "reused_cache_stage_count": timing_summary.get(
                "reused_cache_stage_count",
            ),
            "reused_artifact_stage_count": timing_summary.get(
                "reused_artifact_stage_count",
            ),
            "forced_recompute_stage_count": timing_summary.get(
                "forced_recompute_stage_count",
            ),
            "zero_or_near_zero_reused_stages": _zeroish_reused_stages(timing_stages),
        },
        "timing_stages": timing_stages,
        "ai_review_status": {
            "status": "pending_manual_review",
            "expected_file": "ai_review.json",
        },
    }


def build_ai_review_template(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return the placeholder file where external review output is saved."""

    run_identity = _as_dict(metrics.get("run_identity"))
    return {
        "schema_version": "1.0",
        "review_status": "pending_manual_review",
        "run_id": run_identity.get("run_id"),
        "reviewer": {
            "type": "human_or_external_chatbot",
            "name": "",
            "reviewed_at": "",
        },
        "instructions": (
            "Paste the structured JSON returned by the human or chatbot review "
            "here, preserving run_id so comparisons can link it to metrics.json."
        ),
        "overall": {
            "quality_score": None,
            "runtime_value_score": None,
            "summary": "",
        },
        "candidates": [],
        "failure_modes": [],
        "recommendations": [],
    }


def _derive_input_label(session: LectureSession) -> str:
    """Return a stable input label from the session source."""

    if len(session.input_sources) == 1:
        return session.input_sources[0].original_path.stem
    return session.session_id


def _derive_run_label(session: LectureSession) -> str:
    """Return a timestamped profile label for one evaluation run."""

    profile = str(session.metadata.get("pipeline_profile") or "current")
    segmentation_mode = str(session.metadata.get("segmentation_mode") or "structural")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return (
        f"{timestamp}_"
        f"{_sanitize_name(profile, fallback='profile')}_"
        f"{_sanitize_name(segmentation_mode, fallback='mode')}"
    )


def _resolve_unique_run_label(runs_root: Path, requested_label: str) -> str:
    """Return a filesystem-safe run label, appending a suffix on collision."""

    base_label = _sanitize_name(requested_label, fallback="run")
    candidate = base_label
    suffix = 2
    while (runs_root / candidate).exists():
        candidate = f"{base_label}_{suffix}"
        suffix += 1
    return candidate


def _input_source_metrics(input_sources: list[object]) -> list[dict[str, Any]]:
    """Return stable input identity fields without reading media content."""

    metrics: list[dict[str, Any]] = []
    for source in input_sources:
        source_payload = _as_dict(source)
        original_path = Path(str(source_payload.get("original_path") or ""))
        path_exists = original_path.exists()
        stat = original_path.stat() if path_exists else None
        metrics.append(
            {
                "source_id": source_payload.get("source_id"),
                "original_path": str(original_path),
                "original_filename": source_payload.get("original_filename"),
                "media_type": source_payload.get("media_type"),
                "duration_seconds": source_payload.get("duration_seconds"),
                "path_exists": path_exists,
                "file_size_bytes": stat.st_size if stat is not None else None,
                "modified_ns": stat.st_mtime_ns if stat is not None else None,
            },
        )
    return metrics


def _zeroish_reused_stages(stages: list[dict[str, Any]]) -> list[str]:
    """Return reused stages whose measured cost is effectively zero."""

    return [
        str(stage.get("stage_name") or "")
        for stage in stages
        if (
            (_safe_float(stage.get("duration_seconds"), 0.0) or 0.0) <= 0.001
            and (stage.get("used_cache") or stage.get("used_existing_artifact"))
        )
    ]


def _build_code_snapshot(code_root: str | Path | None) -> dict[str, Any]:
    """Return Git/code identity fields for later run archaeology."""

    root = Path(code_root).expanduser().resolve() if code_root is not None else Path.cwd()
    snapshot: dict[str, Any] = {
        "source_root": str(root),
        "git_available": False,
        "git_commit": None,
        "git_commit_short": None,
        "git_branch": None,
        "git_dirty": None,
        "git_status_short": None,
    }
    full_commit = _run_git(root, "rev-parse", "HEAD")
    if full_commit is None:
        return snapshot

    status_short = _run_git(root, "status", "--short") or ""
    snapshot.update(
        {
            "git_available": True,
            "git_commit": full_commit,
            "git_commit_short": _run_git(root, "rev-parse", "--short", "HEAD"),
            "git_branch": _run_git(root, "branch", "--show-current"),
            "git_dirty": bool(status_short.strip()),
            "git_status_short": status_short,
        },
    )
    return snapshot


def _run_git(root: Path, *args: str) -> str | None:
    """Run a Git command and return stripped stdout, or None on failure."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def _serialize_config(config: object | None) -> dict[str, Any]:
    """Return a JSON-safe, secret-redacted pipeline configuration snapshot."""

    if config is None:
        return {}
    if is_dataclass(config):
        payload = asdict(config)
    elif isinstance(config, dict):
        payload = config
    else:
        payload = {
            name: getattr(config, name)
            for name in dir(config)
            if not name.startswith("_") and not callable(getattr(config, name))
        }
    serialized = _serialize_value(payload)
    return serialized if isinstance(serialized, dict) else {}


def _serialize_value(value: object, *, key: str = "") -> object:
    """Return JSON-safe values with secrets redacted."""

    if _is_sensitive_key(key):
        return "<redacted>"
    if is_dataclass(value):
        return _serialize_value(asdict(value), key=key)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(item_key): _serialize_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _serialize_value(item, key=key)
            for item in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a config key should be redacted."""

    normalized = key.lower()
    return any(
        marker in normalized
        for marker in ("token", "secret", "password", "api_key", "auth")
    )


def _word_count(text: str) -> int:
    """Return a whitespace word count."""

    return len([token for token in text.split() if token.strip()])


def _safe_float(value: object, fallback: float | None = None) -> float | None:
    """Return a float when possible."""

    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_ratio(
    numerator: float | int | None,
    denominator: float | int | None,
) -> float | None:
    """Return a ratio or None when unavailable."""

    if numerator is None or denominator in {None, 0}:
        return None
    return float(numerator) / float(denominator)


def _as_dict(value: object) -> dict[str, Any]:
    """Return a dict when possible, otherwise an empty dict."""

    return value if isinstance(value, dict) else {}


def _sanitize_name(value: str, *, fallback: str) -> str:
    """Return a conservative filesystem label."""

    normalized = value.strip().lower()
    normalized = _SAFE_NAME_RE.sub("_", normalized)
    normalized = normalized.strip("._-")
    return normalized or fallback


def _now_iso() -> str:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
