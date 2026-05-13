"""Compatibility checks for the src-based input migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.input.session_loader import SessionLoader
from lecture_analyzer.input.validation import validate_input_path


class SrcInputCompatibilityTests(unittest.TestCase):
    """Ensure the src namespace exposes both smoke and root input utilities."""

    def test_root_session_loader_is_available_via_src_namespace(self) -> None:
        """The real session loader should resolve through lecture_analyzer.input."""

        self.assertEqual(SessionLoader.__name__, "SessionLoader")

    def test_smoke_validation_utility_remains_available(self) -> None:
        """The smoke-mode input validator should remain available."""

        self.assertEqual(validate_input_path.__name__, "validate_input_path")


if __name__ == "__main__":
    unittest.main()
