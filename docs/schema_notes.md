# Lecture Session JSON Schema Notes

This document describes the current JSON-oriented output structure exported by
the lecture-processing pipeline.

## Top-level sections

- `session_metadata`
- `input_sources`
- `audio_sources`
- `transcript`
- `transcript_chunks`
- `aligned_transcripts`
- `diarization_segments`
- `utterances`
- `sentences`
- `segments`
- `speaker_role_estimates`
- `qa_candidates`
- `pipeline_timing`

## Current assumptions

- The internal pipeline is audio-based after normalization.
- Video is accepted only as an input format and is converted to audio before
  transcription.
- A lecture session may span multiple input files and multiple normalized
  audio sources.
- Alignment, diarization, utterances, and sentences are stored as separate
  layers so later stages can choose the most appropriate evidence source.
- Segmentation is sentence-centric and falls back to the merged transcript only
  when sentence reconstruction is unavailable.

## Pipeline timing

Tracked stages include:

- `session_loading`
- `audio_normalization`
- `transcription`
- `alignment`
- `utterance_building`
- `diarization`
- `speaker_attribution`
- `sentence_reconstruction`
- `transcript_post_processing`
- `transcript_segmentation`
- `qa_extraction`
- `json_export`
- `total_pipeline_execution`

Each stage record includes:

- `stage_name`
- `status`
- `started_at`
- `ended_at`
- `duration_seconds`
- `note`
- `metadata`

## Current limitations

- This is a prototype contract rather than a formal JSON Schema.
- Duration metadata remains partially optional when upstream media metadata is
  unavailable.
- Speaker-role estimation is still placeholder-level.
- Heavy optional branches such as alignment and diarization continue to depend
  on their own runtime stacks.
