"""Comparison export for local QA/C evaluation runs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from typing import Any, Callable


_REUSED_STATUSES = {"reused_from_cache", "reused_from_artifact"}
_NON_COMPUTE_STATUSES = {"disabled", "skipped"}
_REVIEW_SCORE_KEYS = (
    "question_quality",
    "answer_quality",
    "context_quality",
    "grounding_quality",
)


def export_evaluation_comparison(
    evaluation_input_directory: str | Path,
    *,
    write_markdown: bool = True,
) -> dict[str, Any]:
    """Compare all local runs for one evaluated input and write summary files."""

    input_directory = Path(evaluation_input_directory).expanduser().resolve()
    comparison = build_evaluation_comparison(input_directory)
    comparison_path = input_directory / "comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if write_markdown:
        (input_directory / "comparison.md").write_text(
            build_evaluation_comparison_markdown(comparison),
            encoding="utf-8",
        )
    return comparison


def build_evaluation_comparison(input_directory: str | Path) -> dict[str, Any]:
    """Return a comparison payload for all run folders under one input."""

    input_path = Path(input_directory).expanduser().resolve()
    runs_root = input_path / "runs"
    run_records = _load_run_records(runs_root)
    references = _build_stage_cold_references(run_records)
    run_summaries = [
        _summarize_run(record, references)
        for record in run_records
    ]
    return {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "input_identity": {
            "input_label": input_path.name,
            "evaluation_directory": str(input_path),
            "runs_directory": str(runs_root),
            "run_count": len(run_summaries),
            "reviewed_run_count": sum(
                1
                for summary in run_summaries
                if summary.get("ai_review", {}).get("review_status") == "completed"
            ),
        },
        "time_model": {
            "observed_runtime_seconds": (
                "Wall-clock time recorded by the run itself, including cache or "
                "artifact reuse overhead."
            ),
            "cold_equivalent_runtime_seconds": (
                "Estimated runtime for the same run configuration if reused stages "
                "had been recomputed. Reused stages are replaced by observed cold "
                "durations from other runs of the same input when available."
            ),
            "reference_priority": [
                "same stage + same pipeline profile + same segmentation mode",
                "same stage + same pipeline profile",
                "same stage across the same input",
            ],
            "complete_estimate_rule": (
                "A cold-equivalent total is complete only when every reused stage "
                "has a cold reference. Missing references are listed per run."
            ),
            "how_to_make_cold_reconstructable": (
                "For a real cold benchmark, save at least one --force-recompute "
                "evaluation run for each profile/mode you want to compare. Warm "
                "runs can then borrow those stage costs."
            ),
        },
        "stage_cold_references": _serialize_references(references),
        "run_summaries": run_summaries,
        "rankings": _build_rankings(run_summaries),
    }


def build_evaluation_comparison_markdown(comparison: dict[str, Any]) -> str:
    """Return a compact human-readable comparison report."""

    identity = _as_dict(comparison.get("input_identity"))
    lines = [
        f"# Evaluation Comparison: {identity.get('input_label', '')}",
        "",
        f"Generated at: `{comparison.get('generated_at', '')}`",
        "",
        "## Runtime Model",
        "",
        "- observed: recorded wall-clock runtime for the run as executed",
        (
            "- cold equivalent: observed runtime with reused stages replaced by "
            "cold references from other runs of the same input"
        ),
        (
            "- incomplete cold estimate: at least one reused stage has no cold "
            "reference yet; create a `--force-recompute` evaluation run to close it"
        ),
        "",
        "## Runs",
        "",
        (
            "| Run | Profile | Mode | Review | QA/C | Quality | Runtime value | "
            "Observed | Cold equivalent | Cache | Missing cold refs |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for run in comparison.get("run_summaries") or []:
        objective = _as_dict(run.get("objective_metrics"))
        ai_review = _as_dict(run.get("ai_review"))
        runtime = _as_dict(run.get("runtime"))
        warm = _as_dict(run.get("warm_cache_classification"))
        missing = runtime.get("cold_equivalent_missing_reused_stages") or []
        cold_value = runtime.get("cold_equivalent_runtime_seconds")
        if cold_value is None:
            cold_text = f">= {_format_seconds(runtime.get('cold_equivalent_known_seconds'))}"
        else:
            cold_text = _format_seconds(cold_value)
        lines.append(
            "| "
            f"`{run.get('run_label', '')}` | "
            f"{run.get('pipeline_profile', '')} | "
            f"{run.get('segmentation_mode', '')} | "
            f"{ai_review.get('review_status', '')} | "
            f"{objective.get('qa_candidate_count', '')}/"
            f"{objective.get('qa_candidates_with_context_count', '')} | "
            f"{ai_review.get('quality_score', '')} | "
            f"{ai_review.get('runtime_value_score', '')} | "
            f"{_format_seconds(runtime.get('observed_runtime_seconds'))} | "
            f"{cold_text} | "
            f"{warm.get('classification', '')} | "
            f"{', '.join(missing)} |"
        )
    lines.extend(["", "## Rankings", ""])
    rankings = _as_dict(comparison.get("rankings"))
    for name, ranked_ids in rankings.items():
        lines.append(f"- `{name}`: " + ", ".join(f"`{item}`" for item in ranked_ids))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            (
                "Use `observed_runtime_seconds` to understand what happened in that "
                "specific execution. Use `cold_equivalent_runtime_seconds` only when "
                "the estimate is complete; otherwise treat "
                "`cold_equivalent_known_seconds` as a lower bound."
            ),
        ],
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for local evaluation comparison."""

    parser = argparse.ArgumentParser(
        description="Compare local evaluation runs for one input directory.",
    )
    parser.add_argument(
        "evaluation_input_directory",
        help="Path like evaluations/icwros containing a runs/ directory.",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="Write comparison.json only.",
    )
    args = parser.parse_args(argv)
    comparison = export_evaluation_comparison(
        args.evaluation_input_directory,
        write_markdown=not args.no_markdown,
    )
    output_dir = Path(args.evaluation_input_directory).expanduser().resolve()
    print(
        "Wrote "
        f"{output_dir / 'comparison.json'}"
        + ("" if args.no_markdown else f" and {output_dir / 'comparison.md'}"),
    )
    print(f"Compared {comparison['input_identity']['run_count']} runs")
    return 0


