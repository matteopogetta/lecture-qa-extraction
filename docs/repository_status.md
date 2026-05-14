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

## Bridge migration status

The repository now contains two aligned import surfaces:

1. The real implementation in the root modules listed above.
2. A src-based compatibility surface under `src/lecture_analyzer`.

The following src namespaces are now available and intentionally mirror the
root packages:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

These namespaces currently work as compatibility bridges. They expose the real
root modules without moving the implementation yet.

## Why root packages still own the implementation

The root packages remain the temporary source of truth on purpose:

- they are the runtime path already exercised by the real pipeline
- they are the implementation path already covered by the full test suite
- they let the project adopt stable `lecture_analyzer.*` imports before any
  physical relocation happens
- they reduce migration risk by separating namespace stabilization from code
  movement

This means the repository is already src-addressable from the outside while the
actual implementation still lives in the root packages.

## CLI status

- The root-level `main.py` remains the temporary wrapper entry point.
- The installable `lecture-analyzer` command is the official CLI.
- The real processing pipeline is still the one implemented in the root
  modules.
- The CLI now routes through the transitional `src/lecture_analyzer` package
  while keeping the root implementation unchanged.

## Docker status

The Docker setup is now aligned with the real project shape at a transitional
level:

- it copies the root implementation packages
- it installs the `lecture-analyzer` CLI from `src/lecture_analyzer`
- it supports `lecture-analyzer --help`
- it supports the temporary smoke mode

It is still transitional because the implementation has not yet been moved
physically into `src/lecture_analyzer`.

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

## How to use imports during the bridge phase

During this phase:

- keep existing runtime code stable unless there is a clear migration task
- prefer `lecture_analyzer.*` for new package-facing imports when practical
- keep `core.*`, `input.*`, `preprocessing.*`, `transcription.*`,
  `analysis.*`, and `output.*` as the implementation-owning modules until the
  physical consolidation phase starts

## Next migration phase

The next phase is physical consolidation, not another compatibility layer.
Recommended order:

1. `core`
2. `input`
3. `preprocessing`
4. `transcription`
5. `analysis`
6. `output`

This order keeps the migration aligned with the pipeline flow and lets the
lowest-level orchestration modules move before the broader downstream layers.
