"""Tests for pipeline stage timing collection and serialization."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import (
    AudioSource,
    InputSource,
    LectureSession,
    MergedTranscript,
    MergedTranscriptUnit,
    PipelineStageTiming,
    PipelineTiming,
    Segment,
    TranscriptChunk,
)
from lecture_analyzer.core.pipeline import LectureProcessingPipeline
from lecture_analyzer.core.types import MediaType


class PipelineTimingTests(unittest.TestCase):
    """Exercise additive timing collection for the main pipeline stages."""

    def test_session_to_dict_includes_pipeline_timing_section(self) -> None:
        """Serialized sessions should expose timing stages and summary data."""

        pipeline_timing = PipelineTiming(
            stages=[
                PipelineStageTiming(
                    stage_name="audio_normalization",
                    status="executed",
                    started_at="2026-04-15T10:00:00.000Z",
                    finished_at="2026-04-15T10:00:01.250Z",
                    duration_seconds=1.25,
                ),
                PipelineStageTiming(
                    stage_name="total_pipeline_execution",
                    status="completed",
                    started_at="2026-04-15T10:00:00.000Z",
                    finished_at="2026-04-15T10:00:03.000Z",
                    duration_seconds=3.0,
                ),
            ],
        )
        session = LectureSession(
            session_id="session_001",
            pipeline_timing=pipeline_timing,
        )

        payload = session.to_dict()

        self.assertIn("pipeline_timing", payload)
        self.assertEqual(
            payload["pipeline_timing"]["summary"]["total_duration_seconds"],
            3.0,
        )
        self.assertEqual(
            payload["pipeline_timing"]["summary"]["most_expensive_stage_name"],
            "audio_normalization",
        )
        self.assertEqual(
            payload["pipeline_timing"]["stages"][0]["stage_name"],
            "audio_normalization",
        )

    def test_process_tracks_stage_statuses_across_segmentation_modes(self) -> None:
        """A process run should expose upstream and per-mode stage timings."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            media_path = temp_path / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=temp_path / "artifacts",
                segmentation_mode="both",
                transcript_alignment_enabled=False,
                diarization_enabled=False,
                enable_qa_extraction=False,
            )
            pipeline = LectureProcessingPipeline(config)
            pipeline.session_loader = _FakeSessionLoader(media_path)
            pipeline.audio_normalizer = _FakeAudioNormalizer(media_path)
            pipeline.transcriber = _FakeTranscriber()
            pipeline.whisperx_aligner = _FakeAligner()
            pipeline.utterance_builder = _FakeUtteranceBuilder()
            pipeline.pyannote_diarizer = _FakeDiarizer()
            pipeline.speaker_attributor = _FakeSpeakerAttributor()
            pipeline.sentence_reconstructor = _FakeSentenceReconstructor()
            pipeline.transcript_merger = _FakeTranscriptMerger()
            pipeline.transcript_normalizer = _IdentityTranscriptNormalizer()
            pipeline.segmenter = _FakeSegmenter()
            pipeline.qa_extractor = _FakeQAExtractor()

            result = pipeline.process(media_path)

            self.assertIsInstance(result, dict)
            sessions_by_mode = result
            self.assertEqual(
                set(sessions_by_mode.keys()),
                {"structural", "windowed", "adaptive"},
            )

            for mode, session in sessions_by_mode.items():
                self.assertIsNotNone(session.pipeline_timing)
                timing_payload = session.to_dict()["pipeline_timing"]
                stages = timing_payload["stages"]
                stage_names = [stage["stage_name"] for stage in stages]
                stage_by_name = {stage["stage_name"]: stage for stage in stages}

                self.assertIn("session_loading", stage_names)
                self.assertIn("audio_normalization", stage_names)
                self.assertIn("transcription", stage_names)
                self.assertIn("alignment", stage_names)
                self.assertIn("utterance_building", stage_names)
                self.assertIn("diarization", stage_names)
                self.assertIn("speaker_attribution", stage_names)
                self.assertIn("sentence_reconstruction", stage_names)
                self.assertIn("transcript_post_processing", stage_names)
                self.assertIn("transcript_segmentation", stage_names)
                self.assertIn("qa_extraction", stage_names)
                self.assertIn("json_export", stage_names)
                self.assertIn("debug_excel_export", stage_names)
                self.assertIn("total_pipeline_execution", stage_names)

                self.assertEqual(stage_by_name["alignment"]["status"], "disabled")
                self.assertEqual(
                    stage_by_name["utterance_building"]["status"],
                    "skipped",
                )
                self.assertEqual(stage_by_name["diarization"]["status"], "disabled")
                self.assertEqual(
                    stage_by_name["speaker_attribution"]["status"],
                    "skipped",
                )
                self.assertEqual(
                    stage_by_name["sentence_reconstruction"]["status"],
                    "skipped",
                )
                self.assertEqual(stage_by_name["qa_extraction"]["status"], "disabled")
                self.assertEqual(stage_by_name["json_export"]["status"], "skipped")
                self.assertEqual(
                    stage_by_name["debug_excel_export"]["status"],
                    "skipped",
                )
                self.assertEqual(
                    stage_by_name["transcript_segmentation"]["metadata"][
                        "segmentation_mode"
                    ],
                    mode,
                )
                self.assertEqual(
                    timing_payload["summary"]["pipeline_execution_mode"],
                    "normal",
                )
                self.assertEqual(
                    timing_payload["summary"]["run_profile_label"],
                    "cold_run",
                )
                self.assertFalse(timing_payload["summary"]["any_cache_hit"])
                self.assertFalse(timing_payload["summary"]["any_artifact_reuse"])

                summary = timing_payload["summary"]
                self.assertGreater(summary["stage_count"], 0)
                self.assertGreaterEqual(summary["total_duration_seconds"], 0.0)
                self.assertEqual(summary["disabled_stage_count"], 3)
                self.assertEqual(summary["skipped_stage_count"], 5)
                self.assertTrue(session.metadata["pipeline_timing_available"])
                self.assertEqual(
                    session.metadata["pipeline_timing_total_duration_seconds"],
                    summary["total_duration_seconds"],
                )
                self.assertEqual(
                    session.metadata["pipeline_execution_mode"],
                    "normal",
                )

    def test_process_reports_reuse_in_normal_mode(self) -> None:
        """A normal run should expose cache and artifact reuse in the summary."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            media_path = temp_path / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=temp_path / "artifacts",
                segmentation_mode="structural",
            )
            pipeline = LectureProcessingPipeline(config)
            pipeline.session_loader = _FakeSessionLoader(media_path)
            pipeline.audio_normalizer = _FakeReusableAudioNormalizer(config, media_path)
            pipeline.transcriber = _FakeReusableTranscriber(config)
            pipeline.whisperx_aligner = _FakeReusableAligner(config)
            pipeline.utterance_builder = _FakeReusableUtteranceBuilder(config)
            pipeline.pyannote_diarizer = _FakeDiarizer()
            pipeline.speaker_attributor = _FakeSpeakerAttributor()
            pipeline.sentence_reconstructor = _FakeReusableSentenceReconstructor(config)
            pipeline.transcript_merger = _FakeTranscriptMerger()
            pipeline.transcript_normalizer = _IdentityTranscriptNormalizer()
            pipeline.segmenter = _FakeSegmenter()
            pipeline.qa_extractor = _FakeQAExtractor()

            session = pipeline.process(media_path)
            self.assertIsInstance(session, LectureSession)
            timing_payload = session.to_dict()["pipeline_timing"]
            summary = timing_payload["summary"]
            stage_by_name = {
                stage["stage_name"]: stage for stage in timing_payload["stages"]
            }

            self.assertEqual(summary["pipeline_execution_mode"], "normal")
            self.assertEqual(summary["run_profile_label"], "warm_run")
            self.assertTrue(summary["any_cache_hit"])
            self.assertTrue(summary["any_artifact_reuse"])
            self.assertGreaterEqual(summary["reused_cache_stage_count"], 1)
            self.assertGreaterEqual(summary["reused_artifact_stage_count"], 1)
            self.assertEqual(stage_by_name["transcription"]["status"], "reused_from_cache")
            self.assertEqual(
                stage_by_name["alignment"]["status"],
                "reused_from_artifact",
            )
            self.assertTrue(stage_by_name["sentence_reconstruction"]["used_existing_artifact"])

    def test_process_reports_forced_recompute_mode(self) -> None:
        """A from-scratch run should recompute stages and surface forced flags."""

        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            media_path = temp_path / "lesson.wav"
            media_path.write_bytes(b"placeholder")

            config = PipelineConfig(
                working_directory=temp_path / "artifacts",
                segmentation_mode="structural",
                force_recompute=True,
            )
            pipeline = LectureProcessingPipeline(config)
            pipeline.session_loader = _FakeSessionLoader(media_path)
            pipeline.audio_normalizer = _FakeReusableAudioNormalizer(config, media_path)
            pipeline.transcriber = _FakeReusableTranscriber(config)
            pipeline.whisperx_aligner = _FakeReusableAligner(config)
            pipeline.utterance_builder = _FakeReusableUtteranceBuilder(config)
            pipeline.pyannote_diarizer = _FakeDiarizer()
            pipeline.speaker_attributor = _FakeSpeakerAttributor()
            pipeline.sentence_reconstructor = _FakeReusableSentenceReconstructor(config)
            pipeline.transcript_merger = _FakeTranscriptMerger()
            pipeline.transcript_normalizer = _IdentityTranscriptNormalizer()
            pipeline.segmenter = _FakeSegmenter()
            pipeline.qa_extractor = _FakeQAExtractor()

            session = pipeline.process(media_path)
            self.assertIsInstance(session, LectureSession)
            timing_payload = session.to_dict()["pipeline_timing"]
            summary = timing_payload["summary"]
            stage_by_name = {
                stage["stage_name"]: stage for stage in timing_payload["stages"]
            }

            self.assertEqual(summary["pipeline_execution_mode"], "from_scratch")
            self.assertEqual(summary["run_profile_label"], "forced_recompute_run")
            self.assertTrue(summary["full_recompute_requested"])
            self.assertFalse(summary["any_cache_hit"])
            self.assertFalse(summary["any_artifact_reuse"])
            self.assertGreaterEqual(summary["forced_recompute_stage_count"], 4)
            self.assertEqual(stage_by_name["transcription"]["status"], "executed_forced")
            self.assertEqual(
                stage_by_name["alignment"]["status"],
                "executed_forced",
            )
            self.assertTrue(stage_by_name["alignment"]["forced_recompute"])
            self.assertEqual(
                session.metadata["pipeline_forced_recompute_stage_count"],
                summary["forced_recompute_stage_count"],
            )


class _FakeSessionLoader:
    """Return a deterministic in-memory session for timing tests."""

    def __init__(self, media_path: Path) -> None:
        self.media_path = media_path

    def load_session(
        self,
        input_paths: str | Path | list[str | Path],
        session_id: str | None = None,
    ) -> LectureSession:
        return LectureSession(
            session_id=session_id or "session_001",
            input_sources=[
                InputSource(
                    source_id="source_001",
                    original_path=self.media_path,
                    media_type=MediaType.AUDIO,
                    order_index=1,
                    original_filename=self.media_path.name,
                ),
            ],
            language_codes=["it"],
        )


class _FakeAudioNormalizer:
    """Produce one deterministic normalized audio source."""

    def __init__(self, media_path: Path) -> None:
        self.media_path = media_path

    def normalize_sources(self, input_sources: list[InputSource]) -> list[AudioSource]:
        return [
            AudioSource(
                audio_source_id="audio_source_001",
                input_source_id=input_sources[0].source_id,
                audio_path=self.media_path,
                audio_format="wav",
                order_index=1,
                duration_seconds=12.0,
                metadata={
                    "normalization": {
                        "artifact_found": False,
                        "artifact_reused": False,
                        "recomputed": True,
                        "forced_recompute": False,
                    },
                },
            ),
        ]


class _FakeTranscriber:
    """Populate one deterministic transcript chunk."""

    def transcribe_session(self, session: LectureSession) -> list[TranscriptChunk]:
        session.audio_sources[0].metadata["transcription"] = {
            "cache_hit": False,
            "cache_lookup_performed": True,
            "cache_artifact_found": False,
            "transcription_recomputed": True,
            "transcription_forced_recompute": False,
            "recomputed": True,
            "forced_recompute": False,
        }
        session.transcript_chunks = [
            TranscriptChunk(
                chunk_id="audio_source_001_chunk_0001",
                audio_source_id="audio_source_001",
                start_seconds=0.0,
                end_seconds=4.0,
                text="What is a matrix?",
                detected_language="it",
            ),
        ]
        session.transcript_text = "What is a matrix?"
        session.metadata["transcription_backend"] = "fake"
        return session.transcript_chunks


class _FakeAligner:
    """Expose the disabled-alignment branch without external dependencies."""

    def align_session(self, session: LectureSession) -> list[object]:
        session.aligned_transcripts = []
        session.metadata["transcript_alignment_enabled"] = False
        session.metadata["transcript_alignment_status"] = "disabled"
        session.metadata["transcript_alignment_word_count"] = 0
        session.metadata["transcript_alignment_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["alignment"] = {
                "status": "disabled",
                "reason": "alignment_disabled",
                "artifact_found": False,
                "artifact_reused": False,
                "recomputed": False,
                "forced_recompute": False,
            }
        return []


class _FakeUtteranceBuilder:
    """Expose the skipped utterance-building path."""

    def build_session(self, session: LectureSession) -> list[object]:
        session.utterances = []
        session.metadata["utterance_build_status"] = "skipped"
        session.metadata["utterance_build_reason"] = "aligned_transcripts_unavailable"
        session.metadata["utterance_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["utterances"] = {
                "status": "skipped",
                "artifact_found": False,
                "artifact_reused": False,
                "recomputed": False,
                "forced_recompute": False,
            }
        return []


class _FakeDiarizer:
    """Expose the disabled diarization path."""

    def diarize_session(self, session: LectureSession) -> list[object]:
        session.diarization_segments = []
        session.metadata["diarization_enabled"] = False
        session.metadata["diarization_status"] = "disabled"
        session.metadata["diarization_failed_sources"] = []
        session.metadata["diarization_speaker_count"] = 0
        for audio_source in session.audio_sources:
            audio_source.metadata["diarization"] = {
                "status": "disabled",
                "reason": "diarization_disabled",
                "artifact_found": False,
                "artifact_reused": False,
                "recomputed": False,
                "forced_recompute": False,
            }
        return []


class _FakeSpeakerAttributor:
    """Expose skipped speaker attribution when diarization is unavailable."""

    def attribute_session(self, session: LectureSession) -> list[object]:
        session.metadata["speaker_attribution_status"] = "skipped"
        session.metadata["speaker_attribution_reason"] = "diarization_unavailable"
        session.metadata["speaker_attribution_assigned_count"] = 0
        session.metadata["speaker_attribution_unassigned_count"] = 0
        return session.utterances


class _FakeSentenceReconstructor:
    """Expose skipped sentence reconstruction when utterances are unavailable."""

    def reconstruct_session(self, session: LectureSession) -> list[object]:
        session.sentences = []
        session.metadata["sentence_reconstruction_status"] = "skipped"
        session.metadata["sentence_reconstruction_reason"] = "utterances_unavailable"
        session.metadata["sentence_reconstruction_fallback_source_count"] = 0
        session.metadata["sentence_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["sentences"] = {
                "status": "skipped",
                "artifact_found": False,
                "artifact_reused": False,
                "recomputed": False,
                "forced_recompute": False,
            }
        return session.sentences


class _FakeTranscriptMerger:
    """Build a minimal merged transcript for segmentation and QA timing."""

    def merge_session(self, session: LectureSession) -> MergedTranscript:
        return MergedTranscript(
            session_id=session.session_id,
            units=[
                MergedTranscriptUnit(
                    unit_id="unit_0001",
                    chunk_id="audio_source_001_chunk_0001",
                    chunk_occurrence=1,
                    audio_source_id="audio_source_001",
                    source_order_index=1,
                    input_source_id="source_001",
                    start_seconds=0.0,
                    end_seconds=4.0,
                    session_start_seconds=0.0,
                    session_end_seconds=4.0,
                    text="What is a matrix?",
                    detected_language="it",
                ),
            ],
            full_text="What is a matrix?",
            detected_languages=["it"],
        )


class _IdentityTranscriptNormalizer:
    """Leave the merged transcript unchanged."""

    def normalize(self, merged_transcript: MergedTranscript) -> MergedTranscript:
        return merged_transcript


class _FakeSegmenter:
    """Return one deterministic segment per requested mode."""

    def resolved_mode(self, mode: str | None = None) -> str:
        if mode in {"structural", "windowed", "adaptive"}:
            return str(mode)
        return "structural"

    def segment_session(
        self,
        session: LectureSession,
        mode: str | None = None,
    ) -> list[Segment]:
        resolved_mode = self.resolved_mode(mode)
        return [
            Segment(
                segment_id=f"{resolved_mode}_segment_0001",
                start_seconds=0.0,
                end_seconds=4.0,
                text=session.transcript_text,
                transcript_chunk_ids=["audio_source_001_chunk_0001"],
                merged_transcript_unit_ids=["unit_0001"],
                audio_source_ids=["audio_source_001"],
                metadata={"segmentation_mode": resolved_mode},
            ),
        ]


class _FakeQAExtractor:
    """Return no QA candidates so the timing test stays focused."""

    def extract(self, session: LectureSession) -> list[object]:
        return []


class _FakeReusableAudioNormalizer:
    """Produce audio sources that can either reuse or recompute artifacts."""

    def __init__(self, config: PipelineConfig, media_path: Path) -> None:
        self.config = config
        self.media_path = media_path

    def normalize_sources(self, input_sources: list[InputSource]) -> list[AudioSource]:
        reused = not self.config.force_recompute
        return [
            AudioSource(
                audio_source_id="audio_source_001",
                input_source_id=input_sources[0].source_id,
                audio_path=self.media_path,
                audio_format="wav",
                order_index=1,
                duration_seconds=12.0,
                metadata={
                    "normalization": {
                        "artifact_found": True,
                        "artifact_reused": reused,
                        "used_existing_artifact": reused,
                        "recomputed": not reused,
                        "forced_recompute": self.config.force_recompute,
                    },
                },
            ),
        ]


class _FakeReusableTranscriber:
    """Expose either cache reuse or recomputation for transcription."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def transcribe_session(self, session: LectureSession) -> list[TranscriptChunk]:
        reused = not self.config.force_recompute
        session.audio_sources[0].metadata["transcription"] = {
            "cache_hit": reused,
            "used_cache": reused,
            "cache_lookup_performed": not self.config.force_recompute,
            "cache_artifact_found": True,
            "cache_ignored_due_to_force_recompute": self.config.force_recompute,
            "transcription_recomputed": not reused,
            "transcription_forced_recompute": self.config.force_recompute,
            "recomputed": not reused,
            "forced_recompute": self.config.force_recompute,
        }
        session.transcript_chunks = [
            TranscriptChunk(
                chunk_id="audio_source_001_chunk_0001",
                audio_source_id="audio_source_001",
                start_seconds=0.0,
                end_seconds=4.0,
                text="What is a matrix?",
                detected_language="it",
            ),
        ]
        session.transcript_text = "What is a matrix?"
        session.metadata["transcription_backend"] = "fake"
        session.metadata["transcription_cache_enabled"] = True
        session.metadata["transcription_cache_lookup_performed"] = not self.config.force_recompute
        session.metadata["transcription_cache_hit"] = reused
        session.metadata["transcription_cache_hit_count"] = 1 if reused else 0
        session.metadata["transcription_recomputed"] = not reused
        session.metadata["transcription_forced_recompute"] = self.config.force_recompute
        return session.transcript_chunks


