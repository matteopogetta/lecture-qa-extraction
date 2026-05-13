"""Data models for placeholder analysis results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StageResult:
    """Represent the status of one pipeline stage."""

    name: str
    status: str
    message: str


@dataclass(slots=True)
class PlaceholderResult:
    """Represent the JSON payload written by the bootstrap CLI."""

    run_id: str
    status: str
    input: str
    output_directory: str
    transcription_model: str
    generated_at: str
    stages: list[StageResult]

    def to_dict(self) -> dict[str, Any]:
        """Convert the result into a JSON-serializable dictionary."""

        payload = asdict(self)
        payload["stages"] = [asdict(stage) for stage in self.stages]
        return payload

    @property
    def output_filename(self) -> str:
        """Return the default JSON filename for this placeholder result."""

        input_name = Path(self.input).stem or "lecture"
        return f"{input_name}_analysis.json"
