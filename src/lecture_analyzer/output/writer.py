"""Writers for structured output artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from lecture_analyzer.core.models import PlaceholderResult


def ensure_output_directory(output_dir: Path) -> Path:
    """Create the output directory if it does not exist."""

    resolved_dir = output_dir.expanduser().resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    return resolved_dir


def write_result_json(result: PlaceholderResult, output_path: Path) -> Path:
    """Write the placeholder analysis result to JSON."""

    output_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return output_path
