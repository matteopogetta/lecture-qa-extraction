"""Compatibility checks for the src-based preprocessing migration bridges."""

from __future__ import annotations

import unittest

from lecture_analyzer.preprocessing.audio_extractor import AudioExtractor
from lecture_analyzer.preprocessing.audio_normalizer import (
    AudioNormalizer,
    ProbedAudioInfo,
)
from lecture_analyzer.preprocessing.normalized_audio_metadata_store import (
    NormalizedAudioMetadataStore,
)


class SrcPreprocessingCompatibilityTests(unittest.TestCase):
    """Ensure the src preprocessing namespace exposes the real root modules."""

    def test_audio_normalizer_symbols_are_available(self) -> None:
        """The root audio normalizer should resolve through the src namespace."""

        self.assertEqual(AudioNormalizer.__name__, "AudioNormalizer")
        self.assertEqual(ProbedAudioInfo.__name__, "ProbedAudioInfo")

    def test_metadata_store_symbols_are_available(self) -> None:
        """The metadata-store helpers should resolve through the src namespace."""

        self.assertEqual(
            NormalizedAudioMetadataStore.__name__,
            "NormalizedAudioMetadataStore",
        )

    def test_audio_extractor_wrapper_remains_available(self) -> None:
        """The backward-compatible audio extractor alias should stay available."""

        self.assertEqual(AudioExtractor.__name__, "AudioNormalizer")


if __name__ == "__main__":
    unittest.main()
