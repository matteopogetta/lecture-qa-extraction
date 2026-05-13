"""Shared enums and constrained values for the lecture prototype."""

from __future__ import annotations

from enum import Enum


class MediaType(str, Enum):
    """Supported original media types for lecture inputs."""

    AUDIO = "audio"
    VIDEO = "video"
    UNSUPPORTED = "unsupported"


class SpeakerRole(str, Enum):
    """Supported estimated speaker roles."""

    TEACHER = "teacher"
    STUDENT = "student"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    """High-level processing states for pipeline artifacts."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