def _load_run_records(runs_root: Path) -> list[dict[str, Any]]:
    """Load metrics and AI review payloads from run directories."""

    if not runs_root.is_dir():
        raise FileNotFoundError(f"Runs directory does not exist: {runs_root}")
    records: list[dict[str, Any]] = []
    for run_directory in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        metrics_path = run_directory / "metrics.json"
        if not metrics_path.is_file():
            continue
        ai_review_path = run_directory / "ai_review.json"
        metrics = _read_json(metrics_path)
        ai_review = _read_json(ai_review_path) if ai_review_path.is_file() else {}
        records.append(
            {
                "run_directory": run_directory,
                "metrics": metrics,
                "ai_review": ai_review,
            },
        )
    return records


def _build_stage_cold_references(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Collect observed non-reused stage durations that can estimate cold cost."""

    references: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        metrics = _as_dict(record.get("metrics"))
        identity = _as_dict(metrics.get("run_identity"))
        for stage in _stage_list(metrics):
            if not _is_cold_observed_stage(stage):
                continue
            stage_name = str(stage.get("stage_name") or "")
            duration = _safe_float(stage.get("duration_seconds"))
            if not stage_name or duration is None or duration <= 0:
                continue
            references.setdefault(stage_name, []).append(
                {
                    "duration_seconds": duration,
                    "run_id": identity.get("run_id"),
                    "run_label": identity.get("run_label"),
                    "pipeline_profile": identity.get("pipeline_profile"),
                    "segmentation_mode": identity.get("segmentation_mode"),
                    "stage_status": stage.get("status"),
                },
            )
    return references


def _summarize_run(
    record: dict[str, Any],
    references: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return one normalized run summary."""

    metrics = _as_dict(record.get("metrics"))
    ai_review = _as_dict(record.get("ai_review"))
    identity = _as_dict(metrics.get("run_identity"))
    objective = _as_dict(metrics.get("objective_metrics"))
    timing_summary = _as_dict(metrics.get("timing_summary"))
    runtime_metrics = _as_dict(metrics.get("runtime_metrics"))
    stage_estimate = _estimate_cold_runtime(metrics, references)
    observed_runtime = _safe_float(runtime_metrics.get("total_duration_seconds"))
    if observed_runtime is None:
        observed_runtime = _safe_float(timing_summary.get("total_duration_seconds"))
    return {
        "run_id": identity.get("run_id"),
        "run_label": identity.get("run_label") or Path(record["run_directory"]).name,
        "pipeline_profile": identity.get("pipeline_profile"),
        "segmentation_mode": identity.get("segmentation_mode"),
        "pipeline_execution_mode": identity.get("pipeline_execution_mode"),
        "code_snapshot": _compact_code_snapshot(_as_dict(metrics.get("code_snapshot"))),
        "objective_metrics": {
            key: objective.get(key)
            for key in (
                "qa_candidate_count",
                "qa_candidates_with_answer_count",
                "qa_candidates_with_context_count",
                "qa_candidates_with_review_flags_count",
                "transcript_word_count",
                "segment_count",
                "sentence_count",
            )
        },
        "ai_review": _summarize_ai_review(ai_review),
        "warm_cache_classification": {
            "classification": _classify_warm_cache(timing_summary, runtime_metrics),
            "any_cache_hit": _first_present(
                runtime_metrics.get("any_cache_hit"),
                timing_summary.get("any_cache_hit"),
            ),
            "any_artifact_reuse": _first_present(
                runtime_metrics.get("any_artifact_reuse"),
                timing_summary.get("any_artifact_reuse"),
            ),
            "reused_cache_stage_count": _first_present(
                runtime_metrics.get("reused_cache_stage_count"),
                timing_summary.get("reused_cache_stage_count"),
            ),
            "reused_artifact_stage_count": _first_present(
                runtime_metrics.get("reused_artifact_stage_count"),
                timing_summary.get("reused_artifact_stage_count"),
            ),
            "zero_or_near_zero_reused_stages": runtime_metrics.get(
                "zero_or_near_zero_reused_stages",
            ) or [],
        },
        "runtime": {
            "observed_runtime_seconds": observed_runtime,
            "cold_equivalent_runtime_seconds": (
                stage_estimate["cold_equivalent_known_seconds"]
                if stage_estimate["cold_equivalent_estimation_complete"]
                else None
            ),
            "cold_equivalent_known_seconds": stage_estimate[
                "cold_equivalent_known_seconds"
            ],
            "cold_equivalent_estimation_complete": stage_estimate[
                "cold_equivalent_estimation_complete"
            ],
            "cold_equivalent_missing_reused_stages": stage_estimate[
                "missing_reused_stages"
            ],
            "observed_reused_stage_seconds": stage_estimate[
                "observed_reused_stage_seconds"
            ],
            "estimated_recomputed_reused_stage_seconds": stage_estimate[
                "estimated_recomputed_reused_stage_seconds"
            ],
            "stage_estimates": stage_estimate["stage_estimates"],
        },
    }


def _estimate_cold_runtime(
    metrics: dict[str, Any],
    references: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Estimate a cold-equivalent runtime by replacing reused stage costs."""

    identity = _as_dict(metrics.get("run_identity"))
    total_known = 0.0
    observed_reused = 0.0
    estimated_reused = 0.0
    missing: list[str] = []
    stage_estimates: list[dict[str, Any]] = []
    for stage in _stage_list(metrics):
        stage_name = str(stage.get("stage_name") or "")
        duration = _safe_float(stage.get("duration_seconds"), 0.0) or 0.0
        status = str(stage.get("status") or "")
        if _is_reused_stage(stage):
            observed_reused += duration
            reference = _select_reference(stage_name, identity, references)
            if reference is None:
                missing.append(stage_name)
                stage_estimates.append(
                    {
                        "stage_name": stage_name,
                        "observed_duration_seconds": duration,
                        "cold_estimated_duration_seconds": None,
                        "estimation_status": "missing_cold_reference",
                        "stage_status": status,
                    },
                )
                continue
            estimated = _safe_float(reference.get("duration_seconds"), 0.0) or 0.0
            total_known += estimated
            estimated_reused += estimated
            stage_estimates.append(
                {
                    "stage_name": stage_name,
                    "observed_duration_seconds": duration,
                    "cold_estimated_duration_seconds": estimated,
                    "estimation_status": "replaced_from_cold_reference",
                    "reference_run_id": reference.get("run_id"),
                    "reference_strategy": reference.get("reference_strategy"),
                    "stage_status": status,
                },
            )
            continue
        total_known += duration
        stage_estimates.append(
            {
                "stage_name": stage_name,
                "observed_duration_seconds": duration,
                "cold_estimated_duration_seconds": duration,
                "estimation_status": (
                    "not_applicable"
                    if status in _NON_COMPUTE_STATUSES
                    else "observed_cold_or_local_compute"
                ),
                "stage_status": status,
            },
        )
    return {
        "cold_equivalent_known_seconds": round(total_known, 6),
        "cold_equivalent_estimation_complete": not missing,
        "missing_reused_stages": sorted(set(missing)),
        "observed_reused_stage_seconds": round(observed_reused, 6),
        "estimated_recomputed_reused_stage_seconds": round(estimated_reused, 6),
        "stage_estimates": stage_estimates,
    }


def _select_reference(
    stage_name: str,
    identity: dict[str, Any],
    references: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Select the best cold reference for a reused stage."""

    candidates = references.get(stage_name) or []
    if not candidates:
        return None
    same_profile_mode = [
        item
        for item in candidates
        if item.get("pipeline_profile") == identity.get("pipeline_profile")
        and item.get("segmentation_mode") == identity.get("segmentation_mode")
    ]
    if same_profile_mode:
        return _median_reference(same_profile_mode, "same_profile_and_mode")
    same_profile = [
        item
        for item in candidates
        if item.get("pipeline_profile") == identity.get("pipeline_profile")
    ]
    if same_profile:
        return _median_reference(same_profile, "same_profile")
    return _median_reference(candidates, "same_input_stage")


def _median_reference(candidates: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Return the sample closest to the median duration."""

    durations = [float(item["duration_seconds"]) for item in candidates]
    target = median(durations)
    selected = min(
        candidates,
        key=lambda item: abs(float(item["duration_seconds"]) - target),
    )
    result = dict(selected)
    result["sample_count"] = len(candidates)
    result["median_duration_seconds"] = round(target, 6)
    result["reference_strategy"] = strategy
    return result


def _summarize_ai_review(ai_review: dict[str, Any]) -> dict[str, Any]:
    """Return compact review scores and decision counts."""

    overall = _as_dict(ai_review.get("overall"))
    candidates = [
        candidate
        for candidate in ai_review.get("candidates") or []
        if isinstance(candidate, dict)
    ]
    decisions: dict[str, int] = {}
    totals = {key: 0.0 for key in _REVIEW_SCORE_KEYS}
    counts = {key: 0 for key in _REVIEW_SCORE_KEYS}
    for candidate in candidates:
        decision = str(candidate.get("keep_decision") or "missing")
        decisions[decision] = decisions.get(decision, 0) + 1
        for key in _REVIEW_SCORE_KEYS:
            value = _safe_float(candidate.get(key))
            if value is None:
                continue
            totals[key] += value
            counts[key] += 1
    average_scores = {
        key: round(totals[key] / counts[key], 3) if counts[key] else None
        for key in _REVIEW_SCORE_KEYS
    }
    return {
        "review_status": ai_review.get("review_status"),
        "quality_score": overall.get("quality_score"),
        "runtime_value_score": overall.get("runtime_value_score"),
        "summary": overall.get("summary"),
        "candidate_count": len(candidates),
        "decision_counts": decisions,
        "average_candidate_scores": average_scores,
        "failure_modes": ai_review.get("failure_modes") or [],
        "recommendations": ai_review.get("recommendations") or [],
    }


def _build_rankings(run_summaries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return useful run rankings by quality and runtime."""

    return {
        "quality_score_desc": _rank(
            run_summaries,
            lambda run: _safe_float(run.get("ai_review", {}).get("quality_score")),
            reverse=True,
        ),
        "runtime_value_score_desc": _rank(
            run_summaries,
            lambda run: _safe_float(
                run.get("ai_review", {}).get("runtime_value_score"),
            ),
            reverse=True,
        ),
        "observed_runtime_asc": _rank(
            run_summaries,
            lambda run: _safe_float(
                run.get("runtime", {}).get("observed_runtime_seconds"),
            ),
            reverse=False,
        ),
        "complete_cold_runtime_asc": _rank(
            [
                run
                for run in run_summaries
                if run.get("runtime", {}).get("cold_equivalent_estimation_complete")
            ],
            lambda run: _safe_float(
                run.get("runtime", {}).get("cold_equivalent_runtime_seconds"),
            ),
            reverse=False,
        ),
    }


def _rank(
    runs: list[dict[str, Any]],
    key_func: Callable[[dict[str, Any]], float | None],
    *,
    reverse: bool,
) -> list[str]:
    """Rank runs while dropping missing metric values."""

    keyed: list[tuple[float, str]] = []
    for run in runs:
        value = key_func(run)
        if value is None:
            continue
        keyed.append((float(value), str(run.get("run_id") or run.get("run_label"))))
    return [item[1] for item in sorted(keyed, reverse=reverse)]


def _serialize_references(
    references: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Return cold reference samples in a compact serializable shape."""

    serialized: dict[str, dict[str, Any]] = {}
    for stage_name, samples in sorted(references.items()):
        durations = [float(item["duration_seconds"]) for item in samples]
        serialized[stage_name] = {
            "sample_count": len(samples),
            "median_duration_seconds": round(median(durations), 6),
            "samples": samples,
        }
    return serialized


def _stage_list(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Return timing stage dicts from metrics."""

    return [
        stage
        for stage in metrics.get("timing_stages") or []
        if isinstance(stage, dict)
    ]


def _is_reused_stage(stage: dict[str, Any]) -> bool:
    """Return whether a stage used cache or an existing artifact."""

    return bool(
        stage.get("used_cache")
        or stage.get("used_existing_artifact")
        or str(stage.get("status") or "") in _REUSED_STATUSES
    )


def _is_cold_observed_stage(stage: dict[str, Any]) -> bool:
    """Return whether a stage duration represents actual local computation."""

    status = str(stage.get("status") or "")
    if status in _NON_COMPUTE_STATUSES or status in _REUSED_STATUSES:
        return False
    if _is_reused_stage(stage):
        return False
    return (_safe_float(stage.get("duration_seconds"), 0.0) or 0.0) > 0


def _classify_warm_cache(
    timing_summary: dict[str, Any],
    runtime_metrics: dict[str, Any],
) -> str:
    """Return cold/warm classification for a run."""

    any_cache = bool(
        _first_present(
            runtime_metrics.get("any_cache_hit"),
            timing_summary.get("any_cache_hit"),
        ),
    )
    any_artifact = bool(
        _first_present(
            runtime_metrics.get("any_artifact_reuse"),
            timing_summary.get("any_artifact_reuse"),
        ),
    )
    if any_cache or any_artifact:
        return "warm_or_cached"
    return "cold_observed"


def _compact_code_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep only comparison-relevant code identity fields."""

    return {
        "git_commit_short": snapshot.get("git_commit_short"),
        "git_branch": snapshot.get("git_branch"),
        "git_dirty": snapshot.get("git_dirty"),
    }


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, returning an error payload on parse failure."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"read_error": str(error)}
    return payload if isinstance(payload, dict) else {"read_error": "not_a_json_object"}


def _format_seconds(value: object) -> str:
    """Format seconds for Markdown tables."""

    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.3f}s"


def _first_present(*values: object) -> object:
    """Return the first non-None value."""

    for value in values:
        if value is not None:
            return value
    return None


def _safe_float(value: object, fallback: float | None = None) -> float | None:
    """Return a float when possible."""

    try:
        if value is None or value == "":
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_dict(value: object) -> dict[str, Any]:
    """Return a dict when possible, otherwise an empty dict."""

    return value if isinstance(value, dict) else {}


def _now_iso() -> str:
    """Return current UTC time."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
