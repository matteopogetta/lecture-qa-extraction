# QA/C Quality Evaluation

## Goal

The project optimizes for high-quality lecture QA/C extraction in the shortest
reasonable runtime.

QA/C means:

- question: a useful didactic question candidate
- answer: a grounded answer candidate
- context: nearby lecture context that makes the pair understandable

## Evaluation Priorities

The main evaluation dimensions are:

- semantic quality of the question
- correctness and completeness of the answer
- usefulness of the context
- grounding in transcript, timing, and provenance
- runtime and implementation complexity
- local-first execution
- open-source and stable dependencies
- no paid APIs, required tokens, or remote data transfer by default

At equal extraction quality, prefer the faster and simpler pipeline profile.

## Pipeline Profiles To Compare

Recommended comparisons:

- `light`: fastest local baseline
- `current`: current default behavior
- `diagnostic`: richest debug/review mode

Use `full` only when the heavier optional branches are intentionally being
evaluated.

## AI/Human Review Packet

The pipeline can write a local Markdown review packet with:

- reviewer instructions
- transcript text
- QA/C candidates
- confidence and provenance fields
- timing summary
- expected structured JSON response shape

This packet is generated locally. The project does not send it to any external
service. If a human reviewer chooses to paste the packet into a third-party
chatbot, that is a manual external review step and should use non-sensitive
data unless privacy rules allow otherwise.

Example:

```bash
.venv/bin/lecture-analyzer /path/to/input.mp3 \
  --output /path/to/output/session.json \
  --pipeline-profile light \
  --export-ai-review-packet \
  --ai-review-packet-path /path/to/output/review_packets
```

The `diagnostic` profile enables review-packet export by default.

## Persistent Evaluation Runs

Use evaluation runs when you want a stable local history of extraction quality
for one input over time.

Default local root:

```text
evaluations/
```

This directory is ignored by Git and should not be pushed to GitHub. You can
change it with `--evaluation-root`.

Run layout:

```text
evaluations/
  ICWROS/
    runs/
      2026-06-21_light/
        session.json
        review_packet.md
        ai_review.json
        metrics.json
```

The run folder name is generated automatically from UTC timestamp, pipeline
profile, and segmentation mode, for example:

```text
2026-06-21_143022_light_structural
```

Files:

- `session.json`: exact pipeline JSON for the run
- `review_packet.md`: transcript plus QA/C candidates and review instructions
- `ai_review.json`: place to paste the external AI or human review response
- `metrics.json`: identity, paths, code snapshot, pipeline parameters,
  objective counts, timing, cache/reuse, and privacy metadata

Example:

```bash
.venv/bin/lecture-analyzer /path/to/input.mp3 \
  --output /path/to/output/session.json \
  --pipeline-profile light \
  --export-evaluation-run \
  --evaluation-root evaluations \
  --evaluation-input-label ICWROS
```

If a run folder already exists, the exporter appends a numeric suffix instead
of overwriting it.

`--evaluation-run-label` exists only as an advanced override. Normal runs
should omit it so the system creates a unique label automatically.

The project never sends `review_packet.md` or `ai_review.json` to an external
service. External AI review is a manual step: paste the packet into the chosen
tool only when privacy rules allow it, then paste the returned structured JSON
into `ai_review.json`.

## Evaluation Comparison

After one or more runs for the same input have a populated `ai_review.json`,
generate comparison files from the input evaluation directory:

```bash
.venv/bin/lecture-analyzer-compare-evaluations evaluations/icwros
```

This writes:

```text
evaluations/
  icwros/
    comparison.json
    comparison.md
```

`comparison.json` is the structured machine-readable comparison. `comparison.md`
is the human-readable summary.

The comparison tool records two runtime concepts:

- `observed_runtime_seconds`: the time recorded by the run as it actually
  executed, including cache and artifact reuse
- `cold_equivalent_runtime_seconds`: the estimated time the same run would have
  needed if reused stages had been recomputed

Cold-equivalent time is reconstructed stage by stage. If a run reused
`transcription`, `diarization`, `alignment`, or another stage, the comparison
tool searches the other runs for the same input and borrows a real observed
cold duration for that stage.

Reference priority:

1. same stage + same pipeline profile + same segmentation mode
2. same stage + same pipeline profile
3. same stage across the same input

If no cold reference exists for a reused stage, the cold estimate is marked
incomplete. In that case:

- `cold_equivalent_runtime_seconds` is `null`
- `cold_equivalent_known_seconds` is a lower bound
- `cold_equivalent_missing_reused_stages` lists the missing stage references

For reliable benchmark comparisons, save at least one `--force-recompute`
evaluation run for each profile/mode you want to compare. Warm/cache runs can
then reuse those cold stage costs without pretending that cache hits were free
in a real first run.

## Suggested Review Loop

1. Run `light` and save an evaluation run.
2. Run `current` with the same input and save another evaluation run.
3. Populate each `ai_review.json` with human or external AI review.
4. Run `lecture-analyzer-compare-evaluations evaluations/<input-label>`.
5. Compare quality, QA/C count, observed runtime, and cold-equivalent runtime.
6. Keep the simpler profile unless the heavier profile clearly improves quality.
7. Record recurring failure modes before changing extraction logic.
