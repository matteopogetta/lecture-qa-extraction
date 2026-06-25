"""Configuration objects for the lecture processing prototype."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SUPPORTED_NORMALIZED_AUDIO_FORMATS = {"wav", "flac"}
SUPPORTED_PIPELINE_PROFILES = {
    "current",
    "light",
    "quality",
    "quality_local",
    "full",
    "diagnostic",
}

PIPELINE_PROFILE_SETTINGS: dict[str, dict[str, object]] = {
    "current": {},
    "light": {
        "transcript_alignment_enabled": False,
        "diarization_enabled": False,
        "qa_answer_search_strategy": "local_rule_based",
        "qa_semantic_retrieval_enabled": False,
        "qa_answer_ranking_strategy": "rule_based",
        "qa_semantic_reranking_enabled": False,
        "export_debug_excel": False,
    },
    "quality": {
        "transcript_alignment_enabled": True,
        "diarization_enabled": False,
        "qa_answer_search_strategy": "semantic_retrieval",
        "qa_semantic_retrieval_enabled": True,
        "qa_answer_ranking_strategy": "semantic_reranker",
        "qa_semantic_reranking_enabled": True,
        "export_debug_excel": False,
    },
    "quality_local": {
        "transcript_alignment_enabled": True,
        "diarization_enabled": False,
        "qa_answer_search_strategy": "local_rule_based",
        "qa_semantic_retrieval_enabled": False,
        "qa_answer_ranking_strategy": "rule_based",
        "qa_semantic_reranking_enabled": False,
        "export_debug_excel": False,
    },
    "full": {
        "transcript_alignment_enabled": True,
        "diarization_enabled": True,
        "qa_answer_search_strategy": "semantic_retrieval",
        "qa_semantic_retrieval_enabled": True,
        "qa_answer_ranking_strategy": "semantic_reranker",
        "qa_semantic_reranking_enabled": True,
        "export_debug_excel": True,
    },
    "diagnostic": {
        "transcript_alignment_enabled": True,
        "diarization_enabled": True,
        "segmentation_mode": "both",
        "qa_answer_search_strategy": "semantic_retrieval",
        "qa_semantic_retrieval_enabled": True,
        "qa_answer_ranking_strategy": "semantic_reranker",
        "qa_semantic_reranking_enabled": True,
        "export_debug_excel": True,
        "export_ai_review_packet": True,
    },
}


@dataclass(slots=True)
class PipelineConfig:
    """Runtime configuration for the processing pipeline."""

    # Input and preprocessing defaults.
    pipeline_profile: str = "current"
    working_directory: Path = Path("artifacts")
    audio_extensions: tuple[str, ...] = (".wav", ".mp3", ".m4a", ".aac", ".flac")
    video_extensions: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi")
    normalized_audio_directory: Path | None = None
    normalized_audio_format: str = "wav"
    normalized_audio_sample_rate: int = 16000
    normalized_audio_channels: int = 1
    normalized_audio_bit_depth: int = 16
    normalized_audio_metadata_extension: str = ".metadata.json"
    overwrite_normalized_audio: bool = False
    ffmpeg_executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"

    # Transcription defaults.
    default_language: str = "it-en"
    default_speaker_label: str = "speaker_1"
    transcription_backend: str = "faster-whisper"
    transcription_model_name: str = "small"
    transcription_compute_type: str = "auto"
    transcription_language_mode: str = "auto"
    transcription_language: str | None = None
    transcription_beam_size: int = 5
    transcription_debug_directory: Path | None = None
    transcription_cache_enabled: bool = True
    transcription_cache_directory: Path | None = None
    transcription_cache_text_extension: str = ".txt"
    transcription_cache_manifest_extension: str = ".transcription.json"
    transcription_cache_allow_text_fallback: bool = True
    transcription_cache_require_backend_match: bool = False
    force_recompute: bool = False
    transcript_alignment_enabled: bool = True
    transcript_alignment_model_name: str | None = None
    transcript_alignment_device: str = "cpu"
    diarization_enabled: bool = False
    diarization_model_name: str = "pyannote/speaker-diarization-3.1"
    diarization_device: str = "cpu"
    diarization_auth_token: str | None = None
    diarization_num_speakers: int | None = None
    diarization_min_speakers: int | None = None
    diarization_max_speakers: int | None = None
    diarization_prefer_exclusive: bool = True
    speaker_attribution_min_overlap_ratio: float = 0.6
    speaker_attribution_ambiguity_ratio: float = 0.85
    speaker_switch_min_duration_seconds: float = 1.25
    speaker_switch_min_stable_evidence_seconds: float = 1.0
    speaker_switch_short_gap_merge_seconds: float = 0.5
    speaker_attribution_allow_uncertain: bool = True
    speaker_attribution_low_energy_ratio_threshold: float | None = 0.35
    speaker_attribution_high_zero_crossing_rate_threshold: float | None = 0.22
    utterance_max_gap_seconds: float = 0.85
    sentence_reconstruction_enabled: bool = True
    sentence_splitter_backend: str = "wtpsplit"
    sentence_splitter_model_name: str = "sat-3l-sm"
    sentence_reconstruction_max_gap_seconds: float = 2.5
    sentence_reconstruction_respect_speaker_boundaries: bool = True
    sentence_fragment_max_word_count: int = 3
    sentence_fragment_max_text_length: int = 20
    sentence_fragment_max_duration_seconds: float = 2.0
    sentence_run_on_max_word_count: int = 24
    sentence_run_on_max_duration_seconds: float = 12.0
    sentence_merge_max_word_count: int = 30
    sentence_merge_max_duration_seconds: float = 15.0
    sentence_speaker_uncertain_weight: float = 0.65
    sentence_speaker_unassigned_noise_weight: float = 0.5
    sentence_speaker_duration_weight: float = 0.35
    sentence_speaker_duration_reference_seconds: float = 1.0
    sentence_speaker_word_weight: float = 0.25
    sentence_speaker_word_reference_count: int = 4
    sentence_speaker_short_fragment_duration_seconds: float = 0.45
    sentence_speaker_short_fragment_word_count: int = 2
    sentence_speaker_short_fragment_weight: float = 0.5
    sentence_speaker_final_utterance_bonus: float = 1.08
    sentence_speaker_dominant_share_threshold: float = 0.58
    sentence_speaker_dominance_margin_threshold: float = 0.45
    sentence_speaker_internal_recovery_share_threshold: float = 0.52
    sentence_speaker_internal_recovery_margin_threshold: float = 0.3
    sentence_speaker_conflict_share_threshold: float = 0.33
    sentence_speaker_context_recovery_max_duration_seconds: float = 2.25
    sentence_speaker_context_recovery_max_word_count: int = 6
    sentence_speaker_context_recovery_max_gap_seconds: float = 1.0
    sentence_speaker_context_recovery_max_conflict_share: float = 0.2
    sentence_semantic_incomplete_markers: tuple[str, ...] = (
        "and",
        "or",
        "but",
        "because",
        "if",
        "then",
        "that",
        "which",
        "when",
        "where",
        "while",
        "to",
        "for",
        "with",
        "without",
        "of",
        "in",
        "on",
        "at",
        "from",
        "into",
        "the",
        "a",
        "an",
        "e",
        "ed",
        "o",
        "ma",
        "per",
        "che",
        "di",
        "a",
        "da",
        "con",
        "se",
        "quando",
        "dove",
        "mentre",
        "quindi",
        "il",
        "lo",
        "la",
        "gli",
        "le",
        "un",
        "uno",
        "una",
    )
    transcript_drop_empty_chunks: bool = True
    transcript_keep_raw_text: bool = True
    transcript_normalize_whitespace: bool = True
    transcript_normalize_punctuation_spacing: bool = True
    transcript_invalid_timing_policy: str = "clamp"

    # Segmentation defaults.
    segmentation_mode: str = "structural"
    segmentation_max_gap_seconds: float = 2.0
    segmentation_soft_max_gap_seconds: float = 7.0
    segmentation_max_duration_seconds: float = 90.0
    segmentation_max_text_length: int = 800
    segmentation_split_on_source_change: bool = True
    segmentation_drop_empty_units: bool = True
    segmentation_keep_singleton_short_segments: bool = True
    segmentation_incomplete_text_continuation_enabled: bool = True
    segmentation_min_standalone_word_count: int = 4
    segmentation_min_standalone_text_length: int = 20
    segmentation_gap_override_for_incomplete_text: bool = True
    segmentation_window_seconds: float = 60.0
    segmentation_window_overlap_seconds: float = 0.0
    segmentation_window_min_units: int = 1
    segmentation_adaptive_target_duration_seconds: float = 55.0
    segmentation_adaptive_max_duration_seconds: float = 85.0
    segmentation_adaptive_target_text_length: int = 650
    segmentation_adaptive_max_text_length: int = 950
    segmentation_adaptive_boundary_lookback_units: int = 4
    segmentation_adaptive_min_boundary_score: float = 1.5
    segmentation_adaptive_transition_markers: tuple[str, ...] = (
        "now",
        "so",
        "therefore",
        "then",
        "next",
        "okay",
        "well",
        "allora",
        "quindi",
        "ora",
        "bene",
        "adesso",
        "dunque",
    )
    segmentation_continuation_markers: tuple[str, ...] = (
        "and",
        "or",
        "but",
        "because",
        "if",
        "then",
        "that",
        "which",
        "when",
        "where",
        "while",
        "to",
        "for",
        "with",
        "without",
        "of",
        "in",
        "on",
        "at",
        "from",
        "into",
        "e",
        "ed",
        "o",
        "ma",
        "per",
        "che",
        "di",
        "a",
        "da",
        "con",
        "se",
        "quando",
        "dove",
        "mentre",
        "quindi",
    )

    # QA extraction defaults.
    enable_qa_extraction: bool = True
    max_answer_units: int = 3
    max_answer_duration_seconds: float = 45.0
    max_question_length_chars: int = 220
    min_question_score: float = 0.45
    min_qa_confidence: float = 0.4
    question_context_expansion_enabled: bool = True
    question_context_max_gap_seconds: float = 3.5
    question_context_short_token_threshold: int = 5
    deferred_answer_search_enabled: bool = True
    deferred_answer_search_window_units: int = 28
    deferred_answer_search_min_question_score: float = 0.62
    deferred_answer_search_local_score_threshold: float = 0.38
    deferred_answer_search_min_signal_score: float = 0.18
    allow_cross_segment_answer: bool = True
    qa_answer_search_strategy: str = "semantic_retrieval" #"local_rule_based"
    qa_semantic_retrieval_enabled: bool = True #False
    qa_semantic_retrieval_model_name: str = "intfloat/multilingual-e5-base"
    qa_semantic_retrieval_top_k: int = 4
    qa_semantic_retrieval_window_units: int = 8
    qa_semantic_retrieval_min_similarity: float = 0.2
    qa_answer_ranking_strategy: str = "semantic_reranker" #"rule_based"
    qa_semantic_reranking_enabled: bool = True #False
    qa_semantic_reranking_model_name: str = "BAAI/bge-reranker-v2-m3"
    qa_semantic_reranking_max_candidates: int = 8
    qa_semantic_reranking_weight: float = 0.7
    answer_search_window_units: int = 3

    # Output defaults.
    export_indent: int = 2
    export_debug_excel: bool = True
    debug_excel_path: Path = Path("debug.xlsx")
    export_ai_review_packet: bool = False
    ai_review_packet_path: Path = Path("ai_review_packet.md")
    export_evaluation_run: bool = False
    evaluation_root_directory: Path = Path("evaluations")
    evaluation_input_label: str | None = None
    evaluation_run_label: str | None = None

    def __post_init__(self) -> None:
        """Normalize configurable paths after initialization."""

        self.pipeline_profile = self._normalize_pipeline_profile(
            self.pipeline_profile,
        )
        self._apply_pipeline_profile_defaults()
        self._normalize_configurable_values()

    def apply_overrides(self, **overrides: object) -> None:
        """Apply explicit runtime overrides after profile defaults."""

        profile_override = overrides.pop("pipeline_profile", None)
        if profile_override is not None:
            self.pipeline_profile = self._normalize_pipeline_profile(
                str(profile_override),
            )
            self._apply_pipeline_profile_defaults()

        for name, value in overrides.items():
            if not hasattr(self, name):
                raise AttributeError(f"Unknown pipeline configuration option: {name}")
            setattr(self, name, value)
        self._normalize_configurable_values()

    def _apply_pipeline_profile_defaults(self) -> None:
        """Apply the selected profile before explicit runtime overrides."""

        for name, value in PIPELINE_PROFILE_SETTINGS[self.pipeline_profile].items():
            setattr(self, name, value)

    def _normalize_configurable_values(self) -> None:
        """Normalize configurable values after construction or overrides."""

        # Resolve path-like settings immediately so every component works with
        # absolute, normalized locations rather than caller-specific strings.
        self.pipeline_profile = self._normalize_pipeline_profile(
            self.pipeline_profile,
        )
        self.working_directory = self.working_directory.expanduser().resolve()
        if self.normalized_audio_directory is not None:
            self.normalized_audio_directory = (
                self.normalized_audio_directory.expanduser().resolve()
            )
        if self.transcription_debug_directory is not None:
            self.transcription_debug_directory = (
                self.transcription_debug_directory.expanduser().resolve()
            )
        if self.transcription_cache_directory is not None:
            self.transcription_cache_directory = (
                self.transcription_cache_directory.expanduser().resolve()
            )
        self.debug_excel_path = self._resolve_output_artifact_path(
            self.debug_excel_path,
        )
        self.ai_review_packet_path = self._resolve_output_artifact_path(
            self.ai_review_packet_path,
        )
        self.evaluation_root_directory = (
            self.evaluation_root_directory.expanduser().resolve()
        )
        if self.evaluation_input_label is not None:
            normalized_evaluation_input_label = self.evaluation_input_label.strip()
            self.evaluation_input_label = normalized_evaluation_input_label or None
        if self.evaluation_run_label is not None:
            normalized_evaluation_run_label = self.evaluation_run_label.strip()
            self.evaluation_run_label = normalized_evaluation_run_label or None
        self.transcription_cache_text_extension = self._normalize_extension(
            self.transcription_cache_text_extension,
        )
        self.transcription_cache_manifest_extension = self._normalize_extension(
            self.transcription_cache_manifest_extension,
        )
        self.normalized_audio_metadata_extension = (
            self._normalize_metadata_extension(
                self.normalized_audio_metadata_extension,
            )
        )

        # Normalize user-facing string options to a constrained internal form
        # before the rest of the pipeline starts reading configuration.
        self.normalized_audio_format = self.normalized_audio_format.strip().lower()
        if self.normalized_audio_format not in SUPPORTED_NORMALIZED_AUDIO_FORMATS:
            raise ValueError(
                "normalized_audio_format must be one of: "
                f"{', '.join(sorted(SUPPORTED_NORMALIZED_AUDIO_FORMATS))}.",
            )
        self.normalized_audio_sample_rate = max(
            1,
            int(self.normalized_audio_sample_rate),
        )
        self.normalized_audio_channels = max(1, int(self.normalized_audio_channels))
        self.normalized_audio_bit_depth = max(
            1,
            int(self.normalized_audio_bit_depth),
        )
        self.transcription_language_mode = (
            self.transcription_language_mode.strip().lower() or "auto"
        )
        self.transcription_compute_type = (
            self.transcription_compute_type.strip().lower() or "auto"
        )
        if self.transcription_language is not None:
            normalized_language = self.transcription_language.strip().lower()
            self.transcription_language = normalized_language or None
        if self.transcript_alignment_model_name is not None:
            normalized_alignment_model = self.transcript_alignment_model_name.strip()
            self.transcript_alignment_model_name = normalized_alignment_model or None
        self.transcript_alignment_device = (
            self.transcript_alignment_device.strip().lower() or "cpu"
        )
        self.diarization_model_name = (
            self.diarization_model_name.strip()
            or "pyannote/speaker-diarization-3.1"
        )
        self.diarization_device = self.diarization_device.strip().lower() or "cpu"
        if self.diarization_auth_token is not None:
            normalized_auth_token = self.diarization_auth_token.strip()
            self.diarization_auth_token = normalized_auth_token or None
        self.diarization_num_speakers = self._normalize_optional_positive_int(
            self.diarization_num_speakers,
        )
        self.diarization_min_speakers = self._normalize_optional_positive_int(
            self.diarization_min_speakers,
        )
        self.diarization_max_speakers = self._normalize_optional_positive_int(
            self.diarization_max_speakers,
        )
        if self.diarization_num_speakers is not None:
            self.diarization_min_speakers = None
            self.diarization_max_speakers = None
        elif (
            self.diarization_min_speakers is not None
            and self.diarization_max_speakers is not None
            and self.diarization_min_speakers > self.diarization_max_speakers
        ):
            self.diarization_max_speakers = self.diarization_min_speakers
        self.transcript_invalid_timing_policy = (
            self.transcript_invalid_timing_policy.strip().lower() or "clamp"
        )
        if self.transcript_invalid_timing_policy not in {"clamp", "drop"}:
            self.transcript_invalid_timing_policy = "clamp"
        self.utterance_max_gap_seconds = max(
            0.0,
            float(self.utterance_max_gap_seconds),
        )
        self.sentence_splitter_backend = (
            self.sentence_splitter_backend.strip().lower() or "wtpsplit"
        )
        if self.sentence_splitter_backend not in {"wtpsplit", "fallback_rules"}:
            self.sentence_splitter_backend = "wtpsplit"
        self.sentence_splitter_model_name = (
            self.sentence_splitter_model_name.strip() or "sat-3l-sm"
        )
        self.sentence_reconstruction_max_gap_seconds = max(
            0.0,
            float(self.sentence_reconstruction_max_gap_seconds),
        )
        self.sentence_fragment_max_word_count = max(
            1,
            int(self.sentence_fragment_max_word_count),
        )
        self.sentence_fragment_max_text_length = max(
            1,
            int(self.sentence_fragment_max_text_length),
        )
        self.sentence_fragment_max_duration_seconds = max(
            0.0,
            float(self.sentence_fragment_max_duration_seconds),
        )
        self.sentence_run_on_max_word_count = max(
            self.sentence_fragment_max_word_count + 1,
            int(self.sentence_run_on_max_word_count),
        )
        self.sentence_run_on_max_duration_seconds = max(
            self.sentence_fragment_max_duration_seconds,
            float(self.sentence_run_on_max_duration_seconds),
        )
        self.sentence_merge_max_word_count = max(
            self.sentence_run_on_max_word_count,
            int(self.sentence_merge_max_word_count),
        )
        self.sentence_merge_max_duration_seconds = max(
            self.sentence_run_on_max_duration_seconds,
            float(self.sentence_merge_max_duration_seconds),
        )
        self.sentence_semantic_incomplete_markers = tuple(
            marker.strip().lower()
            for marker in self.sentence_semantic_incomplete_markers
            if marker.strip()
        )
        self.speaker_attribution_min_overlap_ratio = min(
            1.0,
            max(0.0, float(self.speaker_attribution_min_overlap_ratio)),
        )
        self.speaker_attribution_ambiguity_ratio = min(
            1.0,
            max(0.0, float(self.speaker_attribution_ambiguity_ratio)),
        )
        self.speaker_switch_min_duration_seconds = max(
            0.0,
            float(self.speaker_switch_min_duration_seconds),
        )
        self.speaker_switch_min_stable_evidence_seconds = max(
            0.0,
            float(self.speaker_switch_min_stable_evidence_seconds),
        )
        self.speaker_switch_short_gap_merge_seconds = max(
            0.0,
            float(self.speaker_switch_short_gap_merge_seconds),
        )
        self.speaker_attribution_low_energy_ratio_threshold = (
            self._normalize_optional_unit_float(
                self.speaker_attribution_low_energy_ratio_threshold,
            )
        )
        self.speaker_attribution_high_zero_crossing_rate_threshold = (
            self._normalize_optional_unit_float(
                self.speaker_attribution_high_zero_crossing_rate_threshold,
            )
        )
        self.segmentation_mode = self.segmentation_mode.strip().lower() or "structural"
        if self.segmentation_mode not in {
            "structural",
            "windowed",
            "adaptive",
            "both",
        }:
            self.segmentation_mode = "structural"

        # Clamp numeric settings to safe lower bounds so heuristic code can
        # rely on stable invariants without repeating validation.
        self.segmentation_max_gap_seconds = max(
            0.0,
            float(self.segmentation_max_gap_seconds),
        )
        self.segmentation_soft_max_gap_seconds = max(
            self.segmentation_max_gap_seconds,
            float(self.segmentation_soft_max_gap_seconds),
        )
        self.segmentation_max_duration_seconds = max(
            1.0,
            float(self.segmentation_max_duration_seconds),
        )
        self.segmentation_max_text_length = max(
            1,
            int(self.segmentation_max_text_length),
        )
        self.segmentation_min_standalone_word_count = max(
            1,
            int(self.segmentation_min_standalone_word_count),
        )
        self.segmentation_min_standalone_text_length = max(
            1,
            int(self.segmentation_min_standalone_text_length),
        )
        self.segmentation_window_seconds = max(
            1.0,
            float(self.segmentation_window_seconds),
        )
        self.segmentation_window_overlap_seconds = max(
            0.0,
            float(self.segmentation_window_overlap_seconds),
        )
        if self.segmentation_window_overlap_seconds >= self.segmentation_window_seconds:
            self.segmentation_window_overlap_seconds = max(
                0.0,
                self.segmentation_window_seconds - 0.001,
            )
        self.segmentation_window_min_units = max(
            1,
            int(self.segmentation_window_min_units),
        )
        self.segmentation_adaptive_target_duration_seconds = max(
            1.0,
            float(self.segmentation_adaptive_target_duration_seconds),
        )
        self.segmentation_adaptive_max_duration_seconds = max(
            self.segmentation_adaptive_target_duration_seconds,
            float(self.segmentation_adaptive_max_duration_seconds),
        )
        self.segmentation_adaptive_target_text_length = max(
            1,
            int(self.segmentation_adaptive_target_text_length),
        )
        self.segmentation_adaptive_max_text_length = max(
            self.segmentation_adaptive_target_text_length,
            int(self.segmentation_adaptive_max_text_length),
        )
        self.segmentation_adaptive_boundary_lookback_units = max(
            1,
            int(self.segmentation_adaptive_boundary_lookback_units),
        )
        self.segmentation_adaptive_min_boundary_score = float(
            self.segmentation_adaptive_min_boundary_score,
        )
        self.max_answer_units = max(1, int(self.max_answer_units))
        self.max_answer_duration_seconds = max(
            1.0,
            float(self.max_answer_duration_seconds),
        )
        self.max_question_length_chars = max(
            8,
            int(self.max_question_length_chars),
        )
        self.min_question_score = min(
            1.0,
            max(0.0, float(self.min_question_score)),
        )
        self.min_qa_confidence = min(
            1.0,
            max(0.0, float(self.min_qa_confidence)),
        )
        self.question_context_max_gap_seconds = max(
            0.0,
            float(self.question_context_max_gap_seconds),
        )
        self.question_context_short_token_threshold = max(
            1,
            int(self.question_context_short_token_threshold),
        )
        self.deferred_answer_search_window_units = max(
            self.answer_search_window_units,
            int(self.deferred_answer_search_window_units),
        )
        self.deferred_answer_search_min_question_score = min(
            1.0,
            max(0.0, float(self.deferred_answer_search_min_question_score)),
        )
        self.deferred_answer_search_local_score_threshold = min(
            1.0,
            max(0.0, float(self.deferred_answer_search_local_score_threshold)),
        )
        self.deferred_answer_search_min_signal_score = min(
            1.0,
            max(0.0, float(self.deferred_answer_search_min_signal_score)),
        )
        self.qa_answer_search_strategy = (
            self.qa_answer_search_strategy.strip().lower() or "local_rule_based"
        )
        if self.qa_answer_search_strategy not in {
            "local_rule_based",
            "semantic_retrieval",
        }:
            self.qa_answer_search_strategy = "local_rule_based"
        self.qa_semantic_retrieval_model_name = (
            self.qa_semantic_retrieval_model_name.strip()
            or "intfloat/multilingual-e5-base"
        )
        self.qa_semantic_retrieval_top_k = max(
            1,
            int(self.qa_semantic_retrieval_top_k),
        )
        self.qa_semantic_retrieval_min_similarity = min(
            1.0,
            max(0.0, float(self.qa_semantic_retrieval_min_similarity)),
        )
        self.qa_answer_ranking_strategy = (
            self.qa_answer_ranking_strategy.strip().lower() or "rule_based"
        )
        if self.qa_answer_ranking_strategy not in {
            "rule_based",
            "semantic_reranker",
        }:
            self.qa_answer_ranking_strategy = "rule_based"
        self.qa_semantic_reranking_model_name = (
            self.qa_semantic_reranking_model_name.strip()
            or "BAAI/bge-reranker-v2-m3"
        )
        self.qa_semantic_reranking_max_candidates = max(
            1,
            int(self.qa_semantic_reranking_max_candidates),
        )
        self.qa_semantic_reranking_weight = min(
            1.0,
            max(0.0, float(self.qa_semantic_reranking_weight)),
        )
        self.answer_search_window_units = max(
            1,
            int(self.answer_search_window_units),
        )
        self.qa_semantic_retrieval_window_units = max(
            self.answer_search_window_units,
            int(self.qa_semantic_retrieval_window_units),
        )
        self.segmentation_adaptive_transition_markers = tuple(
            marker.strip().lower()
            for marker in self.segmentation_adaptive_transition_markers
            if marker.strip()
        )
        self.segmentation_continuation_markers = tuple(
            marker.strip().lower()
            for marker in self.segmentation_continuation_markers
            if marker.strip()
        )

    def ensure_working_directories(self) -> None:
        """Create directories used by the pipeline if they do not exist."""

        self.audio_artifacts_directory.mkdir(parents=True, exist_ok=True)
        if self.transcription_debug_directory is not None:
            self.transcription_debug_directory.mkdir(parents=True, exist_ok=True)
        if self.transcription_cache_directory is not None:
            self.transcription_cache_directory.mkdir(parents=True, exist_ok=True)
        self.alignment_artifacts_directory.mkdir(parents=True, exist_ok=True)
        self.diarization_artifacts_directory.mkdir(parents=True, exist_ok=True)
        self.utterance_artifacts_directory.mkdir(parents=True, exist_ok=True)
        self.sentence_artifacts_directory.mkdir(parents=True, exist_ok=True)

    @property
    def audio_artifacts_directory(self) -> Path:
        """Return the directory used for extracted audio artifacts."""

        if self.normalized_audio_directory is not None:
            return self.normalized_audio_directory
        return self.working_directory / "normalized_audio"

    @property
    def normalized_audio_extension(self) -> str:
        """Return the configured extension for normalized audio artifacts."""

        return f".{self.normalized_audio_format}"

    @property
    def extracted_audio_directory(self) -> Path | None:
        """Compatibility alias for the historical audio directory setting."""

        return self.normalized_audio_directory

    @property
    def extracted_audio_extension(self) -> str:
        """Compatibility alias for the historical audio extension setting."""

        return self.normalized_audio_extension

    @property
    def alignment_artifacts_directory(self) -> Path:
        """Return the directory used for persisted alignment artifacts."""

        return self.working_directory / "alignment"

    @property
    def utterance_artifacts_directory(self) -> Path:
        """Return the directory used for persisted utterance artifacts."""

        return self.working_directory / "utterances"

    @property
    def diarization_artifacts_directory(self) -> Path:
        """Return the directory used for persisted diarization artifacts."""

        return self.working_directory / "diarization"

    @property
    def sentence_artifacts_directory(self) -> Path:
        """Return the directory used for persisted sentence artifacts."""

        return self.working_directory / "sentences"

    @property
    def pipeline_execution_mode(self) -> str:
        """Return the user-selected execution mode for the current run."""

        return "from_scratch" if self.force_recompute else "normal"

    @property
    def transcription_cache_reuse_enabled(self) -> bool:
        """Return whether transcript cache artifacts may be reused as input."""

        return self.transcription_cache_enabled and not self.force_recompute

    @property
    def intermediate_artifact_reuse_enabled(self) -> bool:
        """Return whether existing intermediate artifacts may be reused."""

        return not self.force_recompute

    def _resolve_output_artifact_path(self, value: str | Path) -> Path:
        """Return an absolute output path relative to the working directory."""

        resolved_path = Path(value).expanduser()
        if resolved_path.is_absolute():
            return resolved_path.resolve()
        return (self.working_directory / resolved_path).resolve()

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        """Return a file extension with a leading dot."""

        normalized_extension = extension.strip().lower()
        if not normalized_extension:
            return ".wav"
        if not normalized_extension.startswith("."):
            return f".{normalized_extension}"
        return normalized_extension

    @staticmethod
    def _normalize_metadata_extension(extension: str) -> str:
        """Return a metadata file suffix with a leading dot when needed."""

        normalized_extension = extension.strip().lower()
        if not normalized_extension:
            return ".metadata.json"
        if normalized_extension.startswith("."):
            return normalized_extension
        return f".{normalized_extension}"

    @staticmethod
    def _normalize_pipeline_profile(profile: str) -> str:
        """Return a supported pipeline profile name."""

        normalized_profile = str(profile).strip().lower().replace("-", "_")
        if normalized_profile in {"default", "compat", "compatibility"}:
            normalized_profile = "current"
        if normalized_profile not in SUPPORTED_PIPELINE_PROFILES:
            supported = ", ".join(sorted(SUPPORTED_PIPELINE_PROFILES))
            raise ValueError(f"pipeline_profile must be one of: {supported}.")
        return normalized_profile

    @staticmethod
    def _normalize_optional_positive_int(value: int | None) -> int | None:
        """Return a positive integer or `None` when unset or invalid."""

        if value is None:
            return None
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    @staticmethod
    def _normalize_optional_unit_float(value: float | None) -> float | None:
        """Return a float in the [0, 1] range or `None` when unset."""

        if value is None:
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        return min(1.0, max(0.0, normalized))
