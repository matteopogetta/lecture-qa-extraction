# Architecture

## Overview

This prototype stays independent from ExerPlaza and focuses on producing
structured outputs that can be integrated later through a dedicated connector.

## Input layer

The input layer is responsible for validating local media paths and exposing a
stable hand-off to the rest of the pipeline.

Future responsibilities:

- file type inspection
- batch input handling
- metadata collection

## Preprocessing layer

The preprocessing layer will convert input media into analysis-ready assets.

Future responsibilities:

- audio extraction from video
- loudness normalization
- temporary artifact management

## Transcription layer

The transcription layer will transform normalized audio into text segments with
timing metadata.

Future responsibilities:

- speech-to-text provider integration
- model selection
- transcription caching

## Analysis layer

The analysis layer will detect didactic segments and generate candidate
question-answer pairs.

Future responsibilities:

- segment boundary detection
- educational topic grouping
- question and answer candidate generation

## Output layer

The output layer produces structured artifacts for downstream systems.

Current bootstrap behavior:

- writes a minimal JSON placeholder file

Future responsibilities:

- validated JSON schemas
- richer metadata
- export adapters for future consumers

## Future ExerPlaza connector

The future ExerPlaza connector will consume the structured outputs produced by
this repository. The connector should remain a separate integration layer so
that the prototype can evolve independently without inheriting platform
coupling too early.
