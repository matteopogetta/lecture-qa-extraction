"""Configuration models for the CLI and root-pipeline compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import config as legacy_config


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for one CLI execution."""

    input_path: Path
    output_dir: Path
    log_level: str = "INFO"
    transcription_model: str = ""


def __getattr__(name: str) -> object:
    """Expose the legacy root configuration module through the src package."""

    return getattr(legacy_config, name)


__all__ = [
    "AppConfig",
    *[
        name
        for name in dir(legacy_config)
        if not name.startswith("_")
    ],
]
