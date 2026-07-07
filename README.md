# Lecture QA Extraction

## Quick Docker Demo

Recommended for professors, colleagues, and quick evaluation.

Minimum requirements:

- Docker Desktop
- about 5-10 GB of free disk space recommended
- internet access on the first run for model downloads

For the full step-by-step guide, see [docs/demo_docker.md](docs/demo_docker.md).

```bash
git clone https://github.com/matteopogetta/lecture-qa-extraction.git
cd lecture-qa-extraction
docker compose build
mkdir -p ~/Documents/LectureQASample/input ~/Documents/LectureQASample/output
# Put your video in ~/Documents/LectureQASample/input/lecture.mp4
./scripts/run_demo_docker.sh ~/Documents/LectureQASample/input/lecture.mp4
```

## Full Pipeline Docker Demo

Use this when you want the full lecture-processing pipeline, including
alignment, and are willing to wait longer for heavier model loading.

- helper script: `./scripts/run_full_pipeline_docker.sh`
- direct CLI examples: [docs/demo_docker.md](docs/demo_docker.md)
- optional diarization remains experimental and is documented separately

## Local Python Installation

Available for advanced local development, but not recommended for fast public
evaluation. It is more fragile than Docker and depends on host-level `ffmpeg`
and optional ML packages.

Guide: [docs/local_installation.md](docs/local_installation.md)

