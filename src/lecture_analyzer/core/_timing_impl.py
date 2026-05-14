"""Lightweight helpers for operational pipeline timing."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Iterator

from lecture_analyzer.core.models import PipelineStageTiming, PipelineTiming


def utc_now_iso() -> str:
    """Return the current UTC time formatted as an ISO-8601 string."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class PipelineTimer:
    """Collect ordered stage timings using a monotonic clock for durations."""

    def __init__(self, report: PipelineTiming | None = None) -> None:
        self.report = report or PipelineTiming()

    @contextmanager
    def measure(
        self,
        stage_name: str,
        *,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[PipelineStageTiming]:
        """Measure one stage and append its timing record on exit."""

        stage = PipelineStageTiming(
            stage_name=stage_name,
            status="running",
            started_at=utc_now_iso(),
            note=note,
            metadata=dict(metadata or {}),
        )
        started_monotonic = monotonic()
        try:
            yield stage
        except Exception as error:
            if stage.status == "running":
                stage.status = "failed"
            if stage.note is None:
                stage.note = str(error) or "stage_failed"
            self._finalize_stage(stage, started_monotonic)
            raise
        else:
            if stage.status == "running":
                stage.status = "executed"
            self._finalize_stage(stage, started_monotonic)

    def record(
        self,
        stage_name: str,
        *,
        status: str,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        ended_at: str | None = None,
        duration_seconds: float = 0.0,
        used_cache: bool = False,
        used_existing_artifact: bool = False,
        forced_recompute: bool = False,
    ) -> PipelineStageTiming:
        """Append a precomputed stage timing record to the report."""

        resolved_finished_at = finished_at or ended_at or utc_now_iso()
        stage = PipelineStageTiming(
            stage_name=stage_name,
            status=status,
            started_at=started_at or resolved_finished_at,
            finished_at=resolved_finished_at,
            ended_at=resolved_finished_at,
            duration_seconds=round(max(0.0, float(duration_seconds)), 6),
            used_cache=used_cache,
            used_existing_artifact=used_existing_artifact,
            forced_recompute=forced_recompute,
            note=note,
            metadata=dict(metadata or {}),
        )
        self.report.stages.append(stage)
        self.report.refresh_summary()
        return stage

    def _finalize_stage(
        self,
        stage: PipelineStageTiming,
        started_monotonic: float,
    ) -> None:
        """Freeze the stage duration and append it to the report."""

        finished_at = utc_now_iso()
        stage.finished_at = finished_at
        stage.ended_at = finished_at
        stage.duration_seconds = round(max(0.0, monotonic() - started_monotonic), 6)
        self.report.stages.append(stage)
        self.report.refresh_summary()
