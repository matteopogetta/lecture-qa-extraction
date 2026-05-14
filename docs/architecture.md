# Architecture

## Overview

This prototype stays independent from ExerPlaza and focuses on producing
structured outputs that can be integrated later through a dedicated connector.

The repository is currently in a bridge-based src migration phase:

- the real implementation still lives in the root packages
- `src/lecture_analyzer` now exposes a matching import surface
- the two layers are intentionally connected by compatibility bridges

The goal of this phase is to stabilize package boundaries before physically
moving implementation files.

## Package layout during the bridge phase

Current implementation-owning packages:

- `core/`
- `input/`
- `preprocessing/`
- `transcription/`
- `analysis/`
- `output/`

Current src-facing package surface:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

For now, the src-facing packages are compatibility bridges. They should be
treated as the stable public namespace, while the root packages still own the
runtime implementation.

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

Future responsibilities after consolidation:

- move output implementation under `src/lecture_analyzer/output`
- keep smoke-mode helpers compatible
- continue separating production exporters from smoke-only helpers

## Future ExerPlaza connector

The future ExerPlaza connector will consume the structured outputs produced by
the repository. The connector should remain a separate integration layer so
that the prototype can evolve independently without inheriting platform
coupling too early.

## Recommended consolidation order

Once the physical migration starts, the recommended order is:

1. `core`
2. `input`
3. `preprocessing`
4. `transcription`
5. `analysis`
6. `output`

This ordering keeps the move aligned with the runtime dependency flow and
minimizes the chance of large, cross-cutting refactors.
