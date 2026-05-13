"""Tests for the command-line interface entry point."""

from __future__ import annotations

import unittest

from main import build_parser


class MainCliTests(unittest.TestCase):
    """Exercise the CLI flags exposed by the entry point."""

    def test_help_text_describes_official_root_pipeline(self) -> None:
        """The parser help should present the root pipeline as the main path."""

        parser = build_parser()
        help_text = parser.format_help()

        self.assertIn("Official CLI", help_text)
        self.assertIn("root-based", help_text)
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


if __name__ == "__main__":
    unittest.main()
