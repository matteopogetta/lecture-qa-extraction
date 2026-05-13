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

Recommended order for the next repository phases:

1. repository cleanup
2. real pytest coverage as the default expectation
3. official CLI path aligned to the real pipeline
4. migration of `core` into `src`
5. migration of `input` and `preprocessing`
6. migration of `transcription`
7. migration of `analysis` and `output`
8. Docker realignment
9. stable GitHub push
