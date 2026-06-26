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
- `quality`: semantic QA and alignment without diarization
- `quality_local`: guarded local QA and alignment without diarization or semantic reranking
- `current`: current default behavior
- `diagnostic`: richest debug/review mode

Use `full` only when the heavier optional branches are intentionally being
evaluated.

Current experimental default:

- prefer `quality_local` for sentinel QA/C experiments when privacy, runtime,
  and low maintenance are the priority
- compare against `quality` only on selected sentinels when testing whether
  semantic retrieval/reranking buys enough quality to justify the added time
- avoid broad full-batch reviews until sentinel runs show a clear quality gain

The `quality_local` guardrails are intentionally precision-oriented. They
penalize or filter low-information classroom check-ins, numeric polls, fragment
questions without a real interrogative head, declarative tag questions such as
standalone `right?` prompts, question-like answers, and same-sentence answer
echoes.

The 2026-06-24 QA extractor cycle also uses the candidate-level
`quality_features` as a compact final gate for `quality_local` only. This gate
rejects combinations of high risk and low final quality, especially implicit
statement-like questions with surface answer cues, embedded polling/check-in
noise, and incomplete answer spans. The gate reads existing candidate metadata;
it does not introduce a second candidate store or new output schema.

Answer spans may be lightly normalized before scoring: short meta openings such
as thanks/good-question acknowledgements are trimmed when substantive answer
text immediately follows. Standalone meta or boilerplate answers remain
penalized as non-substantive answers.

The lexical alignment used by local QA intentionally ignores weak function-like
overlap such as pronouns and discourse connectors. Answer cues such as
`because` or `so` are helpful only when the answer also carries topical signal;
surface cues without content are penalized. Procedural Q&A-turn management
questions, for example asking to ask a question or to scroll to another
question, are treated as non-didactic noise for `quality_local`.

Guardrails should generalize from failure categories rather than memorizing
sentinel examples. Prefer structural signals such as question-head presence,
token count, numeric option patterns, answer/question overlap, timing, and
grounding quality over exact phrase blacklists.

The QA extractor records a `question_intent` diagnostic for candidates, using
structural labels such as `information_seeking`, `embedded_statement_question`,
`rhetorical_tag`, `poll_or_check`, `fragment`, and `weak_question_form`.
`quality_local` treats weak or embedded question intent as a reason not to run
far deferred answer search.

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
  objective counts, aggregated QA quality diagnostics, timing, cache/reuse,
  and privacy metadata

QA quality diagnostics are intentionally split across two levels:

- candidate-level `session.json` metadata contains compact
  `quality_features` for each QA/C candidate
- run-level `metrics.json` contains only aggregated `qa_quality_metrics`

`metrics.json` must not duplicate full candidates, question/answer text, or
debug blocks. Use `session.json` as the granular source of truth when a single
candidate needs inspection.

`qa_quality_metrics.run_quality_signal` is a compact diagnostic run-level
indicator derived from aggregate quality distribution, useful yield, and risk.
It is not used to filter or rank candidates.

`answer_responsiveness_score` is a compact local diagnostic inside
`quality_features` and an aggregate distribution inside `qa_quality_metrics`.
It measures whether the selected answer has topical, numeric, or substantive
support for the question. It is intentionally not a standalone hard filter:
low lexical overlap can still be valid for contextual follow-ups and natural
explanatory answers.

Question-autonomy and echo diagnostics are also exported as compact risk
reasons. `low_autonomy_implicit_question` marks implicit cue questions without a
question mark when sentence/merge metadata suggests the cue was extracted from a
weak fragment or run-on. `circular_answer_echo` marks answers that cover most of
the question while adding too little new information.

Sentence-level cleanup diagnostics are conservative metadata, not transcript
rewrites. Reconstructed sentences may expose `metadata.semantic_cleanup` with
`sentence_autonomy_score`, `boundary_confidence_score`, and
`continuation_risk_score`. QA extraction can use these scores as light evidence
when deciding whether a sentence is autonomous enough to seed a question.

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

The batch runner's `--resume` mode skips only runs with a compatible profile,
segmentation mode, successful stages, and matching code snapshot. The snapshot
includes a Git worktree content hash so dirty, uncommitted heuristic changes do
not accidentally reuse older evaluation runs.

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
