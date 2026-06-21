"""Tests for the command-line interface entry point."""

from __future__ import annotations

import unittest

from lecture_analyzer.main import should_use_root_pipeline
from main import build_parser


class MainCliTests(unittest.TestCase):
    """Exercise the CLI flags exposed by the entry point."""

    def test_help_text_describes_official_src_pipeline(self) -> None:
        """The parser help should present the src pipeline as the main path."""

        parser = build_parser()
        help_text = parser.format_help()

        self.assertIn("Official CLI", help_text)
        self.assertIn("src-based", help_text)
        self.assertIn("compatibility wrapper", help_text)
        self.assertIn("--smoke", help_text)
        self.assertNotIn("bootstrap placeholder flow or the legacy", help_text)

    def test_from_scratch_flag_is_parsed(self) -> None:
        """The CLI should expose a dedicated flag for full recomputation."""

        parser = build_parser()

        args = parser.parse_args(["lesson.mp4", "--from-scratch"])

        self.assertEqual(args.inputs, ["lesson.mp4"])
        self.assertTrue(args.from_scratch)

    def test_transcription_compute_type_flag_is_parsed(self) -> None:
        """The CLI should allow explicit faster-whisper compute-type selection."""

        parser = build_parser()

        args = parser.parse_args(
            [
                "lesson.mp4",
                "--transcription-compute-type",
                "float32",
            ],
        )

        self.assertEqual(args.inputs, ["lesson.mp4"])
        self.assertEqual(args.transcription_compute_type, "float32")

    def test_smoke_flag_is_parsed(self) -> None:
        """The CLI should expose an explicit smoke-mode flag."""

        parser = build_parser()

        args = parser.parse_args(["--smoke", "--input", "lesson.mp4"])

        self.assertTrue(args.smoke)
        self.assertEqual(args.input_path, "lesson.mp4")

    def test_pipeline_profile_flag_is_parsed(self) -> None:
        """The CLI should expose opt-in execution profiles."""

        parser = build_parser()

        args = parser.parse_args(["--pipeline-profile", "light", "lesson.mp4"])

        self.assertEqual(args.pipeline_profile, "light")
        self.assertEqual(args.inputs, ["lesson.mp4"])

    def test_pipeline_profile_selects_real_pipeline(self) -> None:
        """A non-default profile should route to the real pipeline path."""

        parser = build_parser()

        args = parser.parse_args(
            ["--pipeline-profile", "light", "--input", "lesson.mp4"],
        )

        self.assertTrue(should_use_root_pipeline(args))


if __name__ == "__main__":
    unittest.main()
