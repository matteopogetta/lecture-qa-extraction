"""Command-line interface for the lecture analyzer project."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from lecture_analyzer.core.config import AppConfig
from lecture_analyzer.core.exceptions import InputValidationError
from lecture_analyzer.core.pipeline import LectureAnalyzerPipeline

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_dotenv_file(dotenv_path: Path = Path(".env")) -> None:
    """Load simple key-value pairs from a local .env file if present."""

    if not dotenv_path.is_file():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for the official and smoke workflows."""

    parser = argparse.ArgumentParser(
        description=(
            "Official CLI for Lecture QA Extraction. The primary path runs "
            "the real src-based lecture-processing pipeline. A temporary "
            "smoke mode remains available for packaging and diagnostic checks."
        ),
        epilog=(
            "Default guidance: use positional input paths to run the official "
            "lecture analyzer pipeline. Use --smoke together with --input to "
            "run the temporary diagnostic placeholder flow. The root main.py "
            "file remains a temporary compatibility wrapper."
        ),
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Run the temporary smoke/diagnostic placeholder flow instead of "
            "the official src-based lecture-processing pipeline."
        ),
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        help=(
            "Input path for smoke/diagnostic mode. This is also accepted as a "
            "fallback input source by the official pipeline when needed."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("OUTPUT_DIR", "output"),
        help=(
            "Output directory for smoke mode, or the fallback output directory "
            "used when the official pipeline is invoked without --output."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level such as DEBUG, INFO, WARNING, or ERROR.",
    )
    parser.add_argument(
        "--transcription-model",
        default=os.getenv("TRANSCRIPTION_MODEL", ""),
        help="Optional label recorded by the temporary smoke/diagnostic flow.",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=(
            "Official positional input paths for the real src-based lecture "
            "processing pipeline."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path or directory for the official lecture analyzer pipeline.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional custom session identifier for the official pipeline.",
    )
    parser.add_argument(
        "--work-dir",
        default="artifacts",
        help="Working directory for intermediate official-pipeline artifacts.",
    )
    parser.add_argument(
        "--pipeline-profile",
        default="current",
        choices=("current", "light", "quality", "quality_local", "full", "diagnostic"),
        help=(
            "Execution profile for optional pipeline branches. The default "
            "'current' preserves the existing behavior."
        ),
    )
    parser.add_argument(
        "--normalized-audio-format",
        default="wav",
        choices=("wav", "flac"),
        help="Normalized audio format for the official pipeline.",
    )
    parser.add_argument(
        "--force-normalization",
        action="store_true",
        help="Force normalized audio regeneration in the official pipeline.",
    )
    parser.add_argument(
        "--transcription-cache-dir",
        default=None,
        help="Optional transcription cache directory for the official pipeline.",
    )
    parser.add_argument(
        "--disable-transcription-cache",
        action="store_true",
        help="Disable transcription cache reuse in the official pipeline.",
    )
    parser.add_argument(
        "--transcription-compute-type",
        default="auto",
        help="Compute type for the official faster-whisper backend.",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="Force a full recompute in the official pipeline.",
    )
    parser.add_argument(
        "--disable-alignment",
        action="store_true",
        help="Disable transcript alignment in the official pipeline.",
    )
    parser.add_argument(
        "--alignment-model",
        default=None,
        help="Optional alignment model override for the official pipeline.",
    )
    parser.add_argument(
        "--alignment-device",
        default="cpu",
        help="Execution device for official alignment.",
    )
    parser.add_argument(
        "--enable-diarization",
        action="store_true",
        help="Enable speaker diarization in the official pipeline.",
    )
    parser.add_argument(
        "--diarization-device",
        default="cpu",
        help="Execution device for official diarization.",
    )
    parser.add_argument(
        "--num-speakers",
        default=None,
        type=int,
        help="Optional exact speaker count hint for official diarization.",
    )
    parser.add_argument(
        "--min-speakers",
        default=None,
        type=int,
        help="Optional lower speaker count bound for official diarization.",
    )
    parser.add_argument(
        "--max-speakers",
        default=None,
        type=int,
        help="Optional upper speaker count bound for official diarization.",
    )
    parser.add_argument(
        "--segmentation-mode",
        default="structural",
        help="Official segmentation mode: structural, windowed, adaptive, or both.",
    )
    parser.add_argument(
        "--enable-qa-speaker-check",
        action="store_true",
        help=(
            "Enable the post-extraction QA speaker-similarity check. By "
            "default it runs automatically only when the configured local "
            "speaker model exists."
        ),
    )
    parser.add_argument(
        "--qa-speaker-model-path",
        default=None,
        help=(
            "Local SpeechBrain/ECAPA speaker embedding model directory. "
            "No model is downloaded at runtime when this is missing; the "
            "project-local model path is used by default."
        ),
    )
    parser.add_argument(
        "--qa-speaker-min-span-seconds",
        default=1.5,
        type=float,
        help="Minimum question/answer span duration for the speaker check.",
    )
    parser.add_argument(
        "--qa-speaker-same-threshold",
        default=0.72,
        type=float,
        help="Cosine threshold above which same speaker is suspected.",
    )
    parser.add_argument(
        "--qa-speaker-same-full-penalty-threshold",
        default=0.85,
        type=float,
        help=(
            "Cosine threshold at or above which the full same-speaker penalty "
            "is applied; values between same-threshold and this are graduated."
        ),
    )
    parser.add_argument(
        "--qa-speaker-different-threshold",
        default=0.45,
        type=float,
        help="Cosine threshold below which different speaker is likely.",
    )
    parser.add_argument(
        "--qa-speaker-same-penalty",
        default=0.25,
        type=float,
        help=(
            "Confidence penalty for same-speaker suspected candidates when "
            "no local Socratic pattern is recognized."
        ),
    )
    parser.add_argument(
        "--qa-speaker-rescue-max-checks",
        default=40,
        type=int,
        help=(
            "Maximum suppressed soft-gate candidates to speaker-check for "
            "quality_local rescue. Set 0 to disable rescue checks."
        ),
    )
    parser.add_argument(
        "--qa-speaker-rescue-max-candidates",
        default=8,
        type=int,
        help=(
            "Maximum speaker-assisted rescued candidates to emit per "
            "quality_local run. Set 0 to disable rescue emission."
        ),
    )
    parser.add_argument(
        "--qa-speaker-rescue-confidence-margin",
        default=0.08,
        type=float,
        help=(
            "Allowed below-min-confidence margin for speaker-assisted rescue "
            "candidates."
        ),
    )
    parser.add_argument(
        "--qa-speaker-rescue-min-text-quality",
        default=0.50,
        type=float,
        help=(
            "Minimum existing question/answer quality score required before "
            "speaker-assisted rescue can emit a candidate."
        ),
    )
    parser.add_argument(
        "--enable-qa-semantic-responsiveness",
        action="store_true",
        help=(
            "Enable optional local embedding semantic responsiveness scoring "
            "over already-extracted QA candidates only. Default is off."
        ),
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-model",
        default=None,
        help=(
            "Local path or already-cached Hugging Face id for the optional "
            "semantic responsiveness sentence embedding model."
        ),
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-max-candidates",
        default=None,
        type=int,
        help="Maximum extracted QA candidates scored by semantic responsiveness.",
    )
    parser.add_argument(
        "--enable-qa-semantic-responsiveness-gate",
        action="store_true",
        help=(
            "Apply the configured semantic responsiveness penalty gate. "
            "Default is off unless this flag is supplied."
        ),
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-gate-min-score",
        default=None,
        type=float,
        help="Minimum semantic responsiveness score before gate penalty applies.",
    )
    parser.add_argument(
        "--qa-semantic-responsiveness-gate-penalty",
        default=None,
        type=float,
        help="Confidence/final-quality penalty for weak semantic responsiveness.",
    )
    parser.add_argument(
        "--export-ai-review-packet",
        action="store_true",
        help=(
            "Write a local Markdown packet with transcript, QA/C candidates, "
            "and reviewer instructions for external human or chatbot review."
        ),
    )
    parser.add_argument(
        "--ai-review-packet-path",
        default=None,
        help=(
            "Optional Markdown file or directory for AI/human review packets. "
            "Defaults to the pipeline working directory."
        ),
    )
    parser.add_argument(
        "--export-evaluation-run",
        action="store_true",
        help=(
            "Write a persistent local evaluation run folder with session JSON, "
            "review packet, AI review placeholder, and metrics."
        ),
    )
    parser.add_argument(
        "--evaluation-root",
        default=None,
        help=(
            "Root directory for local evaluation history. Defaults to "
            "./evaluations, which is ignored by Git."
        ),
    )
    parser.add_argument(
        "--evaluation-input-label",
        default=None,
        help="Optional stable input label used under the evaluation root.",
    )
    parser.add_argument(
        "--evaluation-run-label",
        default=None,
        help=(
            "Advanced override for the run folder label. Omit it to generate "
            "a unique timestamp/profile/mode label automatically."
        ),
    )
    return parser


def resolve_smoke_input_argument(args: argparse.Namespace) -> str:
    """Resolve the smoke-mode input argument."""

    if args.input_path:
        return args.input_path
    raise InputValidationError("An input path is required. Use --input PATH.")


def should_use_root_pipeline(args: argparse.Namespace) -> bool:
    """Decide whether the official lecture analyzer pipeline should handle the request."""

    if args.smoke:
        return False

    return any(
        [
            bool(args.inputs),
            args.output is not None,
            args.session_id is not None,
            args.work_dir != "artifacts",
            args.pipeline_profile != "current",
            args.normalized_audio_format != "wav",
            args.force_normalization,
            args.transcription_cache_dir is not None,
            args.disable_transcription_cache,
            args.transcription_compute_type != "auto",
            args.from_scratch,
            args.disable_alignment,
            args.alignment_model is not None,
            args.alignment_device != "cpu",
            args.enable_diarization,
            args.diarization_device != "cpu",
            args.num_speakers is not None,
            args.min_speakers is not None,
            args.max_speakers is not None,
            args.segmentation_mode != "structural",
            args.enable_qa_speaker_check,
            args.qa_speaker_model_path is not None,
            args.qa_speaker_min_span_seconds != 1.5,
            args.qa_speaker_same_threshold != 0.72,
            args.qa_speaker_same_full_penalty_threshold != 0.85,
            args.qa_speaker_different_threshold != 0.45,
            args.qa_speaker_same_penalty != 0.25,
            args.qa_speaker_rescue_max_checks != 40,
            args.qa_speaker_rescue_max_candidates != 8,
            args.qa_speaker_rescue_confidence_margin != 0.08,
            args.qa_speaker_rescue_min_text_quality != 0.50,
            args.enable_qa_semantic_responsiveness,
            args.qa_semantic_responsiveness_model is not None,
            args.qa_semantic_responsiveness_max_candidates is not None,
            args.enable_qa_semantic_responsiveness_gate,
            args.qa_semantic_responsiveness_gate_min_score is not None,
            args.qa_semantic_responsiveness_gate_penalty is not None,
            args.export_ai_review_packet,
            args.ai_review_packet_path is not None,
            args.export_evaluation_run,
            args.evaluation_root is not None,
            args.evaluation_input_label is not None,
            args.evaluation_run_label is not None,
        ],
    )


def configure_logging(level_name: str) -> None:
    """Configure application logging for the CLI execution."""

    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


def run_smoke_pipeline(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Run the temporary smoke/diagnostic placeholder flow."""

    try:
        input_path = resolve_smoke_input_argument(args)
        config = AppConfig(
            input_path=Path(input_path),
            output_dir=Path(args.output_dir),
            log_level=args.log_level.upper(),
            transcription_model=args.transcription_model,
        )
        pipeline = LectureAnalyzerPipeline(config=config)
        result_path = pipeline.run()
    except InputValidationError as error:
        parser.exit(status=1, message=f"Error: {error}\n")

    LOGGER.info("Smoke analysis saved to %s", result_path)
    return 0


def run_root_pipeline(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Run the official src-based lecture processing pipeline."""

    from lecture_analyzer.core.config import PipelineConfig as LegacyPipelineConfig
    from lecture_analyzer.core.errors import IngestionError
    from lecture_analyzer.core.pipeline import (
        LectureProcessingPipeline as LegacyPipeline,
    )

    input_paths: list[str] = list(args.inputs)
    if not input_paths and args.input_path:
        input_paths = [args.input_path]
    if not input_paths:
        parser.exit(
            status=1,
            message="Error: At least one input path is required.\n",
        )

    config = LegacyPipelineConfig(
        pipeline_profile=args.pipeline_profile,
        working_directory=Path(args.work_dir),
    )
    config_overrides: dict[str, object] = {
        "normalized_audio_format": args.normalized_audio_format,
        "transcription_compute_type": args.transcription_compute_type,
    }
    if args.force_normalization:
        config_overrides["overwrite_normalized_audio"] = True
    if args.disable_transcription_cache:
        config_overrides["transcription_cache_enabled"] = False
    if args.transcription_cache_dir is not None:
        config_overrides["transcription_cache_directory"] = Path(
            args.transcription_cache_dir,
        )
    if args.from_scratch:
        config_overrides["force_recompute"] = True
    if args.disable_alignment:
        config_overrides["transcript_alignment_enabled"] = False
    if args.alignment_model is not None:
        config_overrides["transcript_alignment_model_name"] = args.alignment_model
    if args.alignment_device != "cpu":
        config_overrides["transcript_alignment_device"] = args.alignment_device
    if args.enable_diarization:
        config_overrides["diarization_enabled"] = True
    if args.diarization_device != "cpu":
        config_overrides["diarization_device"] = args.diarization_device
    if args.num_speakers is not None:
        config_overrides["diarization_num_speakers"] = args.num_speakers
    if args.min_speakers is not None:
        config_overrides["diarization_min_speakers"] = args.min_speakers
    if args.max_speakers is not None:
        config_overrides["diarization_max_speakers"] = args.max_speakers
    if args.segmentation_mode != "structural":
        config_overrides["segmentation_mode"] = args.segmentation_mode
    if args.enable_qa_speaker_check:
        config_overrides["qa_speaker_check_enabled"] = True
    if args.qa_speaker_model_path is not None:
        config_overrides["qa_speaker_check_model_path"] = Path(
            args.qa_speaker_model_path,
        )
    if args.qa_speaker_min_span_seconds != 1.5:
        config_overrides["qa_speaker_check_min_span_seconds"] = (
            args.qa_speaker_min_span_seconds
        )
    if args.qa_speaker_same_threshold != 0.72:
        config_overrides["qa_speaker_check_same_threshold"] = (
            args.qa_speaker_same_threshold
        )
    if args.qa_speaker_same_full_penalty_threshold != 0.85:
        config_overrides["qa_speaker_check_same_full_penalty_threshold"] = (
            args.qa_speaker_same_full_penalty_threshold
        )
    if args.qa_speaker_different_threshold != 0.45:
        config_overrides["qa_speaker_check_different_threshold"] = (
            args.qa_speaker_different_threshold
        )
    if args.qa_speaker_same_penalty != 0.25:
        config_overrides["qa_speaker_check_same_speaker_penalty"] = (
            args.qa_speaker_same_penalty
        )
    if args.qa_speaker_rescue_max_checks != 40:
        config_overrides["qa_speaker_rescue_max_checks_per_run"] = (
            args.qa_speaker_rescue_max_checks
        )
    if args.qa_speaker_rescue_max_candidates != 8:
        config_overrides["qa_speaker_rescue_max_candidates_per_run"] = (
            args.qa_speaker_rescue_max_candidates
        )
    if args.qa_speaker_rescue_confidence_margin != 0.08:
        config_overrides["qa_speaker_rescue_min_confidence_margin"] = (
            args.qa_speaker_rescue_confidence_margin
        )
    if args.qa_speaker_rescue_min_text_quality != 0.50:
        config_overrides["qa_speaker_rescue_min_text_quality_score"] = (
            args.qa_speaker_rescue_min_text_quality
        )
    if args.enable_qa_semantic_responsiveness:
        config_overrides["qa_semantic_responsiveness_enabled"] = True
    if args.qa_semantic_responsiveness_model is not None:
        config_overrides["qa_semantic_responsiveness_model_name"] = (
            args.qa_semantic_responsiveness_model
        )
    if args.qa_semantic_responsiveness_max_candidates is not None:
        config_overrides["qa_semantic_responsiveness_max_candidates"] = (
            args.qa_semantic_responsiveness_max_candidates
        )
    if args.enable_qa_semantic_responsiveness_gate:
        config_overrides["qa_semantic_responsiveness_gate_enabled"] = True
    if args.qa_semantic_responsiveness_gate_min_score is not None:
        config_overrides["qa_semantic_responsiveness_gate_min_score"] = (
            args.qa_semantic_responsiveness_gate_min_score
        )
    if args.qa_semantic_responsiveness_gate_penalty is not None:
        config_overrides["qa_semantic_responsiveness_gate_penalty"] = (
            args.qa_semantic_responsiveness_gate_penalty
        )
    if args.export_ai_review_packet:
        config_overrides["export_ai_review_packet"] = True
    if args.ai_review_packet_path is not None:
        config_overrides["ai_review_packet_path"] = Path(args.ai_review_packet_path)
    if args.export_evaluation_run:
        config_overrides["export_evaluation_run"] = True
    if args.evaluation_root is not None:
        config_overrides["evaluation_root_directory"] = Path(args.evaluation_root)
    if args.evaluation_input_label is not None:
        config_overrides["evaluation_input_label"] = args.evaluation_input_label
    if args.evaluation_run_label is not None:
        config_overrides["evaluation_run_label"] = args.evaluation_run_label
    config.apply_overrides(**config_overrides)
    pipeline = LegacyPipeline(config=config)
    output_path = (
        Path(args.output)
        if args.output is not None
        else Path(args.output_dir)
    )

    try:
        pipeline.process(
            input_paths=input_paths,
            output_path=output_path,
            session_id=args.session_id,
        )
    except IngestionError as error:
        parser.exit(status=1, message=f"Error: {error}\n")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the lecture analyzer CLI."""

    load_dotenv_file()
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    if should_use_root_pipeline(args):
        return run_root_pipeline(args=args, parser=parser)
    return run_smoke_pipeline(args=args, parser=parser)