Minimal local setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
pip install -r requirements.txt
```

After activation, the CLI is available as:

```bash
lecture-analyzer --help
```

For QA/C quality and runtime evaluation, see
[docs/quality_evaluation.md](docs/quality_evaluation.md).

## Project overview

Lecture QA Extraction is a standalone Python prototype for transforming lecture
audio or video into structured, traceable JSON artifacts.

The official pipeline is now source-owned under `src/lecture_analyzer`:

- `src/lecture_analyzer/core/`
- `src/lecture_analyzer/input/`
- `src/lecture_analyzer/preprocessing/`
- `src/lecture_analyzer/transcription/`
- `src/lecture_analyzer/analysis/`
- `src/lecture_analyzer/output/`

## Current repository status

The repository is now in a post-wrapper cleanup phase.

- `src/lecture_analyzer` contains the real sentence-centric pipeline.
- The root `main.py` is still a temporary compatibility wrapper.
- Docker is aligned with the current structure and supports both the official
  CLI and smoke-mode verification.

See [docs/repository_status.md](docs/repository_status.md) for the repository
status summary and [docs/simplification_plan.md](docs/simplification_plan.md)
for the cleanup and migration plan.

Public namespaces currently available:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

## Real pipeline

The current official target flow is:

`input -> audio normalization -> transcription -> alignment -> sentence reconstruction -> segmentation -> QA extraction -> JSON output`

Key properties:

- audio and video are both accepted as inputs
- the internal processing pipeline is audio-based after normalization
- the preferred analysis path is sentence-centric
- segmentation and QA support controlled fallbacks when richer upstream layers
  are unavailable

## Reliable commands today

The installable `lecture-analyzer` entry point is the official CLI:

```bash
./.venv/bin/lecture-analyzer --help
```

Typical pipeline invocation:

```bash
./.venv/bin/lecture-analyzer /path/to/input.mp4 --output /path/to/output.json
```

Pipeline profiles are opt-in presets for optional branches:

- `current`: default; preserves the existing behavior
- `light`: disables heavier optional branches for fast local checks
- `quality`: enables alignment plus semantic QA without diarization
- `quality_local`: recommended default for lectures; alignment plus guarded
  local QA, speaker check, and speaker-assisted rescue, without diarization
- `full`: enables the heavier quality-oriented optional branches
- `diagnostic`: enables comparison/debug-oriented branches

Example:

```bash
./.venv/bin/lecture-analyzer /path/to/input.mp4 --output /path/to/output.json --pipeline-profile light
```

Write a local Markdown review packet for human or chatbot assessment:

```bash
./.venv/bin/lecture-analyzer /path/to/input.mp4 --output /path/to/output.json --export-ai-review-packet
```

Write a persistent local evaluation run under ignored `evaluations/`:

```bash
./.venv/bin/lecture-analyzer /path/to/input.mp4 --output /path/to/output.json --pipeline-profile light --export-evaluation-run
```

Use `--pipeline-profile quality` to evaluate semantic QA with alignment but
without diarization. Use `--pipeline-profile quality_local` to measure guarded
local QA without semantic retrieval/reranking cost. The local QA guardrails
filter low-information classroom check-ins, numeric polls, fragment questions,
question-like answers, same-sentence echoes, weak distant deferred answers,
low-autonomy question fragments, thin context, weak answer responsiveness, and
same-speaker/self-continuation risks.

## Speaker check and speaker-assisted rescue

`quality_local` includes a validated speaker-change check: question and answer
audio spans are compared with local ECAPA speaker embeddings (SpeechBrain).
Same-voice answers to non-socratic questions receive a graduated confidence
penalty; confidently different voices can rescue genuine interview exchanges
that soft quality gates would otherwise suppress.

The check activates automatically when the local model directory exists:

```bash
pip install speechbrain
python -c "from speechbrain.inference.speaker import EncoderClassifier; EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='local_models/spkrec-ecapa-voxceleb')"
```

One-time download (~80 MB); afterwards everything runs offline. Without the
model the pipeline falls back transparently (no penalty, no rescue) and notes
it in the run metrics. Runtime cost: ~2s model load per process plus
milliseconds per candidate. Overlapping spans (intra-sentence socratic pairs)
are excluded from the check; too-short question spans are extended audio-only
within the containing utterance.

An optional semantic responsiveness scorer (sentence-transformers) exists but
is OFF by default: on benchmark content it penalized short correct answers and
its per-run model load cost is high. It remains available as an opt-in
diagnostic via `--enable-qa-semantic-responsiveness`.

Compare saved local evaluation runs for one input:

```bash
./.venv/bin/lecture-analyzer-compare-evaluations evaluations/icwros
```

Run the full test suite:

```bash
./.venv/bin/python -m pytest
```

The root wrapper remains available for compatibility:

```bash
./.venv/bin/python main.py --help
```

For a professor-friendly Docker-only demo flow, see
[docs/demo_docker.md](docs/demo_docker.md).

For package-facing imports, use `lecture_analyzer.*`. The legacy root package
imports have been retired.

## Optional Hugging Face Token

Diarization is optional and may require a Hugging Face token for gated
pyannote models. The pipeline checks these values in order:

1. `PipelineConfig.diarization_auth_token`
2. `HUGGINGFACE_HUB_TOKEN`
3. `HF_TOKEN`

For a single terminal session:

```bash
export HUGGINGFACE_HUB_TOKEN="hf_xxxxxxxxxxxxxxxxx"
```

Or create a local `.env` file in the project root:

```env
HUGGINGFACE_HUB_TOKEN=hf_xxxxxxxxxxxxxxxxx
```

`.env` is ignored by Git. Never commit real tokens.

## Experimental or transitional areas

The following areas should be treated as transitional, optional, or
experimental:

- root `main.py` compatibility wrapper
- Docker and Docker Compose integration
- diarization (full-transcript; the span-level speaker check is stable)
- semantic QA retrieval, reranking, and the opt-in responsiveness scorer
- debug Excel export
- adaptive, windowed, and `both` segmentation modes
- text-only transcription cache fallback
- `disable-alignment` compatibility mode
- `src/lecture_analyzer/analysis/speaker_role.py`, which remains a placeholder
  unless integrated

## Prototype status (2026-07-05)

The extraction prototype is feature-complete and frozen. Final benchmark over
7 inputs (external AI review, keep/revise/reject per candidate): 46 emitted
candidates, 52% keep, 30% revise, 17% reject; reference university lecture at
86% keep with zero rejects; pure monologue input correctly yields zero
candidates. Development history and per-cycle decisions are recorded in
`PROJECT_DIARY.md`; the final quality snapshot lives in
`evaluations/benchmark_overview_2026-07-03.md` (local, not committed).

Known limits (documented, out of prototype scope): multi-speaker panels need
full diarization; interview recall is real but low in absolute terms;
residual semantic non-responsiveness cases; deictic blackboard-dependent
answers. See `docs/prototype_closure_report.md` for the closure report.

## Local materials and generated artifacts

The repository may contain local or non-essential materials such as:

- `artifacts/`
- `artifacts_*/`
- `sample_inputs/`
- `tmp/`
- transcription cache sidecars
- generated `.xlsx` debug files

These files are useful for experimentation and review, but they are not part of
the core source tree and should not define the project structure.

## Runtime requirements

- Python 3.11 or newer
- `ffmpeg`
- `ffprobe`

Depending on enabled branches, the real pipeline may also require:

- WhisperX-compatible runtime dependencies for alignment
- `pyannote.audio` for diarization
- `wtpsplit` for the quality sentence splitter; quality runs fail clearly if it is requested but unavailable
- `openpyxl` for debug Excel export
- `speechbrain` + local ECAPA model for the speaker check and rescue
- `sentence-transformers` for the opt-in semantic responsiveness scorer

## Repository structure

```text
project-root/
├── main.py                     # temporary compatibility wrapper
├── src/lecture_analyzer/       # src-owned pipeline implementation
├── docs/
├── tests/
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## Docker status

The current Docker setup copies:

- the src-owned pipeline implementation
- the official `lecture-analyzer` CLI package

This means the container supports:

- `lecture-analyzer --help`
- `lecture-analyzer --smoke --input /app/sample_data/example.mp4 --output-dir /app/tmp/cli-smoke-output`

Docker is aligned with the current repository structure, while heavy optional
runtime branches still depend on their own external libraries and models.
