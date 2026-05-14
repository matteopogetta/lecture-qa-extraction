"""Compatibility checks for the src-based output migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.output.debug_excel_exporter import export_run_to_excel
from lecture_analyzer.output.json_exporter import JsonExporter
from lecture_analyzer.output.sentence_provenance_validation import (
    SentenceProvenanceValidation,
    scoped_utterance_key,
    validate_sentence_provenance,
)
from lecture_analyzer.output.writer import (
    ensure_output_directory,
    write_result_json,
)


class SrcOutputCompatibilityTests(unittest.TestCase):
    """Ensure the src output namespace exposes the intended implementations."""

    def test_root_exporter_symbols_are_available(self) -> None:
        """The real root exporters should resolve through the src namespace."""

        self.assertEqual(JsonExporter.__name__, "JsonExporter")
        self.assertEqual(export_run_to_excel.__name__, "export_run_to_excel")

    def test_provenance_validation_symbols_are_available(self) -> None:
        """The provenance validation helpers should resolve through src."""

        self.assertEqual(
            SentenceProvenanceValidation.__name__,
            "SentenceProvenanceValidation",
        )
        self.assertEqual(scoped_utterance_key.__name__, "scoped_utterance_key")
        self.assertEqual(
            validate_sentence_provenance.__name__,
            "validate_sentence_provenance",
        )

    def test_smoke_writer_remains_available(self) -> None:
        """The smoke-mode writer should remain available unchanged."""

        self.assertEqual(
            ensure_output_directory.__name__,
            "ensure_output_directory",
        )
        self.assertEqual(write_result_json.__name__, "write_result_json")


if __name__ == "__main__":
    unittest.main()
