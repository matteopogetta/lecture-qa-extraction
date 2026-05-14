"""Tests for CLI routing compatibility."""

from __future__ import annotations

from argparse import Namespace

from lecture_analyzer.main import build_parser, main, should_use_root_pipeline


def test_root_pipeline_flags_are_recognized() -> None:
    """The parser should accept the official src-pipeline flags."""

    parser = build_parser()

    args = parser.parse_args(
        [
            "lesson.mp4",
            "--segmentation-mode",
            "adaptive",
            "--enable-diarization",
            "--transcription-compute-type",
            "float32",
        ],
    )

    assert args.inputs == ["lesson.mp4"]
    assert args.segmentation_mode == "adaptive"
    assert args.enable_diarization is True
    assert args.transcription_compute_type == "float32"


def test_main_routes_root_arguments_to_root_pipeline(monkeypatch) -> None:
    """Official pipeline arguments should trigger the main execution path."""

    captured: dict[str, Namespace] = {}

    def fake_run_root_pipeline(args: Namespace, parser) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(
        "lecture_analyzer.main.run_root_pipeline",
        fake_run_root_pipeline,
    )

    exit_code = main(
        [
            "lesson.mp4",
            "--segmentation-mode",
            "adaptive",
            "--enable-diarization",
            "--transcription-compute-type",
            "float32",
        ],
    )

    assert exit_code == 0
    assert captured["args"].enable_diarization is True
    assert captured["args"].segmentation_mode == "adaptive"


def test_smoke_mode_remains_available(monkeypatch) -> None:
    """The explicit smoke flag should keep the diagnostic flow reachable."""

    captured: dict[str, Namespace] = {}

    def fake_run_smoke_pipeline(args: Namespace, parser) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(
        "lecture_analyzer.main.run_smoke_pipeline",
        fake_run_smoke_pipeline,
    )

    exit_code = main(
        [
            "--smoke",
            "--input",
            "sample_data/example.mp4",
        ],
    )

    assert exit_code == 0
    assert captured["args"].smoke is True
    assert captured["args"].input_path == "sample_data/example.mp4"


def test_smoke_flag_disables_root_routing() -> None:
    """The explicit smoke flag should override official-pipeline auto-routing."""

    parser = build_parser()
    args = parser.parse_args(["--smoke", "--input", "lesson.mp4"])

    assert should_use_root_pipeline(args) is False
