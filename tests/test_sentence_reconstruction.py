"""Tests for utterance-based sentence reconstruction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import warnings

from lecture_analyzer.analysis.sentence_provenance import validate_sentence_structure
from lecture_analyzer.analysis.sentence_reconstruction import SentenceReconstructor
from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AudioSource,
    LectureSession,
    Sentence,
    Utterance,
)


class _FakeSplitter:
    """Return deterministic split results keyed by rendered text."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def split(
        self,
        text: str,
        lang_code: str | None = None,
    ) -> list[str]:
        return self.mapping.get(text, [text])


class SentenceReconstructionTests(unittest.TestCase):
    """Exercise sentence reconstruction, persistence, and fallback behavior."""

    def test_install_splitter_warning_filter_ignores_only_known_backend_noise(
        self,
    ) -> None:
        """The wtpsplit filter should leave unrelated warnings visible."""

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            SentenceReconstructor._install_splitter_warning_filter()
            warnings.warn_explicit(
                "Torchaudio's I/O functions now support per-call backend dispatch. "
                "Importing backend implementation directly is no longer guaranteed "
                "to work.",
                UserWarning,
                filename="skops/io/_utils.py",
                lineno=25,
                module="skops.io._utils",
            )
            warnings.warn_explicit(
                "different splitter warning",
                UserWarning,
                filename="skops/io/_utils.py",
                lineno=99,
                module="skops.io._utils",
            )

        self.assertEqual(len(caught), 1)
        self.assertEqual(str(caught[0].message), "different splitter warning")

    def test_wtpsplit_backend_fails_when_splitter_is_unavailable(self) -> None:
        """The wtpsplit backend should not silently use rule fallback."""

        config = PipelineConfig(sentence_splitter_backend="wtpsplit")
        reconstructor = SentenceReconstructor(config)
        reconstructor._splitter_resolution_attempted = True
        reconstructor._resolved_splitter = None

        with self.assertRaisesRegex(RuntimeError, "wtpsplit sentence splitter"):
            reconstructor._split_text(text="Alpha continues. Beta follows.", language_code="en")

    def test_fallback_rules_backend_remains_explicitly_available(self) -> None:
        """Rule fallback remains available only when selected explicitly."""

        config = PipelineConfig(sentence_splitter_backend="fallback_rules")
        reconstructor = SentenceReconstructor(config)

        sentences, metadata = reconstructor._split_text(
            text="Alpha continues. Beta follows.",
            language_code="en",
        )

        self.assertEqual(sentences, ["Alpha continues.", "Beta follows."])
        self.assertEqual(metadata["splitter_backend"], "fallback_rules")
        self.assertEqual(metadata["fallback_reason"], "fallback_backend_forced")

    def test_build_source_reconstructs_sentences_from_utterances(self) -> None:
        """Speaker-consistent utterances should become traceable sentences."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Hello there how are you I am fine thanks": [
                            "Hello there how are you",
                            "I am fine thanks",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Hello there",
                    start_seconds=0.0,
                    end_seconds=0.5,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="how are you",
                    start_seconds=0.6,
                    end_seconds=1.0,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="I am fine",
                    start_seconds=1.1,
                    end_seconds=1.5,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=4,
                    text="thanks",
                    start_seconds=1.6,
                    end_seconds=1.9,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)
            session = LectureSession(
                session_id="session_001",
                audio_sources=[audio_source],
                utterances=utterances,
                sentences=sentence_collection.sentences,
            )

            self.assertEqual(len(sentence_collection.sentences), 2)
            self.assertEqual(
                sentence_collection.sentences[0].text,
                "Hello there how are you",
            )
            self.assertEqual(
                sentence_collection.sentences[0].source_utterance_ids,
                [
                    "audio_source_001_utterance_0001",
                    "audio_source_001_utterance_0002",
                ],
            )
            self.assertEqual(sentence_collection.sentences[0].speaker_id, "SPEAKER_00")
            self.assertEqual(
                sentence_collection.sentences[1].source_utterance_start_index,
                3,
            )
            self.assertEqual(
                sentence_collection.sentences[1].source_utterance_end_index,
                4,
            )
            self.assertEqual(
                sentence_collection.sentences[0].speaker_resolution_status,
                "stable",
            )
            self.assertEqual(
                sentence_collection.sentences[0].semantic_quality_label,
                "borderline",
            )
            self.assertEqual(session.to_dict()["transcript"]["sentence_count"], 2)
            self.assertEqual(len(session.to_dict()["sentences"]), 2)

    def test_build_source_respects_speaker_boundaries(self) -> None:
        """Utterances from different speakers should not be merged arbitrarily."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter({}),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="First speaker talks",
                    start_seconds=0.0,
                    end_seconds=0.8,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="Second speaker replies",
                    start_seconds=0.9,
                    end_seconds=1.7,
                    speaker_id="SPEAKER_01",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 2)
            self.assertEqual(
                [sentence.speaker_id for sentence in sentence_collection.sentences],
                ["SPEAKER_00", "SPEAKER_01"],
            )
            self.assertEqual(
                sentence_collection.metadata["group_count"],
                2,
            )

    def test_consolidation_adds_semantic_cleanup_features(self) -> None:
        """Sentence cleanup diagnostics should be compact and review-visible."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(config, splitter=_FakeSplitter({}))
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="because alpha",
                    start_seconds=0.0,
                    end_seconds=0.5,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)
            sentence = sentence_collection.sentences[0]
            cleanup = sentence.metadata["semantic_cleanup"]

            self.assertEqual(cleanup["schema_version"], "1.0")
            self.assertLess(cleanup["sentence_autonomy_score"], 0.45)
            self.assertLess(cleanup["boundary_confidence_score"], 0.45)
            self.assertIn("low_sentence_autonomy", sentence.sentence_review_flags)
            self.assertIn("low_boundary_confidence", sentence.sentence_review_flags)

    def test_consolidation_marks_unassigned_and_uncertain_sources(self) -> None:
        """Sentence consolidation should distinguish uncertain and unassigned evidence."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter({"Short piece continues": ["Short piece continues"]}),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Short piece",
                    start_seconds=0.0,
                    end_seconds=0.7,
                    speaker_id="SPEAKER_00",
                    speaker_is_uncertain=True,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="continues",
                    start_seconds=0.75,
                    end_seconds=1.2,
                    speaker_id=None,
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            self.assertEqual(
                sentence_collection.sentences[0].speaker_resolution_status,
                "unassigned",
            )
            self.assertEqual(
                sentence_collection.sentences[0].merge_safety_label,
                "risky",
            )
            self.assertEqual(
                sentence_collection.sentences[0].review_priority,
                "high",
            )
            self.assertIn(
                "unassigned_source",
                sentence_collection.sentences[0].sentence_review_flags,
            )

    def test_consolidation_keeps_dominant_speaker_with_uncertain_noise(self) -> None:
        """A dominant stable speaker should survive minor uncertain noise."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Main explanation brief overlap Main explanation closes": [
                            "Main explanation brief overlap Main explanation closes",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Main explanation",
                    start_seconds=0.0,
                    end_seconds=0.8,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="brief overlap",
                    start_seconds=0.85,
                    end_seconds=1.1,
                    speaker_id="SPEAKER_01",
                    speaker_is_uncertain=True,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="Main explanation closes",
                    start_seconds=1.12,
                    end_seconds=1.9,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            sentence = sentence_collection.sentences[0]
            self.assertEqual(sentence.speaker_id, "SPEAKER_00")
            self.assertEqual(sentence.speaker_resolution_status, "mostly_stable")
            self.assertNotEqual(sentence.speaker_resolution_status, "mixed")

    def test_consolidation_keeps_dominant_speaker_with_marginal_unassigned(self) -> None:
        """A dominant stable speaker should survive minor unassigned noise."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Stable opening stable middle trailing noise": [
                            "Stable opening stable middle trailing noise",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Stable opening",
                    start_seconds=0.0,
                    end_seconds=0.6,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="stable middle",
                    start_seconds=0.65,
                    end_seconds=1.2,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="trailing noise",
                    start_seconds=1.25,
                    end_seconds=1.5,
                    speaker_id=None,
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            sentence = sentence_collection.sentences[0]
            self.assertEqual(sentence.speaker_id, "SPEAKER_00")
            self.assertIn(
                sentence.speaker_resolution_status,
                {"mostly_stable", "uncertain"},
            )
            self.assertNotEqual(sentence.speaker_resolution_status, "unassigned")

    def test_sentence_speaker_resolution_keeps_stable_dominant_with_short_interruption(
        self,
    ) -> None:
        """A short interruption should not overturn a clearly dominant speaker."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                sentence_reconstruction_respect_speaker_boundaries=False,
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Main explanation continues ok Main explanation closes.": [
                            "Main explanation continues ok Main explanation closes.",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Main explanation continues",
                    start_seconds=0.0,
                    end_seconds=1.4,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="ok",
                    start_seconds=1.45,
                    end_seconds=1.6,
                    speaker_id="SPEAKER_01",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="Main explanation closes.",
                    start_seconds=1.65,
                    end_seconds=3.0,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            sentence = sentence_collection.sentences[0]
            self.assertEqual(sentence.speaker_id, "SPEAKER_00")
            self.assertEqual(sentence.speaker_resolution_status, "mostly_stable")
            self.assertEqual(sentence.speaker_assignment_method, "direct_weighted_majority")
            self.assertEqual(
                sentence.metadata["short_fragment_source_utterance_count"],
                1,
            )

    def test_sentence_speaker_resolution_assigns_uncertain_same_speaker_majority(
        self,
    ) -> None:
        """Mostly uncertain evidence from one speaker should still resolve."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Maybe this part still belongs here actually yes.": [
                            "Maybe this part still belongs here actually yes.",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Maybe this part",
                    start_seconds=0.0,
                    end_seconds=0.8,
                    speaker_id="SPEAKER_00",
                    speaker_is_uncertain=True,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="still belongs here",
                    start_seconds=0.82,
                    end_seconds=1.75,
                    speaker_id="SPEAKER_00",
                    speaker_is_uncertain=True,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="actually yes.",
                    start_seconds=1.8,
                    end_seconds=2.25,
                    speaker_id="SPEAKER_00",
                    speaker_is_uncertain=True,
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            sentence = sentence_collection.sentences[0]
            self.assertEqual(sentence.speaker_id, "SPEAKER_00")
            self.assertEqual(sentence.speaker_resolution_status, "uncertain")
            self.assertEqual(sentence.speaker_assignment_method, "direct_uncertain_majority")
            self.assertEqual(sentence.speaker_confidence_label, "low")

    def test_sentence_speaker_resolution_marks_real_conflict_as_mixed(self) -> None:
        """Comparable evidence for two speakers should stay mixed."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                sentence_reconstruction_respect_speaker_boundaries=False,
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Teacher explains the point student adds another long idea.": [
                            "Teacher explains the point student adds another long idea.",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Teacher explains the point",
                    start_seconds=0.0,
                    end_seconds=1.6,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="student adds another long idea.",
                    start_seconds=1.62,
                    end_seconds=3.3,
                    speaker_id="SPEAKER_01",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 1)
            sentence = sentence_collection.sentences[0]
            self.assertIsNone(sentence.speaker_id)
            self.assertEqual(sentence.speaker_resolution_status, "mixed")
            self.assertEqual(sentence.speaker_assignment_method, "mixed_conflict")

    def test_sentence_speaker_resolution_recovers_short_unassigned_fragment_from_context(
        self,
    ) -> None:
        """A short unassigned fragment between stable neighbors can be recovered."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
                sentence_reconstruction_respect_speaker_boundaries=False,
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "Stable intro. Tiny bridge. Stable close.": [
                            "Stable intro.",
                            "Tiny bridge.",
                            "Stable close.",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="Stable intro.",
                    start_seconds=0.0,
                    end_seconds=0.9,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="Tiny",
                    start_seconds=0.95,
                    end_seconds=1.05,
                    speaker_id=None,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="bridge.",
                    start_seconds=1.06,
                    end_seconds=1.25,
                    speaker_id=None,
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=4,
                    text="Stable close.",
                    start_seconds=1.3,
                    end_seconds=2.1,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 3)
            sentence = sentence_collection.sentences[1]
            self.assertEqual(sentence.speaker_id, "SPEAKER_00")
            self.assertEqual(sentence.speaker_resolution_status, "uncertain")
            self.assertEqual(
                sentence.speaker_assignment_method,
                "recovered_from_neighbor_consensus",
            )

    def test_sentence_speaker_resolution_does_not_force_long_unassigned_sentence_from_context(
        self,
    ) -> None:
        """Long weak sentences should stay unassigned even with uniform neighbors."""

        with tempfile.TemporaryDirectory() as temp_directory:
            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter({}),
            )
            sentences = [
                Sentence(
                    sentence_id="audio_source_001_sentence_0001",
                    audio_source_id="audio_source_001",
                    text="Stable intro.",
                    start_seconds=0.0,
                    end_seconds=0.9,
                    speaker_id="SPEAKER_00",
                    speaker_resolution_status="stable",
                    speaker_assignment_method="direct_stable_majority",
                    metadata={"word_count": 2, "duration_seconds": 0.9},
                ),
                Sentence(
                    sentence_id="audio_source_001_sentence_0002",
                    audio_source_id="audio_source_001",
                    text="This section has many words and stays unresolved for quite a while.",
                    start_seconds=0.95,
                    end_seconds=4.3,
                    speaker_id=None,
                    speaker_resolution_status="unassigned",
                    speaker_assignment_method="insufficient_evidence",
                    metadata={
                        "word_count": 11,
                        "duration_seconds": 3.35,
                        "source_utterance_count": 1,
                        "speaker_evidence": {
                            "assigned_count": 0,
                            "uncertain_count": 0,
                            "unassigned_count": 1,
                            "short_fragment_count": 0,
                            "dominant_share": 0.0,
                            "second_share": 0.0,
                            "dominance_margin": 0.0,
                            "noise_score": 0.8,
                            "candidates": [],
                        },
                    },
                ),
                Sentence(
                    sentence_id="audio_source_001_sentence_0003",
                    audio_source_id="audio_source_001",
                    text="Stable close.",
                    start_seconds=4.35,
                    end_seconds=5.1,
                    speaker_id="SPEAKER_00",
                    speaker_resolution_status="stable",
                    speaker_assignment_method="direct_stable_majority",
                    metadata={"word_count": 2, "duration_seconds": 0.75},
                ),
            ]

            reconstructor._recover_unassigned_sentence_speakers(sentences)

            sentence = sentences[1]
            self.assertIsNone(sentence.speaker_id)
            self.assertEqual(sentence.speaker_resolution_status, "unassigned")
            self.assertEqual(sentence.speaker_assignment_method, "insufficient_evidence")

    def test_sentence_provenance_is_unique_and_isolated(self) -> None:
        """Sentence provenance lists and metadata should stay isolated per sentence."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "First statement. Second statement. Third statement.": [
                            "First statement.",
                            "Second statement. Third statement.",
                        ],
                    },
                ),
            )
            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="First statement.",
                    start_seconds=0.0,
                    end_seconds=0.5,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="Second statement.",
                    start_seconds=0.6,
                    end_seconds=1.0,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=3,
                    text="Third statement.",
                    start_seconds=1.05,
                    end_seconds=1.5,
                    speaker_id="SPEAKER_00",
                ),
            ]

            sentence_collection = reconstructor.build_source(audio_source, utterances)

            self.assertEqual(len(sentence_collection.sentences), 2)
            sentence_a, sentence_b = sentence_collection.sentences
            flattened_utterance_ids = [
                utterance_id
                for sentence in sentence_collection.sentences
                for utterance_id in sentence.source_utterance_ids
            ]
            self.assertEqual(len(flattened_utterance_ids), len(set(flattened_utterance_ids)))
            self.assertIsNot(
                sentence_a.source_utterance_ids,
                sentence_b.source_utterance_ids,
            )
            self.assertIsNot(sentence_a.metadata, sentence_b.metadata)

            sentence_a.source_utterance_ids.append("synthetic_utterance")
            sentence_a.metadata["speaker_evidence"]["assigned_speakers"].append("SYNTHETIC")

            self.assertNotIn("synthetic_utterance", sentence_b.source_utterance_ids)
            self.assertNotIn(
                "SYNTHETIC",
                sentence_b.metadata["speaker_evidence"]["assigned_speakers"],
            )
            self.assertEqual(
                sentence_collection.metadata["shared_source_utterance_count"],
                0,
            )
            self.assertEqual(
                sentence_collection.metadata["sentence_assignment_total"],
                3,
            )
            self.assertFalse(
                sentence_collection.metadata["all_sentences_have_provenance_overlap"],
            )

    def test_cached_sentences_keep_group_local_provenance_after_reload(self) -> None:
        """Persisted sentence indices must remain global to the source stream."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter(
                    {
                        "First group.": ["First group."],
                        "Second group.": ["Second group."],
                    },
                ),
            )

            audio_source = self._build_audio_source(media_path)
            utterances = [
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=1,
                    text="First group.",
                    start_seconds=0.0,
                    end_seconds=0.5,
                    speaker_id="SPEAKER_00",
                ),
                self._build_utterance(
                    audio_source_id=audio_source.audio_source_id,
                    utterance_index=2,
                    text="Second group.",
                    start_seconds=4.0,
                    end_seconds=4.5,
                    speaker_id="SPEAKER_00",
                ),
            ]

            first_collection = reconstructor.build_source(audio_source, utterances)
            reconstructor.cache_store.save_sentences(
                audio_source=audio_source,
                utterances=utterances,
                sentence_collection=first_collection,
            )

            cached_sentences = reconstructor.cache_store.load_sentences(
                audio_source=audio_source,
                utterances=utterances,
            )

            self.assertIsNotNone(cached_sentences)
            assert cached_sentences is not None
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].source_utterance_ids,
                ["audio_source_001_utterance_0001"],
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[1].source_utterance_ids,
                ["audio_source_001_utterance_0002"],
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].source_utterance_start_index,
                1,
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[1].source_utterance_start_index,
                2,
            )

    def test_sentence_structure_validation_reports_unique_mapping(self) -> None:
        """Independent sentences should have one-to-one utterance assignments."""

        utterances = [
            self._build_utterance(
                audio_source_id="audio_source_001",
                utterance_index=1,
                text="Alpha",
                start_seconds=0.0,
                end_seconds=0.5,
                speaker_id="SPEAKER_00",
            ),
            self._build_utterance(
                audio_source_id="audio_source_001",
                utterance_index=2,
                text="Beta",
                start_seconds=0.6,
                end_seconds=1.0,
                speaker_id="SPEAKER_00",
            ),
        ]
        sentences = [
            Sentence(
                sentence_id="audio_source_001_sentence_0001",
                audio_source_id="audio_source_001",
                text="Alpha",
                start_seconds=0.0,
                end_seconds=0.5,
                source_utterance_ids=["audio_source_001_utterance_0001"],
            ),
            Sentence(
                sentence_id="audio_source_001_sentence_0002",
                audio_source_id="audio_source_001",
                text="Beta",
                start_seconds=0.6,
                end_seconds=1.0,
                source_utterance_ids=["audio_source_001_utterance_0002"],
            ),
        ]

        validation = validate_sentence_structure(
            utterances=utterances,
            sentences=sentences,
        )

        self.assertEqual(validation.utterances_assigned_to_multiple_sentences, 0)
        self.assertEqual(validation.sentences_with_provenance_overlap_count, 0)
        self.assertEqual(validation.max_sentence_reuse_per_utterance, 1)
        self.assertFalse(validation.all_sentences_have_provenance_overlap)

    def test_sentence_structure_validation_detects_real_overlap_without_self_overlap(self) -> None:
        """Real overlaps should be flagged, but a sentence must not overlap with itself."""

        utterances = [
            self._build_utterance(
                audio_source_id="audio_source_001",
                utterance_index=1,
                text="Shared",
                start_seconds=0.0,
                end_seconds=0.5,
                speaker_id="SPEAKER_00",
            ),
        ]
        sentences = [
            Sentence(
                sentence_id="audio_source_001_sentence_0001",
                audio_source_id="audio_source_001",
                text="Shared A",
                start_seconds=0.0,
                end_seconds=0.5,
                source_utterance_ids=["audio_source_001_utterance_0001"],
            ),
            Sentence(
                sentence_id="audio_source_001_sentence_0002",
                audio_source_id="audio_source_001",
                text="Shared B",
                start_seconds=0.0,
                end_seconds=0.5,
                source_utterance_ids=["audio_source_001_utterance_0001"],
            ),
        ]

        validation = validate_sentence_structure(
            utterances=utterances,
            sentences=sentences,
        )

        self.assertEqual(validation.utterances_assigned_to_multiple_sentences, 1)
        self.assertEqual(validation.sentences_with_provenance_overlap_count, 2)
        self.assertEqual(validation.max_sentence_reuse_per_utterance, 2)
        self.assertIn(
            "audio_source_001_sentence_0002",
            validation.sentence_to_overlap_sentence_ids[
                "audio_source_001_sentence_0001"
            ],
        )
        self.assertNotIn(
            "audio_source_001_sentence_0001",
            validation.sentence_to_overlap_sentence_ids[
                "audio_source_001_sentence_0001"
            ],
        )

    def test_sentence_structure_validation_flags_pathological_global_overlap(self) -> None:
        """A simple dataset where every sentence overlaps should trigger the guardrail."""

        utterances = [
            self._build_utterance(
                audio_source_id="audio_source_001",
                utterance_index=1,
                text="Shared",
                start_seconds=0.0,
                end_seconds=0.5,
                speaker_id="SPEAKER_00",
            ),
        ]
        sentences = [
            Sentence(
                sentence_id=f"audio_source_001_sentence_{index:04d}",
                audio_source_id="audio_source_001",
                text=f"Shared {index}",
                start_seconds=0.0,
                end_seconds=0.5,
                source_utterance_ids=["audio_source_001_utterance_0001"],
            )
            for index in range(1, 4)
        ]

        validation = validate_sentence_structure(
            utterances=utterances,
            sentences=sentences,
        )

        self.assertTrue(validation.all_sentences_have_provenance_overlap)
        self.assertEqual(validation.max_sentence_reuse_per_utterance, 3)
        self.assertGreater(validation.provenance_anomaly_count, 0)

    def test_cached_sentences_rebind_current_source_ids(self) -> None:
        """A cached sentence artifact should remain reusable across source ids."""

        with tempfile.TemporaryDirectory() as temp_directory:
            media_path = Path(temp_directory) / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=Path(temp_directory) / "artifacts",
            )
            reconstructor = SentenceReconstructor(
                config,
                splitter=_FakeSplitter({"Reusable sentence": ["Reusable sentence"]}),
            )

            first_source = self._build_audio_source(media_path)
            first_utterances = [
                self._build_utterance(
                    audio_source_id=first_source.audio_source_id,
                    utterance_index=1,
                    text="Reusable sentence",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    speaker_id="SPEAKER_00",
                ),
            ]
            first_collection = reconstructor.build_source(first_source, first_utterances)
            reconstructor.cache_store.save_sentences(
                audio_source=first_source,
                utterances=first_utterances,
                sentence_collection=first_collection,
            )

            second_source = self._build_audio_source(
                media_path,
                audio_source_id="audio_source_099",
                session_offset_seconds=5.0,
            )
            second_utterances = [
                self._build_utterance(
                    audio_source_id=second_source.audio_source_id,
                    utterance_index=1,
                    text="Reusable sentence",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    speaker_id="SPEAKER_00",
                    session_offset_seconds=5.0,
                ),
            ]

            cached_sentences = reconstructor.cache_store.load_sentences(
                audio_source=second_source,
                utterances=second_utterances,
            )

            self.assertIsNotNone(cached_sentences)
            assert cached_sentences is not None
            self.assertEqual(
                cached_sentences.sentence_collection.audio_source_id,
                "audio_source_099",
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].sentence_id,
                "audio_source_099_sentence_0001",
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].source_utterance_ids,
                ["audio_source_099_utterance_0001"],
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].session_start_seconds,
                5.0,
            )
            self.assertEqual(
                cached_sentences.sentence_collection.sentences[0].session_end_seconds,
                6.0,
            )

    @staticmethod
    def _build_audio_source(
        media_path: Path,
        audio_source_id: str = "audio_source_001",
        session_offset_seconds: float | None = None,
    ) -> AudioSource:
        """Build an audio source linked to one original media file."""

        return AudioSource(
            audio_source_id=audio_source_id,
            input_source_id="source_001",
            audio_path=media_path,
            audio_format=media_path.suffix.lstrip("."),
            duration_seconds=4.0,
            order_index=1,
            session_offset_seconds=session_offset_seconds,
            metadata={"original_path": str(media_path)},
        )

    @staticmethod
    def _build_utterance(
        *,
        audio_source_id: str,
        utterance_index: int,
        text: str,
        start_seconds: float,
        end_seconds: float,
        speaker_id: str | None,
        session_offset_seconds: float | None = None,
        speaker_is_uncertain: bool = False,
    ) -> Utterance:
        """Build a minimal utterance for sentence reconstruction tests."""

        return Utterance(
            utterance_id=f"{audio_source_id}_utterance_{utterance_index:04d}",
            audio_source_id=audio_source_id,
            text=text,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            aligned_segment_id=f"{audio_source_id}_aligned_segment_0001",
            aligned_segment_index=1,
            transcript_chunk_id=f"{audio_source_id}_chunk_0001",
            start_word_index=utterance_index,
            end_word_index=utterance_index,
            source_word_ids=[
                f"{audio_source_id}_aligned_word_0001_{utterance_index:04d}",
            ],
            detected_language="en",
            speaker_id=speaker_id,
            speaker_is_uncertain=speaker_is_uncertain,
            session_start_seconds=(
                start_seconds + session_offset_seconds
                if session_offset_seconds is not None
                else None
            ),
            session_end_seconds=(
                end_seconds + session_offset_seconds
                if session_offset_seconds is not None
                else None
            ),
        )


if __name__ == "__main__":
    unittest.main()
