# ExerPlaza Lecture Analyzer

## Quick Docker Demo

For the full step-by-step guide, see
[docs/demo_docker.md](docs/demo_docker.md).

```bash
git clone https://github.com/matteopogetta/lecture-qa-extraction.git
cd lecture-qa-extraction
docker compose build
mkdir -p ~/Documents/ExerPlazaSample/input ~/Documents/ExerPlazaSample/output
# Put your video in ~/Documents/ExerPlazaSample/input/lezione.mp4
./scripts/run_demo_docker.sh ~/Documents/ExerPlazaSample/input/lezione.mp4
```

## Project overview

ExerPlaza Lecture Analyzer is a standalone Python prototype for transforming
lecture audio or video into structured, traceable JSON artifacts.

The official pipeline is now source-owned under `src/lecture_analyzer`:

- `src/lecture_analyzer/core/`
- `src/lecture_analyzer/input/`
- `src/lecture_analyzer/preprocessing/`
- `src/lecture_analyzer/transcription/`
- `src/lecture_analyzer/analysis/`
- `src/lecture_analyzer/output/`

The root packages with the same names still exist, but only as temporary
legacy wrappers that preserve backwards-compatible imports.

## Current repository status

The repository is in a post-consolidation compatibility phase.

- `src/lecture_analyzer` contains the real sentence-centric pipeline.
- The root packages are temporary wrappers that re-export the src-owned
  implementation.
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
./.venv-system/bin/lecture-analyzer --help
```

Typical pipeline invocation:

```bash
./.venv-system/bin/lecture-analyzer /path/to/input.mp4 --output /path/to/output.json
```

Run the full test suite:

```bash
./.venv-system/bin/python -m pytest
```

The root wrapper remains available for compatibility:

```bash
./.venv-system/bin/python main.py --help
```

For a professor-friendly Docker-only demo flow, see
[docs/demo_docker.md](docs/demo_docker.md).

For package-facing imports, prefer `lecture_analyzer.*`. Root imports such as
`core.*`, `input.*`, and `transcription.*` remain available only as temporary
compatibility wrappers.

## Experimental or transitional areas

The following areas should be treated as transitional, optional, or
experimental:

- root-package legacy wrappers
- root `main.py` compatibility wrapper
- Docker and Docker Compose integration
- diarization
- semantic QA retrieval and reranking
- debug Excel export
- adaptive, windowed, and `both` segmentation modes
- text-only transcription cache fallback
- `disable-alignment` compatibility mode
- `analysis/speaker_role.py`, which remains a placeholder unless integrated

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
- `wtpsplit` for the preferred sentence splitter
- `openpyxl` for debug Excel export
- semantic-model dependencies for semantic QA branches

## Repository structure

```text
project-root/
├── main.py                     # temporary compatibility wrapper
├── core/                       # legacy wrapper package
├── input/                      # legacy wrapper package
├── preprocessing/              # legacy wrapper package
├── transcription/              # legacy wrapper package
├── analysis/                   # legacy wrapper package
├── output/                     # legacy wrapper package
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
- the temporary legacy root wrappers needed for compatibility

This means the container supports:

- `lecture-analyzer --help`
- `lecture-analyzer --smoke --input /app/sample_data/example.mp4 --output-dir /app/tmp/cli-smoke-output`

Docker is aligned with the current repository structure, while heavy optional
runtime branches still depend on their own external libraries and models.
