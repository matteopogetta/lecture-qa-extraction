# Simplification Plan

## A. Official target pipeline

The official target pipeline for this project is now the src-owned pipeline:

`input -> audio normalization -> transcription -> alignment -> sentence reconstruction -> segmentation -> QA extraction -> JSON output`

This is the project center that packaging, CLI, and cleanup should now serve.

## B. Core branches to keep

The following components are currently part of the core pipeline and should be
treated as essential:

- input loading and media-type detection
- audio normalization and normalized-audio reuse
- transcription and structured transcription cache manifests
- transcript merge and conservative transcript normalization
- alignment-backed utterance construction
- sentence reconstruction as the preferred analysis layer
- structural segmentation
- QA extraction over the sentence-centric pipeline
- JSON export

## C. Optional branches

The following branches are useful but should be treated as optional rather than
defining the center of the project:

- diarization
- semantic QA retrieval and reranking
- Excel debug export
- adaptive, windowed, and `both` segmentation modes
- text-only transcription cache fallback
- `disable-alignment` compatibility mode

## D. Removal or deprecation candidates

The following areas are good candidates for deprecation, isolation, or removal
in later cleanup steps:

- bootstrap placeholder as an autonomous pipeline
- `src/lecture_analyzer/analysis/speaker_role.py` if it remains unintegrated
- heavy `sample_inputs/` media tracked in the repository
- tracked `artifacts/` and cache artifacts in the repository
- `main.py` once downstream workflows no longer need the compatibility entry
  point

## E. Next migration steps

Repository cleanup, bridge stabilization, and physical consolidation are now
complete. The next steps should focus on reducing temporary compatibility
surface area.

Recommended order for the next repository phases:

1. repository cleanup
2. real pytest coverage as the default expectation
3. official CLI path aligned to the real pipeline
4. bridge-based `lecture_analyzer.*` namespace stabilization
5. physical consolidation into `src/lecture_analyzer`
6. compatibility validation across CLI and src imports
7. final removal of redundant root-package wrappers
8. Docker and packaging cleanup after wrapper retirement

## F. Consolidation outcome

The project now exposes all major namespaces under `lecture_analyzer.*`, and
those namespaces are the implementation-owning source of truth:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

The legacy root packages have been removed. The repository now keeps a single
implementation-owning namespace under `lecture_analyzer.*`.
