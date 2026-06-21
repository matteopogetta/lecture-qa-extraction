"""Tests for pipeline configuration profiles."""

from __future__ import annotations

import unittest

from lecture_analyzer.core.config import PipelineConfig


class PipelineConfigProfileTests(unittest.TestCase):
    """Exercise named pipeline profiles without changing the default behavior."""

    def test_current_profile_preserves_existing_defaults(self) -> None:
        """The default profile should keep the previous active pipeline shape."""

        config = PipelineConfig()

        self.assertEqual(config.pipeline_profile, "current")
        self.assertTrue(config.transcript_alignment_enabled)
        self.assertFalse(config.diarization_enabled)
        self.assertEqual(config.segmentation_mode, "structural")
        self.assertEqual(config.qa_answer_search_strategy, "semantic_retrieval")
        self.assertTrue(config.qa_semantic_retrieval_enabled)
        self.assertEqual(config.qa_answer_ranking_strategy, "semantic_reranker")
        self.assertTrue(config.qa_semantic_reranking_enabled)
        self.assertTrue(config.export_debug_excel)

    def test_light_profile_disables_heavy_optional_branches(self) -> None:
        """The light profile should favor fast local runs and CI checks."""

        config = PipelineConfig(pipeline_profile="light")

        self.assertEqual(config.pipeline_profile, "light")
        self.assertFalse(config.transcript_alignment_enabled)
        self.assertFalse(config.diarization_enabled)
        self.assertEqual(config.qa_answer_search_strategy, "local_rule_based")
        self.assertFalse(config.qa_semantic_retrieval_enabled)
        self.assertEqual(config.qa_answer_ranking_strategy, "rule_based")
        self.assertFalse(config.qa_semantic_reranking_enabled)
        self.assertFalse(config.export_debug_excel)

    def test_diagnostic_profile_runs_comparison_and_debug_outputs(self) -> None:
        """The diagnostic profile should enable the richer comparison surface."""

        config = PipelineConfig(pipeline_profile="diagnostic")

        self.assertEqual(config.pipeline_profile, "diagnostic")
        self.assertTrue(config.transcript_alignment_enabled)
        self.assertTrue(config.diarization_enabled)
        self.assertEqual(config.segmentation_mode, "both")
        self.assertTrue(config.export_debug_excel)

    def test_apply_overrides_keeps_explicit_choices_after_profile_defaults(self) -> None:
        """Explicit runtime settings should win over the selected profile."""

        config = PipelineConfig(pipeline_profile="diagnostic")

        config.apply_overrides(
            transcript_alignment_enabled=False,
            diarization_enabled=False,
            segmentation_mode="adaptive",
            export_debug_excel=False,
        )

        self.assertEqual(config.pipeline_profile, "diagnostic")
        self.assertFalse(config.transcript_alignment_enabled)
        self.assertFalse(config.diarization_enabled)
        self.assertEqual(config.segmentation_mode, "adaptive")
        self.assertFalse(config.export_debug_excel)

    def test_unknown_profile_is_rejected(self) -> None:
        """Unknown profile labels should fail early instead of silently drifting."""

        with self.assertRaises(ValueError):
            PipelineConfig(pipeline_profile="experimental")


if __name__ == "__main__":
    unittest.main()
