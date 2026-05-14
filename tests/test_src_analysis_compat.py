"""Compatibility checks for the src-based analysis migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.analysis.audio_quality import (
    AudioQualityAnalyzer,
    AudioQualityAssessment,
)
from lecture_analyzer.analysis.qa_extractor import (
    QAPairExtractor,
    QuestionCandidate,
)
from lecture_analyzer.analysis.qa_rules import CueRule, collect_rule_matches
from lecture_analyzer.analysis.segmenter import TranscriptSegmenter
from lecture_analyzer.analysis.semantic_reranking import (
    SemanticRerankScore,
    TransformersBGERerankerBackend,
)
from lecture_analyzer.analysis.semantic_retrieval import (
    SemanticSearchHit,
    SentenceTransformersE5Backend,
)
from lecture_analyzer.analysis.sentence_provenance import (
    SentenceStructureValidation,
    validate_sentence_structure,
)
from lecture_analyzer.analysis.sentence_reconstruction import SentenceReconstructor
from lecture_analyzer.analysis.speaker_attribution import SpeakerAttributor
from lecture_analyzer.analysis.speaker_role import SpeakerRoleEstimator
from lecture_analyzer.analysis.speaker_stability import (
    SpeakerAttributionDecision,
    SpeakerStabilitySmoother,
)
from lecture_analyzer.analysis.utterance_builder import UtteranceBuilder


class SrcAnalysisCompatibilityTests(unittest.TestCase):
    """Ensure the src analysis namespace exposes root implementations."""

    def test_qa_and_segmentation_symbols_are_available(self) -> None:
        """The QA extractor, rules, and segmenter should resolve through src."""

        self.assertEqual(QAPairExtractor.__name__, "QAPairExtractor")
        self.assertEqual(QuestionCandidate.__name__, "QuestionCandidate")
        self.assertEqual(CueRule.__name__, "CueRule")
        self.assertEqual(collect_rule_matches.__name__, "collect_rule_matches")
        self.assertEqual(TranscriptSegmenter.__name__, "TranscriptSegmenter")

    def test_sentence_and_utterance_symbols_are_available(self) -> None:
        """Sentence and utterance processing modules should resolve through src."""

        self.assertEqual(
            SentenceReconstructor.__name__,
            "SentenceReconstructor",
        )
        self.assertEqual(UtteranceBuilder.__name__, "UtteranceBuilder")
        self.assertEqual(
            SentenceStructureValidation.__name__,
            "SentenceStructureValidation",
        )
        self.assertEqual(
            validate_sentence_structure.__name__,
            "validate_sentence_structure",
        )

    def test_speaker_related_symbols_are_available(self) -> None:
        """Speaker-oriented analysis helpers should resolve through src."""

        self.assertEqual(SpeakerAttributor.__name__, "SpeakerAttributor")
        self.assertEqual(SpeakerRoleEstimator.__name__, "SpeakerRoleEstimator")
        self.assertEqual(
            SpeakerAttributionDecision.__name__,
            "SpeakerAttributionDecision",
        )
        self.assertEqual(
            SpeakerStabilitySmoother.__name__,
            "SpeakerStabilitySmoother",
        )

    def test_optional_semantic_symbols_are_available(self) -> None:
        """Optional semantic modules should resolve without eager ML imports."""

        self.assertEqual(SemanticSearchHit.__name__, "SemanticSearchHit")
        self.assertEqual(
            SentenceTransformersE5Backend.__name__,
            "SentenceTransformersE5Backend",
        )
        self.assertEqual(SemanticRerankScore.__name__, "SemanticRerankScore")
        self.assertEqual(
            TransformersBGERerankerBackend.__name__,
            "TransformersBGERerankerBackend",
        )

    def test_audio_quality_symbols_are_available(self) -> None:
        """Audio quality helpers should resolve through the src namespace."""

        self.assertEqual(AudioQualityAnalyzer.__name__, "AudioQualityAnalyzer")
        self.assertEqual(
            AudioQualityAssessment.__name__,
            "AudioQualityAssessment",
        )


if __name__ == "__main__":
    unittest.main()
