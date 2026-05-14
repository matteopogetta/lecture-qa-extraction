"""JSON export utilities with deterministic, session-derived filenames.

The exporter derives a stable output base name from the original lecture input:
- single input source: use the original file stem
- multi-input session: use the session identifier

The segmentation mode is always appended as a suffix so structural, windowed,
and adaptive outputs never overwrite each other. Re-running the same session
and mode overwrites the same deterministic file, while unrelated sessions
resolve to different target paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.models import LectureSession


class JsonExporter:
    """Export processed session data to deterministic JSON paths."""

    _SAFE_NAME_RE = re.compile(r"[^a-z0-9]+")

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def export(
        self,
        session: LectureSession,
        output_path: str | Path | None = None,
        segmentation_mode: str | None = None,
    ) -> Path:
        """Write one session JSON using a derived filename policy."""

        mode = self._normalize_mode(
            segmentation_mode or str(session.metadata.get("segmentation_mode", "")),
        )
        target_path = self.build_output_path(
            session=session,
            output_path=output_path,
            segmentation_mode=mode,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            # Export the session exactly as the models serialize it so JSON
            # output remains a faithful external view of in-memory state.
            json.dumps(
                session.to_dict(),
                indent=self.config.export_indent,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return target_path

    def export_many(
        self,
        sessions_by_mode: dict[str, LectureSession],
        output_path: str | Path | None = None,
    ) -> dict[str, Path]:
        """Write one JSON file for each segmentation mode in one run."""

        exported_paths: dict[str, Path] = {}
        for mode, session in sessions_by_mode.items():
            # Normalize the requested mode before naming the file so invalid
            # caller input cannot produce inconsistent filenames.
            normalized_mode = self._normalize_mode(mode)
            exported_paths[normalized_mode] = self.export(
                session=session,
                output_path=output_path,
                segmentation_mode=normalized_mode,
            )
        return exported_paths

    def build_output_path(
        self,
        session: LectureSession,
        output_path: str | Path | None,
        segmentation_mode: str,
    ) -> Path:
        """Return the deterministic export path for one session and mode."""

        output_directory = self._resolve_output_directory(output_path)
        base_name = self.build_base_output_name(session)
        filename = f"{base_name}_{self._normalize_mode(segmentation_mode)}.json"
        return (output_directory / filename).expanduser().resolve()

    def build_base_output_name(self, session: LectureSession) -> str:
        """Return a stable, filesystem-safe base name for session export.

        Single-file sessions use the original input filename stem. Multi-file
        sessions use the session identifier because it is already deterministic
        and avoids ambiguous filenames when the lecture spans many inputs.
        """

        if len(session.input_sources) == 1:
            source_name = session.input_sources[0].original_path.stem
        else:
            source_name = session.session_id
        return self._sanitize_name(source_name)

    def _resolve_output_directory(self, output_path: str | Path | None) -> Path:
        """Resolve the directory used for derived export filenames.

        When a JSON file path is provided, only its parent directory is used.
        The final filename is still derived from the session and mode so output
        naming stays deterministic and traceable to the lecture source.
        """

        if output_path is None:
            return self.config.working_directory

        resolved_path = Path(output_path).expanduser()
        if resolved_path.suffix.lower() == ".json":
            return resolved_path.parent.resolve()
        return resolved_path.resolve()

    def _sanitize_name(self, value: str) -> str:
        """Return a conservative filesystem-safe output base name."""

        normalized = value.strip().lower()
        normalized = self._SAFE_NAME_RE.sub("_", normalized)
        normalized = normalized.strip("_")
        return normalized or "session"

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        """Normalize an export mode name with a safe structural fallback."""

        normalized = mode.strip().lower()
        if normalized in {"structural", "windowed", "adaptive"}:
            return normalized
        return "structural"
