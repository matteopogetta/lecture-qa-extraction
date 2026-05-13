# Repository Status

## Current source of truth

The official lecture-processing pipeline currently lives in the root modules:

- `core/`
- `input/`
- `preprocessing/`
- `transcription/`
- `analysis/`
- `output/`

This is the real, functional pipeline used by the project today.

## Current repository split

The repository currently contains two parallel structures:

1. The real pipeline in the root modules listed above.
2. A bootstrap/package skeleton under `src/lecture_analyzer`.

At the moment, `src/lecture_analyzer` should not be treated as the main product
implementation. It is still a bootstrap-oriented package skeleton and a future
destination for the real pipeline, not the current source of truth.

## CLI status

- The root-level `main.py` remains the temporary wrapper entry point.
- The real processing pipeline is still the one implemented in the root
  modules.
- The `lecture-analyzer` package entry point currently reflects the bootstrap
  split and legacy compatibility layer rather than a fully migrated src-based
  architecture.

## Docker status

The current Docker setup is still bootstrap-oriented:

- `Dockerfile`
- `docker-compose.yml`

They are useful for repository bootstrapping and packaging experiments, but
they are not yet aligned with the real pipeline as the primary product path.

## Local and non-essential materials

The following directories and artifacts should be treated as local or
non-essential repository materials:

- `artifacts/`
- `artifacts_*/`
- `sample_inputs/`
- `tmp/`
- transcription cache sidecars such as `.txt` and `.transcription.json`
- generated debug workbooks such as `.xlsx`

These files are helpful for experimentation, manual review, and local reruns,
but they are not required as versioned source code.

## Target architectural direction

The official target pipeline remains sentence-centric. The intended dominant
flow is:

`input -> audio normalization -> transcription -> alignment -> sentence reconstruction -> segmentation -> QA extraction -> JSON output`

The migration toward `src/lecture_analyzer` will happen gradually after this
cleanup and repository-preparation phase.
