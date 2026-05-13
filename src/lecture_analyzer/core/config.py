"""Configuration models for the lecture analyzer CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for one CLI execution."""

    input_path: Path
    output_dir: Path
    log_level: str = "INFO"
    transcription_model: str = ""
