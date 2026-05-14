"""Session loading utilities for lecture media ingestion.

This module builds a lecture session from one file, many files, or a
directory of media files. The current prototype keeps ordering deterministic:
explicit caller-provided paths are preserved as-is, while files discovered
inside a directory are appended in sorted filename order.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Sequence

from lecture_analyzer.core.config import PipelineConfig
from lecture_analyzer.core.errors import (
    EmptyInputError,
    MissingInputError,
    UnsupportedMediaError,
)
from lecture_analyzer.core.models import InputSource, LectureSession
from lecture_analyzer.core.types import MediaType


class SessionLoader:
    """Load lecture inputs into a deterministic, traceable session object."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def load_session(
        self,
        input_paths: str | Path | Sequence[str | Path],
        session_id: str | None = None,
    ) -> LectureSession:
        """Create a lecture session from files or directories.

        Ordering rules are intentionally simple and predictable:
        caller-provided paths keep their original order, while directory
        contents are expanded using sorted filename order. Unsupported files
        found in a directory are ignored and recorded in session metadata.
        """

        discovered_paths, ignored_paths = self._resolve_input_paths(input_paths)
        input_sources = self._build_input_sources(discovered_paths)
        if not input_sources:
            raise EmptyInputError(
                "No supported lecture media files were found in the provided input.",
            )

        session = LectureSession(
            session_id=session_id or self._build_session_id(input_sources),
            input_sources=input_sources,
            language_codes=self._default_language_codes(),
        )
        if ignored_paths:
            # Keep ignored files visible in metadata so directory ingestion
            # remains auditable without interrupting the happy path.
            session.metadata["ignored_inputs"] = ignored_paths
        return session

    def detect_media_type(self, path: Path) -> MediaType:
        """Classify a path as audio, video, or unsupported.

        Detection uses file extensions first because it is simple and easy to
        replace later. A lightweight MIME-type guess is used only as fallback.
        """

        suffix = path.suffix.lower()
        if suffix in self.config.audio_extensions:
            return MediaType.AUDIO
        if suffix in self.config.video_extensions:
            return MediaType.VIDEO

        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type:
            if mime_type.startswith("audio/"):
                return MediaType.AUDIO
            if mime_type.startswith("video/"):
                return MediaType.VIDEO
        return MediaType.UNSUPPORTED

    def _resolve_input_paths(
        self,
        input_paths: str | Path | Sequence[str | Path],
    ) -> tuple[list[Path], list[str]]:
        """Expand input files and directories into a deterministic file list."""

        raw_paths = self._coerce_input_paths(input_paths)
        resolved_paths: list[Path] = []
        ignored_paths: list[str] = []

        for raw_path in raw_paths:
            path = raw_path.expanduser().resolve()
            if not path.exists():
                raise MissingInputError(f"Input path does not exist: '{path}'.")
            if path.is_dir():
                # Directories expand in a deterministic way, while explicit
                # unsupported files still fail fast because the caller named
                # them intentionally.
                directory_paths, directory_ignored = self._collect_directory_files(path)
                resolved_paths.extend(directory_paths)
                ignored_paths.extend(directory_ignored)
                continue

            media_type = self.detect_media_type(path)
            if media_type == MediaType.UNSUPPORTED:
                raise UnsupportedMediaError(
                    f"Unsupported input file type for '{path.name}'.",
                )
            resolved_paths.append(path)

        if not resolved_paths and ignored_paths:
            raise EmptyInputError(
                "The provided directory does not contain supported audio or video files.",
            )
        return resolved_paths, ignored_paths

    def _collect_directory_files(
        self,
        directory_path: Path,
    ) -> tuple[list[Path], list[str]]:
        """Return supported files from a directory in sorted filename order.

        The current prototype scans only the top level of the directory to keep
        behavior explicit and easy to reason about.
        """

        supported_paths: list[Path] = []
        ignored_paths: list[str] = []

        for child in sorted(
            directory_path.iterdir(),
            key=lambda item: item.name.lower(),
        ):
            if not child.is_file():
                continue

            media_type = self.detect_media_type(child)
            if media_type == MediaType.UNSUPPORTED:
                if self._is_generated_artifact(child):
                    # Ignore known pipeline sidecars so reprocessing a media
                    # directory does not accidentally treat cache files as input.
                    continue
                ignored_paths.append(str(child))
                continue
            supported_paths.append(child.resolve())

        return supported_paths, ignored_paths

    def _is_generated_artifact(self, path: Path) -> bool:
        """Return whether a path matches a known pipeline sidecar artifact."""

        manifest_extension = self.config.transcription_cache_manifest_extension
        if path.name.endswith(manifest_extension):
            return True
        if path.name.endswith(self.config.normalized_audio_metadata_extension):
            return True

        if path.suffix.lower() != self.config.transcription_cache_text_extension:
            return False

        for extension in (
            *self.config.audio_extensions,
            *self.config.video_extensions,
        ):
            if path.with_suffix(extension).exists():
                return True
        return False

    def _build_input_sources(self, paths: Sequence[Path]) -> list[InputSource]:
        """Build ordered input source objects from resolved file paths."""

        input_sources: list[InputSource] = []
        for index, path in enumerate(paths, start=1):
            media_type = self.detect_media_type(path)
            if media_type == MediaType.UNSUPPORTED:
                continue

            input_sources.append(
                InputSource(
                    # Source identifiers are deterministic within a session and
                    # later reused as anchors for audio and transcript objects.
                    source_id=f"source_{index:03d}",
                    original_path=path,
                    media_type=media_type,
                    order_index=index,
                    original_filename=path.name,
                ),
            )
        return input_sources

    @staticmethod
    def _coerce_input_paths(
        input_paths: str | Path | Sequence[str | Path],
    ) -> list[Path]:
        """Normalize supported input path shapes into a list of paths."""

        if isinstance(input_paths, (str, Path)):
            return [Path(input_paths)]
        return [Path(path) for path in input_paths]

    @staticmethod
    def _build_session_id(input_sources: Sequence[InputSource]) -> str:
        """Build a deterministic session identifier from the input sources."""

        first_stem = (
            input_sources[0].original_path.stem if input_sources else "session"
        )
        return f"{first_stem}_{len(input_sources)}files"

    def _default_language_codes(self) -> list[str]:
        """Return the configured session-level language codes."""

        language_codes = [
            code.strip()
            for code in self.config.default_language.split("-")
            if code.strip()
        ]
        return language_codes or [self.config.default_language]