class _FakeReusableAligner:
    """Expose either artifact reuse or recomputation for alignment."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def align_session(self, session: LectureSession) -> list[object]:
        reused = not self.config.force_recompute
        session.aligned_transcripts = []
        session.metadata["transcript_alignment_enabled"] = True
        session.metadata["transcript_alignment_status"] = "available"
        session.metadata["transcript_alignment_word_count"] = 0
        session.metadata["transcript_alignment_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["alignment"] = {
                "status": "available",
                "artifact_found": True,
                "artifact_reused": reused,
                "used_existing_artifact": reused,
                "recomputed": not reused,
                "forced_recompute": self.config.force_recompute,
            }
        return []


class _FakeReusableUtteranceBuilder:
    """Expose either artifact reuse or recomputation for utterances."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def build_session(self, session: LectureSession) -> list[object]:
        reused = not self.config.force_recompute
        session.utterances = []
        session.metadata["utterance_build_status"] = "available"
        session.metadata["utterance_build_reason"] = None
        session.metadata["utterance_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["utterances"] = {
                "status": "available",
                "artifact_found": True,
                "artifact_reused": reused,
                "used_existing_artifact": reused,
                "recomputed": not reused,
                "forced_recompute": self.config.force_recompute,
            }
        return []


class _FakeReusableSentenceReconstructor:
    """Expose either artifact reuse or recomputation for sentences."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def reconstruct_session(self, session: LectureSession) -> list[object]:
        reused = not self.config.force_recompute
        session.sentences = []
        session.metadata["sentence_reconstruction_status"] = "available"
        session.metadata["sentence_reconstruction_reason"] = None
        session.metadata["sentence_reconstruction_fallback_source_count"] = 0
        session.metadata["sentence_failed_sources"] = []
        for audio_source in session.audio_sources:
            audio_source.metadata["sentences"] = {
                "status": "available",
                "artifact_found": True,
                "artifact_reused": reused,
                "used_existing_artifact": reused,
                "recomputed": not reused,
                "forced_recompute": self.config.force_recompute,
            }
        return []


if __name__ == "__main__":
    unittest.main()
