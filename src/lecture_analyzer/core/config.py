"""Configuration models for the CLI and the src-based pipeline runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from lecture_analyzer.core import _pipeline_config_impl as pipeline_config_impl
from lecture_analyzer.core._pipeline_config_impl import *  # noqa: F401,F403


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for one CLI execution."""

    input_path: Path
    output_dir: Path
    log_level: str = "INFO"
    transcription_model: str = ""


def __getattr__(name: str) -> object:
    """Expose the consolidated pipeline configuration through the src package."""

    return getattr(pipeline_config_impl, name)


__all__ = [
    "AppConfig",
    *[
        name
        for name in dir(pipeline_config_impl)
        if not name.startswith("_")
    ],
]
