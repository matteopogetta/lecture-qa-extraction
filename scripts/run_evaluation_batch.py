#!/usr/bin/env python3
"""Run local evaluation batches over a folder of media files."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = Path("/Users/matteopogetta/Documents/ExerPlazaSample/input")
DEFAULT_OUTPUT_PARENT = Path("/Users/matteopogetta/Documents/ExerPlazaSample/output")
DEFAULT_EVALUATION_ROOT = PROJECT_ROOT / "evaluations"
MEDIA_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}
SAFE_NAME_RE = re.compile(r"[^a-z0-9._-]+")
_NORMALIZED_AUDIO_SUFFIX_RE = re.compile(r"_mono_\d+hz$")
_CANONICAL_INPUT_LABEL_ALIASES = {
    "dialoghi_di_scienza_ep2_dialoghi_di_scienza_ep2_-_astrofisica": (
        "dialoghi_di_scienza_ep2_-_astrofisica"
    ),
    "dialoghi_di_scienza_ep2_astrofisica": (
        "dialoghi_di_scienza_ep2_-_astrofisica"
    ),
    "stanford_seminar_human_centered_explainable_ai_from_algorithms_to_user_experiences": (
        "stanford_seminar_-_human-centered_explainable_ai_from_algorithms_to_user_experiences"
    ),
}


def main(argv: list[str] | None = None) -> int:
    """Run one evaluation export per input file/profile pair."""

    parser = build_parser()
    args = parser.parse_args(argv)
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_root = _resolve_output_root(args.output_root)
    evaluation_root = Path(args.evaluation_root).expanduser().resolve()
    media_files = _discover_media_files(input_dir, args.pattern)
    if args.limit is not None:
        media_files = media_files[: args.limit]
    if not media_files:
        print(f"No media files found in {input_dir}", file=sys.stderr)
        return 2
    if "full" in args.profiles and not args.dry_run and not args.skip_full_preflight:
        full_environment = _check_full_profile_environment(args.python)
        if not full_environment["ok"]:
            print(
                "Full-profile environment check failed. Fix the environment "
                "before running full evaluations, or pass --skip-full-preflight "
                "only if you intentionally want to continue.",
                file=sys.stderr,
            )
            for problem in full_environment["problems"]:
                print(f"- {problem}", file=sys.stderr)
            return 2

    code_snapshot = _current_code_snapshot()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "batch_manifest.json"
    log_path = output_root / "batch_log.jsonl"
    batch_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "batch_id": output_root.name,
        "created_at": _now_iso(),
        "project_root": str(PROJECT_ROOT),
        "input_dir": str(input_dir),
        "output_root": str(output_root),
        "evaluation_root": str(evaluation_root),
        "profiles": args.profiles,
        "segmentation_mode": args.segmentation_mode,
        "force_recompute": args.force_recompute,
        "resume": args.resume,
        "skip_full_preflight": args.skip_full_preflight,
        "qa_semantic_responsiveness_enabled": args.enable_qa_semantic_responsiveness,
        "qa_semantic_responsiveness_gate_enabled": (
            args.enable_qa_semantic_responsiveness_gate
        ),
        "dry_run": args.dry_run,
        "code_snapshot": code_snapshot,
        "runs": [],
    }

    print(f"Input: {input_dir}")
    print(f"Output batch: {output_root}")
    print(f"Evaluation root: {evaluation_root}")
    print(f"Files: {len(media_files)} | profiles: {', '.join(args.profiles)}")

    failures = 0
    for media_path in media_files:
        input_label = _build_input_label(media_path, args.input_label_prefix)
        for profile in args.profiles:
            if args.resume and _has_existing_evaluation_run(
                evaluation_root=evaluation_root,
                input_label=input_label,
                profile=profile,
                segmentation_mode=args.segmentation_mode,
                expected_code_snapshot=code_snapshot,
            ):
                run_record = {
                    "input_file": str(media_path),
                    "input_label": input_label,
                    "profile": profile,
                    "segmentation_mode": args.segmentation_mode,
                    "evaluation_root": str(evaluation_root),
                    "status": "skipped_existing_compatible_evaluation_run",
                    "started_at": _now_iso(),
                    "finished_at": _now_iso(),
                    "returncode": 0,
                }
                print(
                    f"\n[{input_label}] profile={profile} "
                    f"mode={args.segmentation_mode} | skipped existing evaluation",
                )
                batch_manifest["runs"].append(run_record)
                _append_jsonl(log_path, run_record)
                _write_manifest(manifest_path, batch_manifest)
                continue
            run_record = _build_run_record(
                python_executable=args.python,
                main_path=args.main_path,
                media_path=media_path,
                output_root=output_root,
                evaluation_root=evaluation_root,
                input_label=input_label,
                profile=profile,
                segmentation_mode=args.segmentation_mode,
                force_recompute=args.force_recompute,
                enable_qa_semantic_responsiveness=(
                    args.enable_qa_semantic_responsiveness
                ),
                qa_semantic_responsiveness_model=(
                    args.qa_semantic_responsiveness_model
                ),
                qa_semantic_responsiveness_max_candidates=(
                    args.qa_semantic_responsiveness_max_candidates
                ),
                enable_qa_semantic_responsiveness_gate=(
                    args.enable_qa_semantic_responsiveness_gate
                ),
                qa_semantic_responsiveness_gate_min_score=(
                    args.qa_semantic_responsiveness_gate_min_score
                ),
                qa_semantic_responsiveness_gate_penalty=(
                    args.qa_semantic_responsiveness_gate_penalty
                ),
                enable_qa_speaker_check=args.enable_qa_speaker_check,
                qa_speaker_model_path=args.qa_speaker_model_path,
                qa_speaker_min_span_seconds=args.qa_speaker_min_span_seconds,
                qa_speaker_same_threshold=args.qa_speaker_same_threshold,
                qa_speaker_same_full_penalty_threshold=(
                    args.qa_speaker_same_full_penalty_threshold
                ),
                qa_speaker_different_threshold=(
                    args.qa_speaker_different_threshold
                ),
                qa_speaker_same_penalty=args.qa_speaker_same_penalty,
            )
            print(
                f"\n[{input_label}] profile={profile} "
                f"mode={args.segmentation_mode}",
            )
            print(" ".join(_quote_for_display(part) for part in run_record["command"]))
            if args.dry_run:
                run_record["status"] = "dry_run"
                run_record["finished_at"] = _now_iso()
                batch_manifest["runs"].append(run_record)
                _append_jsonl(log_path, run_record)
                _write_manifest(manifest_path, batch_manifest)
                continue

            completed = subprocess.run(
                run_record["command"],
                cwd=PROJECT_ROOT,
                text=True,
            )
            run_record["returncode"] = completed.returncode
            run_record["status"] = (
                "completed" if completed.returncode == 0 else "failed"
            )
            run_record["finished_at"] = _now_iso()
            batch_manifest["runs"].append(run_record)
            _append_jsonl(log_path, run_record)
            _write_manifest(manifest_path, batch_manifest)
            if completed.returncode != 0:
                failures += 1
                print(
                    f"Run failed with exit code {completed.returncode}",
                    file=sys.stderr,
                )
                if args.stop_on_error:
                    print(f"Stopped after failure. Manifest: {manifest_path}")
                    return completed.returncode

    _write_manifest(manifest_path, batch_manifest)
    print(f"\nBatch manifest: {manifest_path}")
    print(f"Batch log: {log_path}")
    if failures:
        print(f"Completed with {failures} failed run(s)", file=sys.stderr)
        return 1
    print("Batch completed successfully")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the batch runner parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Run light/full evaluation exports for all media files in a folder."
        ),
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Folder containing media files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help=(
            "Batch output directory for session JSON files. Defaults to "
            "/Users/matteopogetta/Documents/ExerPlazaSample/output/"
            "evaluation_batch_<UTC timestamp>."
        ),
    )
    parser.add_argument(
        "--evaluation-root",
        default=str(DEFAULT_EVALUATION_ROOT),
        help=f"Evaluation history root. Default: {DEFAULT_EVALUATION_ROOT}",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["light", "full"],
        help="Pipeline profiles to run for each file. Default: light full",
    )
    parser.add_argument(
        "--segmentation-mode",
        default="structural",
        choices=["structural", "adaptive", "windowed", "both"],
        help="Segmentation mode used for every run. Default: structural",
    )
    parser.add_argument(
        "--force-recompute",
        action="store_true",
        help="Disable reuse where supported, useful for cold benchmark runs.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip file/profile pairs that already have a matching valid "
            "evaluation run under evaluation-root and the same code snapshot. "
            "Runs with failed stages or a different code snapshot are not "
            "considered valid."
        ),
    )
    parser.add_argument(
        "--skip-full-preflight",
        action="store_true",
        help=(
            "Skip import checks for full-profile optional dependencies. Use only "
            "when you intentionally want to run even if alignment or diarization "
            "may fail."
        ),
    )
    parser.add_argument(
        "--pattern",
        default="*",
        help="Glob pattern inside input-dir. Default: *",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of media files to process.",
    )
    parser.add_argument(
        "--input-label-prefix",
        default=None,
        help="Optional prefix added to evaluation input labels.",
    )
    parser.add_argument(
        "--enable-qa-semantic-responsiveness",
        action="store_true",
        help=(
            "Enable optional local semantic responsiveness scoring in main.py. "
            "Default is off."
        ),
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-model",
        default=None,
        help="Local path or already-cached model id passed to main.py.",
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-max-candidates",
        type=int,
        default=None,
        help="Maximum extracted QA candidates scored by semantic responsiveness.",
    )
    parser.add_argument(
        "--enable-qa-semantic-responsiveness-gate",
        action="store_true",
        help=(
            "Enable the optional semantic responsiveness penalty gate in main.py. "
            "Default is off."
        ),
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-gate-min-score",
        type=float,
        default=None,
        help="Semantic responsiveness score threshold passed to main.py.",
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-gate-penalty",
        type=float,
        default=None,
        help="Semantic responsiveness gate penalty passed to main.py.",
    )
    parser.add_argument(
        "--enable-qa-speaker-check",
        action="store_true",
        help=(
            "Force the local QA speaker-change check in main.py; otherwise "
            "main.py auto-enables it only when the configured local model exists."
        ),
    )
    parser.add_argument(
        "--qa-speaker-model-path",
        default=None,
        help=(
            "Local speaker embedding model directory passed to main.py. "
            "No download is attempted when it is missing."
        ),
    )
    parser.add_argument(
        "--qa-speaker-min-span-seconds",
        type=float,
        default=None,
        help="Minimum span seconds for the speaker check passed to main.py.",
    )
    parser.add_argument(
        "--qa-speaker-same-threshold",
        type=float,
        default=None,
        help="Same-speaker similarity threshold passed to main.py.",
    )
    parser.add_argument(
        "--qa-speaker-same-full-penalty-threshold",
        type=float,
        default=None,
        help="Full same-speaker penalty threshold passed to main.py.",
    )
    parser.add_argument(
        "--qa-speaker-different-threshold",
        type=float,
        default=None,
        help="Different-speaker similarity threshold passed to main.py.",
    )
    parser.add_argument(
        "--qa-speaker-same-penalty",
        type=float,
        default=None,
        help="Same-speaker penalty passed to main.py.",
    )
    parser.add_argument(
        "--python",
        default="python",
        help="Python executable used to launch main.py. Default: python.",
    )
    parser.add_argument(
        "--main-path",
        default=str(PROJECT_ROOT / "main.py"),
        help="Path to main.py. Default: project main.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print and record commands without executing them.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the batch at the first failed run.",
    )
    return parser


def _resolve_output_root(value: str | None) -> Path:
    """Return a batch output directory."""

    if value:
        return Path(value).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return (DEFAULT_OUTPUT_PARENT / f"evaluation_batch_{timestamp}").resolve()


def _discover_media_files(input_dir: Path, pattern: str) -> list[Path]:
    """Return sorted media files from the input directory."""

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")
    return sorted(
        path
        for path in input_dir.glob(pattern)
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )


def _build_run_record(
    *,
    python_executable: str,
    main_path: str,
    media_path: Path,
    output_root: Path,
    evaluation_root: Path,
    input_label: str,
    profile: str,
    segmentation_mode: str,
    force_recompute: bool,
    enable_qa_semantic_responsiveness: bool,
    qa_semantic_responsiveness_model: str | None,
    qa_semantic_responsiveness_max_candidates: int | None,
    enable_qa_semantic_responsiveness_gate: bool,
    qa_semantic_responsiveness_gate_min_score: float | None,
    qa_semantic_responsiveness_gate_penalty: float | None,
    enable_qa_speaker_check: bool = False,
    qa_speaker_model_path: str | None = None,
    qa_speaker_min_span_seconds: float | None = None,
    qa_speaker_same_threshold: float | None = None,
    qa_speaker_same_full_penalty_threshold: float | None = None,
    qa_speaker_different_threshold: float | None = None,
    qa_speaker_same_penalty: float | None = None,
) -> dict[str, Any]:
    """Return one planned batch run record with its command."""

    session_output = output_root / input_label / profile / "session.json"
    session_output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        main_path,
        str(media_path),
        "--output",
        str(session_output),
        "--pipeline-profile",
        profile,
        "--segmentation-mode",
        segmentation_mode,
        "--export-evaluation-run",
        "--evaluation-root",
        str(evaluation_root),
        "--evaluation-input-label",
        input_label,
    ]
    if force_recompute:
        command.append("--force-recompute")
    if enable_qa_semantic_responsiveness:
        command.append("--enable-qa-semantic-responsiveness")
    if qa_semantic_responsiveness_model is not None:
        command.extend(
            [
                "--qa-semantic-responsiveness-model",
                qa_semantic_responsiveness_model,
            ],
        )
    if qa_semantic_responsiveness_max_candidates is not None:
        command.extend(
            [
                "--qa-semantic-responsiveness-max-candidates",
                str(qa_semantic_responsiveness_max_candidates),
            ],
        )
    if enable_qa_semantic_responsiveness_gate:
        command.append("--enable-qa-semantic-responsiveness-gate")
    if qa_semantic_responsiveness_gate_min_score is not None:
        command.extend(
            [
                "--qa-semantic-responsiveness-gate-min-score",
                str(qa_semantic_responsiveness_gate_min_score),
            ],
        )
    if qa_semantic_responsiveness_gate_penalty is not None:
        command.extend(
            [
                "--qa-semantic-responsiveness-gate-penalty",
                str(qa_semantic_responsiveness_gate_penalty),
            ],
        )
    if enable_qa_speaker_check:
        command.append("--enable-qa-speaker-check")
    if qa_speaker_model_path is not None:
        command.extend(["--qa-speaker-model-path", qa_speaker_model_path])
    if qa_speaker_min_span_seconds is not None:
        command.extend(
            ["--qa-speaker-min-span-seconds", str(qa_speaker_min_span_seconds)],
        )
    if qa_speaker_same_threshold is not None:
        command.extend(
            ["--qa-speaker-same-threshold", str(qa_speaker_same_threshold)],
        )
    if qa_speaker_same_full_penalty_threshold is not None:
        command.extend(
            [
                "--qa-speaker-same-full-penalty-threshold",
                str(qa_speaker_same_full_penalty_threshold),
            ],
        )
    if qa_speaker_different_threshold is not None:
        command.extend(
            ["--qa-speaker-different-threshold", str(qa_speaker_different_threshold)],
        )
    if qa_speaker_same_penalty is not None:
        command.extend(
            ["--qa-speaker-same-penalty", str(qa_speaker_same_penalty)],
        )
    return {
        "input_file": str(media_path),
        "input_label": input_label,
        "profile": profile,
        "segmentation_mode": segmentation_mode,
        "session_output": str(session_output),
        "evaluation_root": str(evaluation_root),
        "command": command,
        "status": "planned",
        "started_at": _now_iso(),
        "returncode": None,
    }


def _has_existing_evaluation_run(
    *,
    evaluation_root: Path,
    input_label: str,
    profile: str,
    segmentation_mode: str,
    expected_code_snapshot: dict[str, Any],
) -> bool:
    """Return whether a matching valid evaluation run already exists."""

    runs_root = evaluation_root / input_label / "runs"
    if not runs_root.is_dir():
        return False
    for metrics_path in sorted(runs_root.glob("*/metrics.json")):
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = metrics.get("run_identity")
        if not isinstance(identity, dict):
            continue
        if (
            identity.get("pipeline_profile") == profile
            and identity.get("segmentation_mode") == segmentation_mode
        ):
            return not _has_failed_stages(metrics) and _code_snapshot_matches(
                metrics.get("code_snapshot"),
                expected_code_snapshot,
            )
    return False


def _current_code_snapshot() -> dict[str, Any]:
    """Return a small Git snapshot used to keep resume decisions honest."""

    commit = _run_git(["rev-parse", "HEAD"])
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    status_short = _run_git(["status", "--short"])
    worktree_hash = _current_worktree_hash(status_short or "")
    return {
        "git_available": commit is not None,
        "git_commit": commit,
        "git_branch": branch,
        "git_dirty": bool(status_short),
        "git_status_short": status_short or "",
        "git_worktree_hash": worktree_hash,
    }


def _code_snapshot_matches(
    metrics_snapshot: object,
    expected_snapshot: dict[str, Any],
) -> bool:
    """Return whether an existing run was produced by the same source state."""

    if not isinstance(metrics_snapshot, dict):
        return False
    if not expected_snapshot.get("git_available"):
        return False
    return (
        metrics_snapshot.get("git_commit") == expected_snapshot.get("git_commit")
        and bool(metrics_snapshot.get("git_dirty"))
        == bool(expected_snapshot.get("git_dirty"))
        and str(metrics_snapshot.get("git_status_short") or "")
        == str(expected_snapshot.get("git_status_short") or "")
        and str(metrics_snapshot.get("git_worktree_hash") or "")
        == str(expected_snapshot.get("git_worktree_hash") or "")
    )


def _run_git(args: list[str]) -> str | None:
    """Run a Git command and return stripped stdout when available."""

    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _current_worktree_hash(status_short: str) -> str | None:
    """Return a content hash for dirty tracked and untracked Git changes."""

    if _run_git(["rev-parse", "--is-inside-work-tree"]) != "true":
        return None
    digest = hashlib.sha256()
    digest.update(status_short.encode("utf-8", errors="replace"))
    tracked_diff = _run_git(["diff", "--binary", "HEAD", "--"]) or ""
    digest.update(b"\0tracked-diff\0")
    digest.update(tracked_diff.encode("utf-8", errors="replace"))
    untracked_files = _run_git(["ls-files", "--others", "--exclude-standard"]) or ""
    digest.update(b"\0untracked-files\0")
    for relative_path in sorted(
        item.strip() for item in untracked_files.splitlines() if item.strip()
    ):
        digest.update(relative_path.encode("utf-8", errors="replace"))
        path = PROJECT_ROOT / relative_path
        try:
            if path.is_file():
                digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest()


def _has_failed_stages(metrics: dict[str, Any]) -> bool:
    """Return whether an evaluation run contains failed timing stages."""

    return any(
        isinstance(stage, dict) and stage.get("status") == "failed"
        for stage in metrics.get("timing_stages") or []
    )


def _check_full_profile_environment(python_executable: str) -> dict[str, Any]:
    """Return whether optional full-profile imports work in the target Python."""

    check_code = r"""
