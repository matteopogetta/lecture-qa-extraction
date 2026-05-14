"""Simple exceptions for ingestion and normalization failures."""

from __future__ import annotations


class PipelineError(Exception):
    """Base exception for the lecture processing prototype."""


class IngestionError(PipelineError):
    """Base exception for session loading and input normalization."""


class MissingInputError(IngestionError):
    """Raised when an expected input path does not exist."""


class EmptyInputError(IngestionError):
    """Raised when no supported input media files are available."""


class UnsupportedMediaError(IngestionError):
    """Raised when a caller explicitly provides an unsupported media file."""


class AudioNormalizationError(IngestionError):
    """Raised when source-to-audio normalization cannot be completed."""


class AudioExtractionError(AudioNormalizationError):
    """Raised when media-to-audio conversion cannot be completed."""


class AudioValidationError(AudioNormalizationError):
    """Raised when a normalized audio artifact is invalid or corrupted."""


class AudioMetadataError(AudioNormalizationError):
    """Raised when normalized audio metadata cannot be loaded or saved."""
