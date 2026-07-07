# Repository Status

> **Prototype frozen (2026-07-05).** Extraction development is closed at cycle
> R10.1. Final quality snapshot: `docs/quality_evaluation.md` (Final Prototype
> Snapshot) and `docs/prototype_closure_report.md`. Development history:
> `PROJECT_DIARY.md`. Ready-to-use prompts for future cycles:
> `docs/decision_plan_2026-07-03.md`.

## Current source of truth

The official lecture-processing pipeline is now source-owned under
`src/lecture_analyzer`:

- `src/lecture_analyzer/core/`
- `src/lecture_analyzer/input/`
- `src/lecture_analyzer/preprocessing/`
- `src/lecture_analyzer/transcription/`
- `src/lecture_analyzer/analysis/`
- `src/lecture_analyzer/output/`

This is the real, functional pipeline used by the project today.

## Import status

The repository now exposes a single implementation-owning import surface:

1. The src-owned implementation under `src/lecture_analyzer`.

The following public namespaces are available and stable:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

The former root package imports have been removed as part of the repository
cleanup. Public and internal imports should use `lecture_analyzer.*`.

## CLI status

- The installable `lecture-analyzer` command is the official CLI.
- The real processing pipeline now lives in `src/lecture_analyzer`.
- The root-level `main.py` remains a temporary compatibility wrapper.
- Smoke mode still uses the lightweight placeholder flow under the same CLI.

## Docker status

The Docker setup is aligned with the current repository shape:

- it copies the src-owned implementation packages
- it installs the `lecture-analyzer` CLI from `src/lecture_analyzer`
- it supports `lecture-analyzer --help`
- it supports the temporary smoke mode

Docker is still not a promise that every heavy optional ML branch is available
out of the box. Those branches continue to depend on their specific external
libraries and models.

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

## Import guidance

During the current phase:

- use `lecture_analyzer.*` for package-facing imports
- treat `main.py` as a temporary CLI convenience wrapper only
- keep runtime changes conservative while packaging and docs settle around the
  src-owned structure

## Next migration phase

The current cleanup phase is focused on stabilizing the src-owned structure.
Recommended order:

1. stabilize the consolidated src-owned packages
2. simplify CLI and packaging around the single import surface
3. prune remaining optional or placeholder branches that are no longer needed
4. retire `main.py` when external workflows no longer need the compatibility
   entry point
