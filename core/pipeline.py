"""Pipeline orchestration for lecture ingestion, transcription, and export."""

from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
from time import monotonic
from typing import Any, Sequence

from analysis.qa_extractor import QAPairExtractor
from analysis.sentence_reconstruction import SentenceReconstructor
from analysis.segmenter import TranscriptSegmenter
from analysis.speaker_attribution import SpeakerAttributor
from analysis.utterance_builder import UtteranceBuilder
from core.config import PipelineConfig
from core.models import LectureSession
from core.timing import PipelineTimer, utc_now_iso
from core.types import ProcessingStatus
from input.session_loader import SessionLoader
from output.debug_excel_exporter import export_run_to_excel
from output.json_exporter import JsonExporter
from preprocessing.audio_normalizer import AudioNormalizer
from transcription.pyannote_diarizer import PyannoteDiarizer
from transcription.transcript_merger import TranscriptMerger
from transcription.transcript_normalizer import TranscriptNormalizer
from transcription.transcriber import Transcriber
from transcription.whisperx_aligner import WhisperXAligner


LOGGER = logging.getLogger(__name__)


class LectureProcessingPipeline:
    """Coordinate ingestion, transcript preparation, segmentation, and export."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        # Instantiate long-lived collaborators once so every stage shares the
        # same normalized configuration and path conventions.
        self.config = config or PipelineConfig()
        self.session_loader = SessionLoader(self.config)
        self.audio_normalizer = AudioNormalizer(self.config)
        self.transcriber = Transcriber(self.config)
        self.whisperx_aligner = WhisperXAligner(self.config)
        self.pyannote_diarizer = PyannoteDiarizer(self.config)
        self.transcript_merger = TranscriptMerger(self.config)
        self.transcript_normalizer = TranscriptNormalizer(self.config)
        self.segmenter = TranscriptSegmenter(self.config)
        self.utterance_builder = UtteranceBuilder(self.config)
        self.speaker_attributor = SpeakerAttributor(self.config)
        self.sentence_reconstructor = SentenceReconstructor(self.config)
        self.qa_extractor = QAPairExtractor(self.config)
        self.json_exporter = JsonExporter(self.config)
        self._active_timer: PipelineTimer | None = None

    def ingest(
        self,
        input_paths: str | Path | Sequence[str | Path],
        session_id: str | None = None,
    ) -> LectureSession:
        """Load a lecture session and normalize every source to audio."""

        # Directory creation is centralized here so downstream components can
        # assume their artifact roots already exist.
        self.config.ensure_working_directories()

        timer = self._resolve_timer()
        with timer.measure(
            "session_loading",
            metadata={"requested_session_id": session_id},
        ) as stage:
            session = self.session_loader.load_session(
                input_paths=input_paths,
                session_id=session_id,
            )
            stage.metadata["resolved_session_id"] = session.session_id
            stage.metadata["input_source_count"] = len(session.input_sources)
            stage.metadata["ignored_input_count"] = len(
                session.metadata.get("ignored_inputs", []),
            )
            stage.status = "executed"
        session.pipeline_timing = timer.report
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)

        LOGGER.info(
            "Starting audio normalization for %s input source(s).",
            len(session.input_sources),
        )
        with timer.measure(
            "audio_normalization",
            metadata={"output_format": self.config.normalized_audio_format},
        ) as stage:
            session.audio_sources = self.audio_normalizer.normalize_sources(
                session.input_sources,
            )
            stage.metadata["audio_source_count"] = len(session.audio_sources)
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="normalization",
            )
        session.metadata["normalized_audio_format"] = (
            self.config.normalized_audio_format
        )
        session.metadata["normalized_audio_sample_rate"] = (
            self.config.normalized_audio_sample_rate
        )
        session.metadata["normalized_audio_channels"] = (
            self.config.normalized_audio_channels
        )
        session.metadata["normalized_audio_bit_depth"] = (
            self.config.normalized_audio_bit_depth
        )
        session.processing_status = ProcessingStatus.READY
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def transcribe(self, session: LectureSession) -> LectureSession:
        """Transcribe and build the merged normalized transcript."""

        # Transcription and transcript post-processing are grouped because the
        # rest of the pipeline operates on the merged session-level view.
        timer = self._resolve_timer(session)
        with timer.measure(
            "transcription",
            metadata={
                "backend": self.config.transcription_backend,
                "model_name": self.config.transcription_model_name,
                "compute_type": self.config.transcription_compute_type,
            },
        ) as stage:
            self.transcriber.transcribe_session(session)
            stage.metadata["audio_source_count"] = len(session.audio_sources)
            stage.metadata["transcript_chunk_count"] = len(session.transcript_chunks)
            stage.metadata["transcription_cache_enabled"] = (
                session.metadata.get("transcription_cache_enabled")
            )
            stage.metadata["transcription_cache_lookup_performed"] = (
                session.metadata.get("transcription_cache_lookup_performed")
            )
            stage.metadata["transcription_cache_hit"] = session.metadata.get(
                "transcription_cache_hit",
            )
            stage.metadata["transcription_recomputed"] = session.metadata.get(
                "transcription_recomputed",
            )
            stage.metadata["transcription_compute_type"] = session.metadata.get(
                "transcription_compute_type",
            )
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="transcription",
            )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        self.align_transcript(session)
        self.build_utterances(session)
        self.diarize_speakers(session)
        self.attribute_utterance_speakers(session)
        self.reconstruct_sentences(session)
        self.post_process_transcript(session)
        session.processing_status = ProcessingStatus.READY
        return session

    def align_transcript(self, session: LectureSession) -> LectureSession:
        """Run the optional alignment refinement layer after ASR."""

        timer = self._resolve_timer(session)
        with timer.measure(
            "alignment",
            metadata={"enabled": self.config.transcript_alignment_enabled},
        ) as stage:
            aligned_transcripts = self.whisperx_aligner.align_session(session)
            stage.status = self._normalize_stage_status(
                session.metadata.get("transcript_alignment_status"),
            )
            stage.note = self._first_source_reason(session, "alignment")
            stage.metadata["aligned_transcript_count"] = len(aligned_transcripts)
            stage.metadata["aligned_word_count"] = session.metadata.get(
                "transcript_alignment_word_count",
                0,
            )
            stage.metadata["failed_source_count"] = len(
                session.metadata.get("transcript_alignment_failed_sources", []),
            )
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="alignment",
            )
        session.metadata["transcript_alignment_enabled"] = (
            self.config.transcript_alignment_enabled
        )
        session.metadata["transcript_alignment_artifact_count"] = len(
            aligned_transcripts,
        )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def build_utterances(self, session: LectureSession) -> LectureSession:
        """Build aligned-word-based utterances when alignment artifacts exist."""

        timer = self._resolve_timer(session)
        with timer.measure("utterance_building") as stage:
            self.utterance_builder.build_session(session)
            stage.status = self._normalize_stage_status(
                session.metadata.get("utterance_build_status"),
            )
            stage.note = session.metadata.get("utterance_build_reason")
            stage.metadata["utterance_count"] = len(session.utterances)
            stage.metadata["failed_source_count"] = len(
                session.metadata.get("utterance_failed_sources", []),
            )
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="utterances",
            )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def diarize_speakers(self, session: LectureSession) -> LectureSession:
        """Run speaker diarization on normalized audio when enabled."""

        timer = self._resolve_timer(session)
        with timer.measure(
            "diarization",
            metadata={
                "enabled": self.config.diarization_enabled,
                "model_name": self.config.diarization_model_name,
            },
        ) as stage:
            diarization_segments = self.pyannote_diarizer.diarize_session(session)
            stage.status = self._normalize_stage_status(
                session.metadata.get("diarization_status"),
            )
            stage.note = self._first_source_reason(session, "diarization")
            stage.metadata["diarization_segment_count"] = len(diarization_segments)
            stage.metadata["speaker_count"] = session.metadata.get(
                "diarization_speaker_count",
                0,
            )
            stage.metadata["failed_source_count"] = len(
                session.metadata.get("diarization_failed_sources", []),
            )
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="diarization",
            )
        session.metadata["diarization_artifact_count"] = len(
            {
                segment.audio_source_id
                for segment in diarization_segments
            },
        )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def attribute_utterance_speakers(self, session: LectureSession) -> LectureSession:
        """Assign anonymous speaker ids to utterances when diarization exists."""

        timer = self._resolve_timer(session)
        with timer.measure("speaker_attribution") as stage:
            self.speaker_attributor.attribute_session(session)
            stage.status = self._normalize_stage_status(
                session.metadata.get("speaker_attribution_status"),
            )
            stage.note = session.metadata.get("speaker_attribution_reason")
            stage.metadata["assigned_utterance_count"] = session.metadata.get(
                "speaker_attribution_assigned_count",
                0,
            )
            stage.metadata["uncertain_utterance_count"] = session.metadata.get(
                "speaker_attribution_uncertain_count",
                0,
            )
            stage.metadata["unassigned_utterance_count"] = session.metadata.get(
                "speaker_attribution_unassigned_count",
                0,
            )
            self._finalize_stage_report(stage)
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def reconstruct_sentences(self, session: LectureSession) -> LectureSession:
        """Reconstruct a sentence layer from speaker-aware utterances."""

        timer = self._resolve_timer(session)
        with timer.measure(
            "sentence_reconstruction",
            metadata={
                "enabled": self.config.sentence_reconstruction_enabled,
                "backend": self.config.sentence_splitter_backend,
            },
        ) as stage:
            self.sentence_reconstructor.reconstruct_session(session)
            stage.status = self._normalize_stage_status(
                session.metadata.get("sentence_reconstruction_status"),
            )
            stage.note = session.metadata.get("sentence_reconstruction_reason")
            stage.metadata["sentence_count"] = len(session.sentences)
            stage.metadata["fallback_source_count"] = session.metadata.get(
                "sentence_reconstruction_fallback_source_count",
                0,
            )
            stage.metadata["failed_source_count"] = len(
                session.metadata.get("sentence_failed_sources", []),
            )
            self._apply_source_stage_report(
                stage,
                session,
                source_metadata_key="sentences",
            )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def post_process_transcript(self, session: LectureSession) -> LectureSession:
        """Merge transcript chunks and apply conservative normalization."""

        # The merger preserves traceability and ordering, while the normalizer
        # only applies format-safe text cleanup.
        timer = self._resolve_timer(session)
        with timer.measure("transcript_post_processing") as stage:
            merged_transcript = self.transcript_merger.merge_session(session)
            normalized_transcript = self.transcript_normalizer.normalize(
                merged_transcript,
            )
            stage.metadata["merged_unit_count"] = len(normalized_transcript.units)
            stage.metadata["detected_language_count"] = len(
                normalized_transcript.detected_languages,
            )
            self._finalize_stage_report(stage, status="executed")
        session.merged_transcript = normalized_transcript
        session.transcript_text = normalized_transcript.full_text
        session.metadata["merged_transcript_unit_count"] = len(
            normalized_transcript.units,
        )
        session.metadata["merged_transcript_languages"] = (
            normalized_transcript.detected_languages
        )
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def segment_transcript(
        self,
        session: LectureSession,
        segmentation_mode: str | None = None,
    ) -> LectureSession:
        """Group merged transcript units using the requested segmentation mode."""

        resolved_mode = self.segmenter.resolved_mode(segmentation_mode)
        timer = self._resolve_timer(session)
        with timer.measure(
            "transcript_segmentation",
            metadata={"segmentation_mode": resolved_mode},
        ) as stage:
            session.segments = self.segmenter.segment_session(
                session,
                mode=resolved_mode,
            )
            stage.status = self._resolve_segmentation_stage_status(session)
            stage.note = self._resolve_segmentation_stage_note(session)
            stage.metadata["segment_count"] = len(session.segments)
            stage.metadata["input_layer"] = session.metadata.get(
                "segmentation_input_layer",
            )
            self._finalize_stage_report(stage)
        session.metadata["segmentation_mode"] = resolved_mode
        session.metadata["segment_count"] = len(session.segments)
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def extract_qa_candidates(self, session: LectureSession) -> LectureSession:
        """Extract rule-based QA candidates from sentences with QA grounding."""

        timer = self._resolve_timer(session)
        with timer.measure(
            "qa_extraction",
            metadata={"enabled": self.config.enable_qa_extraction},
        ) as stage:
            session.qa_candidates = self.qa_extractor.extract(session)
            stage.status = self._resolve_qa_stage_status(session)
            stage.note = self._resolve_qa_stage_note(session)
            stage.metadata["qa_candidate_count"] = len(session.qa_candidates)
            self._finalize_stage_report(stage)
        session.metadata["qa_extraction_enabled"] = self.config.enable_qa_extraction
        session.metadata["qa_candidate_count"] = len(session.qa_candidates)
        self._sync_timing_summary(session)
        self._log_stage_completion(stage)
        return session

    def process(
        self,
        input_paths: str | Path | Sequence[str | Path],
        output_path: str | Path | None = None,
        session_id: str | None = None,
    ) -> LectureSession | dict[str, LectureSession]:
        """Run the pipeline and optionally export one or more segmentation modes.

        The transcript preparation steps run once. Segmentation is then applied
        for each requested mode so structural, windowed, and adaptive outputs
        can be compared in the same run without recomputing transcription.
        """

        total_started_at = utc_now_iso()
        total_started_monotonic = monotonic()
        timing_report = PipelineTimer()
        base_session: LectureSession | None = None
        sessions_by_mode: dict[str, LectureSession] = {}
        self._active_timer = timing_report
        LOGGER.info("Run mode: %s", self.config.pipeline_execution_mode.replace("_", " "))

        try:
            base_session = self.ingest(
                input_paths=input_paths,
                session_id=session_id,
            )
            base_session = self.transcribe(base_session)

            # Segmentation mode fan-out happens after transcript preparation so
            # all segmentation outputs can reuse the same transcript state.
            sessions_by_mode = self.process_segmentation_modes(base_session)
            if output_path is not None:
                for mode, session in sessions_by_mode.items():
                    timer = self._resolve_timer(session)
                    with timer.measure(
                        "json_export",
                        metadata={"segmentation_mode": mode},
                    ) as stage:
                        exported_path = self.json_exporter.export(
                            session=session,
                            output_path=output_path,
                            segmentation_mode=mode,
                        )
                        stage.metadata["output_path"] = str(exported_path)
                        self._finalize_stage_report(stage, status="executed")
                    self._sync_timing_summary(session)
                    self._log_stage_completion(stage)
                    if self.config.export_debug_excel:
                        with timer.measure(
                            "debug_excel_export",
                            metadata={"segmentation_mode": mode},
                        ) as debug_stage:
                            debug_excel_path = self._resolve_debug_excel_path(
                                exported_json_path=exported_path,
                            )
                            export_run_to_excel(exported_path, debug_excel_path)
                            debug_stage.metadata["debug_excel_path"] = str(
                                debug_excel_path,
                            )
                            self._finalize_stage_report(
                                debug_stage,
                                status="executed",
                            )
                        self._sync_timing_summary(session)
                        self._log_stage_completion(debug_stage)
                    else:
                        timer.record(
                            "debug_excel_export",
                            status="skipped",
                            note="debug_excel_export_disabled",
                            metadata={"segmentation_mode": mode},
                        )
                        self._sync_timing_summary(session)
                        LOGGER.info("debug_excel_export: skipped | debug export disabled")
            else:
                for mode, session in sessions_by_mode.items():
                    timer = self._resolve_timer(session)
                    timer.record(
                        "json_export",
                        status="skipped",
                        note="output_path_not_requested",
                        metadata={"segmentation_mode": mode},
                    )
                    LOGGER.info("json_export: skipped | no output path requested")
                    timer.record(
                        "debug_excel_export",
                        status="skipped",
                        note="output_path_not_requested",
                        metadata={"segmentation_mode": mode},
                    )
                    LOGGER.info("debug_excel_export: skipped | no output path requested")
                    self._sync_timing_summary(session)
        except Exception as error:
            failed_sessions = (
                sessions_by_mode
                if sessions_by_mode
                else (
                    {self.config.segmentation_mode: base_session}
                    if base_session is not None
                    else {}
                )
            )
            self._record_total_stage(
                sessions=list(failed_sessions.values()),
                started_at=total_started_at,
                started_monotonic=total_started_monotonic,
                status="failed",
                note=str(error) or "pipeline_failed",
            )
            raise
        finally:
            self._active_timer = None

        self._record_total_stage(
            sessions=list(sessions_by_mode.values()),
            started_at=total_started_at,
            started_monotonic=total_started_monotonic,
            status="completed",
            note=None,
        )

        if len(sessions_by_mode) == 1:
            return next(iter(sessions_by_mode.values()))
        return sessions_by_mode

    def process_segmentation_modes(
        self,
        session: LectureSession,
    ) -> dict[str, LectureSession]:
        """Return segmented session copies for the configured mode selection."""

        sessions_by_mode: dict[str, LectureSession] = {}
        for mode in self._configured_segmentation_modes():
            # Copy the shared session before segmentation so each exported mode
            # remains self-contained and cannot overwrite the other.
            segmented_session = deepcopy(session)
            self.segment_transcript(segmented_session, segmentation_mode=mode)
            self.extract_qa_candidates(segmented_session)
            sessions_by_mode[mode] = segmented_session
        return sessions_by_mode

    def _configured_segmentation_modes(self) -> list[str]:
        """Return the deterministic list of segmentation modes to run."""

        if self.config.segmentation_mode == "both":
            return ["structural", "windowed", "adaptive"]
        return [self.segmenter.resolved_mode(self.config.segmentation_mode)]

    def _resolve_debug_excel_path(
        self,
        *,
        exported_json_path: Path,
    ) -> Path:
        """Return the configured debug Excel path for the current export mode."""

        configured_path = self.config.debug_excel_path
        if configured_path.suffix.lower() != ".xlsx":
            return (configured_path / f"{exported_json_path.stem}.xlsx").resolve()
        target_stem = exported_json_path.stem
        if configured_path.stem:
            target_stem = f"{configured_path.stem}_{target_stem}"
        return configured_path.with_name(f"{target_stem}.xlsx")

    def _resolve_timer(self, session: LectureSession | None = None) -> PipelineTimer:
        """Return the active timer or create a session-local one."""

        if session is not None and session.pipeline_timing is not None:
            return PipelineTimer(session.pipeline_timing)
        if self._active_timer is not None:
            if session is not None and session.pipeline_timing is None:
                session.pipeline_timing = self._active_timer.report
            return self._active_timer

        timer = PipelineTimer()
        if session is not None:
            session.pipeline_timing = timer.report
        return timer

    def _record_total_stage(
        self,
        sessions: Sequence[LectureSession],
        *,
        started_at: str,
        started_monotonic: float,
        status: str,
        note: str | None,
    ) -> None:
        """Append the total pipeline timing to every returned session."""

        if not sessions:
            return

        ended_at = utc_now_iso()
        duration_seconds = round(max(0.0, monotonic() - started_monotonic), 6)
        for session in sessions:
            timer = self._resolve_timer(session)
            timer.record(
                "total_pipeline_execution",
                status=status,
                note=note,
                metadata={
                    "session_id": session.session_id,
                    "segmentation_mode": session.metadata.get("segmentation_mode"),
                    "pipeline_execution_mode": self.config.pipeline_execution_mode,
                    "full_recompute_requested": self.config.force_recompute,
                },
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
            )
            self._sync_timing_summary(session)

    def _sync_timing_summary(self, session: LectureSession) -> None:
        """Mirror compact timing summary values into session metadata."""

        if session.pipeline_timing is None:
            return
        session.pipeline_timing.refresh_summary()
        summary = session.pipeline_timing.summary
        session.metadata["pipeline_timing_available"] = True
        session.metadata["pipeline_timing_stage_count"] = summary.stage_count
        session.metadata["pipeline_timing_total_duration_seconds"] = (
            summary.total_duration_seconds
        )
        session.metadata["pipeline_timing_most_expensive_stage"] = (
            summary.most_expensive_stage_name
        )
        session.metadata["pipeline_execution_mode"] = summary.pipeline_execution_mode
        session.metadata["pipeline_run_profile_label"] = summary.run_profile_label
        session.metadata["pipeline_any_cache_hit"] = summary.any_cache_hit
        session.metadata["pipeline_any_artifact_reuse"] = summary.any_artifact_reuse
        session.metadata["pipeline_full_recompute_requested"] = (
            summary.full_recompute_requested
        )
        session.metadata["pipeline_executed_stage_count"] = (
            summary.executed_stage_count
        )
        session.metadata["pipeline_skipped_stage_count"] = (
            summary.skipped_stage_count
        )
        session.metadata["pipeline_reused_cache_stage_count"] = (
            summary.reused_cache_stage_count
        )
        session.metadata["pipeline_reused_artifact_stage_count"] = (
            summary.reused_artifact_stage_count
        )
        session.metadata["pipeline_forced_recompute_stage_count"] = (
            summary.forced_recompute_stage_count
        )

    def _apply_source_stage_report(
        self,
        stage: Any,
        session: LectureSession,
        *,
        source_metadata_key: str,
    ) -> None:
        """Project source-level reuse metadata into one stage timing report."""

        source_reports = [
            metadata
            for audio_source in session.audio_sources
            if isinstance(
                (metadata := audio_source.metadata.get(source_metadata_key)),
                dict,
            )
        ]
        if not source_reports:
            self._finalize_stage_report(stage)
            return

        cache_hit_count = sum(
            1
            for metadata in source_reports
            if self._metadata_bool(metadata, "cache_hit")
            or self._metadata_bool(metadata, "used_cache")
        )
        artifact_reuse_count = sum(
            1
            for metadata in source_reports
            if self._metadata_bool(metadata, "artifact_reused")
            or self._metadata_bool(metadata, "used_existing_artifact")
        )
        recomputed_count = sum(
            1 for metadata in source_reports if self._metadata_bool(metadata, "recomputed")
        )
        artifact_found_count = sum(
            1
            for metadata in source_reports
            if self._metadata_bool(metadata, "artifact_found")
            or self._metadata_bool(metadata, "cache_artifact_found")
        )
        ignored_due_to_force_count = sum(
            1
            for metadata in source_reports
            if self._metadata_bool(
                metadata,
                "artifact_ignored_due_to_force_recompute",
            )
            or self._metadata_bool(
                metadata,
                "cache_ignored_due_to_force_recompute",
            )
        )
        forced_recompute = any(
            self._metadata_bool(metadata, "forced_recompute")
            or self._metadata_bool(metadata, "transcription_forced_recompute")
            for metadata in source_reports
        )
        used_cache = cache_hit_count > 0
        used_existing_artifact = artifact_reuse_count > 0

        if stage.status in {"failed", "skipped", "disabled"}:
            status = stage.status
        elif cache_hit_count == len(source_reports) and recomputed_count == 0:
            status = "reused_from_cache"
        elif artifact_reuse_count == len(source_reports) and recomputed_count == 0:
            status = "reused_from_artifact"
        elif forced_recompute:
            status = "executed_forced"
        else:
            status = "executed"

        stage.metadata["source_report_count"] = len(source_reports)
        stage.metadata["recomputed_source_count"] = recomputed_count
        stage.metadata["cache_hit_source_count"] = cache_hit_count
        stage.metadata["artifact_found_source_count"] = artifact_found_count
        stage.metadata["artifact_reused_source_count"] = artifact_reuse_count
        stage.metadata["artifact_ignored_due_to_force_recompute_count"] = (
            ignored_due_to_force_count
        )
        self._finalize_stage_report(
            stage,
            status=status,
            used_cache=used_cache,
            used_existing_artifact=used_existing_artifact,
            forced_recompute=forced_recompute,
        )

    @staticmethod
    def _finalize_stage_report(
        stage: Any,
        *,
        status: str | None = None,
        used_cache: bool | None = None,
        used_existing_artifact: bool | None = None,
        forced_recompute: bool | None = None,
    ) -> None:
        """Finalize the stage report with a normalized status and flags."""

        if status is not None:
            stage.status = status
        stage.status = LectureProcessingPipeline._normalize_stage_status(
            getattr(stage, "status", "running"),
            default="executed",
        )
        if getattr(stage, "status", "running") == "running":
            stage.status = "executed"
        if used_cache is not None:
            stage.used_cache = used_cache
        if used_existing_artifact is not None:
            stage.used_existing_artifact = used_existing_artifact
        if forced_recompute is not None:
            stage.forced_recompute = forced_recompute

    def _log_stage_completion(self, stage: Any) -> None:
        """Emit one compact log line describing the stage outcome."""

        parts = [f"{stage.stage_name}: {stage.status}"]
        if getattr(stage, "used_cache", False):
            parts.append("cache hit")
        if getattr(stage, "used_existing_artifact", False):
            parts.append("artifact reuse")
        if getattr(stage, "forced_recompute", False):
            parts.append("forced recompute")
        if getattr(stage, "duration_seconds", None) is not None:
            parts.append(f"{stage.duration_seconds:.3f}s")
        LOGGER.info(" | ".join(parts))

    @staticmethod
    def _metadata_bool(metadata: dict[str, Any], key: str) -> bool:
        """Return a stable boolean view over one metadata field."""

        return bool(metadata.get(key))

    @staticmethod
    def _normalize_stage_status(status: object, default: str = "executed") -> str:
        """Map module-specific status labels to the shared timing vocabulary."""

        normalized = str(status or "").strip().lower()
        if normalized in {
            "partial",
            "skipped",
            "disabled",
            "failed",
            "executed",
            "executed_forced",
            "reused_from_cache",
            "reused_from_artifact",
        }:
            return normalized
        if normalized in {"completed", "ready", "available"}:
            return "executed"
        return default

    @staticmethod
    def _first_source_reason(session: LectureSession, metadata_key: str) -> str | None:
        """Return the first available source-level reason for one stage."""

        for audio_source in session.audio_sources:
            metadata = audio_source.metadata.get(metadata_key)
            if isinstance(metadata, dict) and metadata.get("reason"):
                return str(metadata["reason"])
        return None

    @staticmethod
    def _resolve_segmentation_stage_status(session: LectureSession) -> str:
        """Return the timing status for the segmentation stage."""

        status = str(session.metadata.get("segmentation_status", "")).strip().lower()
        if status in {"completed", "partial", "skipped", "disabled", "failed"}:
            return status
        if session.merged_transcript is None:
            return "skipped"
        if not session.merged_transcript.units:
            return "skipped"
        return "completed"

    @staticmethod
    def _resolve_segmentation_stage_note(session: LectureSession) -> str | None:
        """Return a compact explanatory note for segmentation when skipped."""

        reason = session.metadata.get("segmentation_reason")
        if isinstance(reason, str) and reason.strip():
            return reason
        if session.merged_transcript is None:
            return "merged_transcript_unavailable"
        if not session.merged_transcript.units:
            return "merged_transcript_units_unavailable"
        return None

    def _resolve_qa_stage_status(self, session: LectureSession) -> str:
        """Return the timing status for the QA extraction stage."""

        if not self.config.enable_qa_extraction:
            return "disabled"
        if session.merged_transcript is None:
            return "skipped"
        if not session.merged_transcript.units:
            return "skipped"
        return "completed"

    def _resolve_qa_stage_note(self, session: LectureSession) -> str | None:
        """Return a compact explanatory note for QA extraction when not run."""

        if not self.config.enable_qa_extraction:
            return "qa_extraction_disabled"
        if session.merged_transcript is None:
            return "merged_transcript_unavailable"
        if not session.merged_transcript.units:
            return "merged_transcript_units_unavailable"
        return None
