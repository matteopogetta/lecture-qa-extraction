# Simplification Plan

## A. Official target pipeline

The official target pipeline for this project is the real root-based pipeline:

`input -> audio normalization -> transcription -> alignment -> sentence reconstruction -> segmentation -> QA extraction -> JSON output`

This is the project center that future packaging and src-based migration should
serve.

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
- `analysis/speaker_role.py` if it remains unintegrated
- heavy `sample_inputs/` media tracked in the repository
- tracked `artifacts/` and cache artifacts in the repository
- Docker setup that only reflects the bootstrap-oriented flow

## E. Next migration steps

The repository cleanup and bridge phase is now complete. The next steps should
move from namespace compatibility to physical consolidation.

Recommended order for the next repository phases:

1. repository cleanup
2. real pytest coverage as the default expectation
3. official CLI path aligned to the real pipeline
4. bridge-based `lecture_analyzer.*` namespace stabilization
5. physical consolidation of `core` into `src/lecture_analyzer`
6. physical consolidation of `input` and `preprocessing`
7. physical consolidation of `transcription`
8. physical consolidation of `analysis` and `output`
9. final removal of redundant root-package bridges
10. Docker and packaging cleanup after consolidation

## F. Bridge phase outcome

The bridge phase now exposes all major namespaces under `lecture_analyzer.*`
without changing the runtime source of truth:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

This creates a stable import surface for downstream code and packaging while
keeping the real implementation in the root packages until the next phase.
