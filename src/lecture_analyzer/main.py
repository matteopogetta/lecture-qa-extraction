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
