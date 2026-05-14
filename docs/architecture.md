# Architecture

## Overview

This prototype is intentionally platform-neutral and focuses on producing
structured outputs that can be integrated later into downstream educational
workflows.

The repository is now in a consolidated src-based phase:

- the real implementation lives under `src/lecture_analyzer`
- `lecture_analyzer.*` is the stable public namespace
- the root package wrappers have been removed

The goal of the current phase is to stabilize the consolidated structure with a
single import surface.

## Package layout after consolidation

Implementation-owning packages:

- `src/lecture_analyzer/core/`
- `src/lecture_analyzer/input/`
- `src/lecture_analyzer/preprocessing/`
- `src/lecture_analyzer/transcription/`
- `src/lecture_analyzer/analysis/`
- `src/lecture_analyzer/output/`

Public src-facing package surface:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

The src-facing packages should be treated as the stable public namespace.

## Input layer

The input layer is responsible for validating local media paths and exposing a
stable hand-off to the rest of the pipeline.

## Preprocessing layer

The preprocessing layer converts input media into analysis-ready audio assets.

## Transcription layer

The transcription layer transforms normalized audio into text segments with
timing metadata and optional alignment or diarization side outputs.

## Analysis layer

The analysis layer reconstructs sentence-level structure, segments the lecture,
and generates candidate question-answer pairs.

## Output layer

The output layer produces structured artifacts for downstream systems.

Current production responsibilities:

- deterministic JSON export
- optional debug Excel export
- sentence provenance validation helpers

Current smoke-mode behavior:

- writes a minimal JSON placeholder file through
  `src/lecture_analyzer/output/writer.py`

Current separation after consolidation:

- production exporters live under `src/lecture_analyzer/output`
- smoke-mode helpers remain separate in `src/lecture_analyzer/output/writer.py`

## Future platform integration

Future educational platform integrations can consume the structured outputs
produced by the repository. That integration layer should remain separate so
the prototype can evolve independently without inheriting platform coupling
too early.

## Next cleanup order

After consolidation, the recommended cleanup order is:

1. `core`
2. `input`
3. `preprocessing`
4. `transcription`
5. `analysis`
6. `output`

This keeps the remaining cleanup aligned with the dependency flow and
minimizes the chance of broad follow-up refactors.