import importlib
import sys

checks = [
    ("whisperx alignment", "whisperx.alignment"),
    ("pyannote.audio", "pyannote.audio"),
]
problems = []
for label, module_name in checks:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        problems.append(f"{label}: {type(exc).__name__}: {exc}")

if problems:
    for problem in problems:
        print(problem)
    sys.exit(1)
"""
    completed = subprocess.run(
        [python_executable, "-c", check_code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part.strip()
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "problems": [
            line.strip()
            for line in output.splitlines()
            if line.strip()
        ],
    }


def _build_input_label(media_path: Path, prefix: str | None) -> str:
    """Return a stable evaluation input label for a media file."""

    label = _canonical_input_label(media_path.stem, fallback="input")
    if prefix:
        return f"{_canonical_input_label(prefix, fallback='batch')}_{label}"
    return label


def _canonical_input_label(value: str, *, fallback: str) -> str:
    """Return the canonical benchmark label used under evaluations/."""

    label = _sanitize_name(value, fallback=fallback)
    label = re.sub(r"^\d+_", "", label)
    label = _NORMALIZED_AUDIO_SUFFIX_RE.sub("", label)
    return _CANONICAL_INPUT_LABEL_ALIASES.get(label, label)


def _sanitize_name(value: str, *, fallback: str) -> str:
    """Return a conservative filesystem label."""

    normalized = SAFE_NAME_RE.sub("_", value.strip().lower())
    normalized = normalized.strip("._-")
    return normalized or fallback


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSON line to a log file."""

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Write the current batch manifest."""

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _quote_for_display(value: str) -> str:
    """Return a readable shell-ish argument."""

    if not value or any(char.isspace() for char in value):
        return "'" + value.replace("'", "'\"'\"'") + "'"
    return value


def _now_iso() -> str:
    """Return current UTC time."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
