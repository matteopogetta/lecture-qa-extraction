"""Input validation utilities."""

from __future__ import annotations

from pathlib import Path

from lecture_analyzer.core.exceptions import InputValidationError


def validate_input_path(input_path: Path) -> Path:
    """Validate that the provided lecture input exists and is a file."""

    resolved_path = input_path.expanduser().resolve()
    if not resolved_path.exists():
        raise InputValidationError(f"Input file does not exist: {input_path}")
    if not resolved_path.is_file():
        raise InputValidationError(f"Input path is not a file: {input_path}")
    return resolved_path
