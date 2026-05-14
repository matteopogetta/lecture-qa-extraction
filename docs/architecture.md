# Architecture

## Overview

This prototype stays independent from ExerPlaza and focuses on producing
structured outputs that can be integrated later through a dedicated connector.

The repository is now in a consolidated src-based phase:

- the real implementation lives under `src/lecture_analyzer`
- root packages are kept as temporary legacy wrappers
- the two import surfaces remain intentionally aligned during the transition

The goal of the current phase is to stabilize the consolidated structure before
removing the temporary wrappers.

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

Temporary legacy wrapper surface:

- `core.*`
- `input.*`
- `preprocessing.*`
- `transcription.*`
- `analysis.*`
- `output.*`

The src-facing packages should be treated as the stable public namespace. The
root packages should be treated as temporary compatibility wrappers only.

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
- root `output.*` imports remain available as legacy wrappers

## Future ExerPlaza connector

The future ExerPlaza connector will consume the structured outputs produced by
the repository. The connector should remain a separate integration layer so
that the prototype can evolve independently without inheriting platform
coupling too early.

## Next cleanup order

After consolidation, the recommended cleanup order is:

1. `core`
2. `input`
3. `preprocessing`
4. `transcription`
5. `analysis`
6. `output`

This keeps wrapper retirement aligned with the dependency flow and minimizes
the chance of broad follow-up refactors.
