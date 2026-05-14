"""Summary import checks for the bridge-based src namespace surface."""

from __future__ import annotations

import importlib
import unittest


class SrcNamespaceImportTests(unittest.TestCase):
    """Ensure all bridge-phase top-level src namespaces are importable."""

    def test_all_main_src_namespaces_are_importable(self) -> None:
        """The bridge phase should expose each major package namespace."""

        module_names = (
            "lecture_analyzer.core",
            "lecture_analyzer.input",
            "lecture_analyzer.preprocessing",
            "lecture_analyzer.transcription",
            "lecture_analyzer.analysis",
            "lecture_analyzer.output",
        )

        imported_modules = [
            importlib.import_module(module_name) for module_name in module_names
        ]

        self.assertEqual(len(imported_modules), len(module_names))


if __name__ == "__main__":
    unittest.main()
