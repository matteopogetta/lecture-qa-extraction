# Local Python Installation

This mode is useful for local development, debugging, and custom experiments.
For quick evaluation, the Docker demo is more stable and is the recommended
entry point.

The implementation-owning packages live under `src/lecture_analyzer`.

## Why this is not the recommended path

- local Python environments are more fragile than Docker
- `ffmpeg` must be installed on the host
- optional ML dependencies can vary by operating system
- model downloads can be slow and disk-heavy

For the recommended public-facing path, see [docs/demo_docker.md](docs/demo_docker.md).

## 1. Prerequisites

- Python 3.11
- `ffmpeg` available on `PATH`
- enough disk space for models and generated artifacts

## 2. Clone the repository

```bash
git clone https://github.com/matteopogetta/lecture-qa-extraction.git
cd lecture-qa-extraction
```

## 3. Create the virtual environment

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 4. Install the project

```bash
pip install -e .
pip install -r requirements.txt
```

## 5. Install transcription dependencies

The real lecture-processing pipeline needs at least:

```bash
pip install faster-whisper
```

Optional alignment support:

```bash
pip install whisperx
```

Optional diarization support is heavier and should be treated as experimental.
If a gated pyannote model requires authentication, set a Hugging Face token
before running with `--enable-diarization`:

```bash
export HUGGINGFACE_HUB_TOKEN="hf_xxxxxxxxxxxxxxxxx"
```

You can also put it in a local `.env` file:

```env
HUGGINGFACE_HUB_TOKEN=hf_xxxxxxxxxxxxxxxxx
```

The project also accepts `HF_TOKEN`. Local `.env` files are ignored by Git.

## 6. Install ffmpeg

### macOS

```bash
brew install ffmpeg
```

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

### Windows

Install `ffmpeg` with your preferred package manager, then make sure the
binary is available on `PATH`.

Example with `winget`:

```powershell
winget install Gyan.FFmpeg
```

## 7. Run the CLI

Quick smoke mode:

```bash
lecture-analyzer --smoke --input sample_data/example.mp4 --output-dir tmp/cli-smoke-output
```

Real pipeline example:

```bash
lecture-analyzer /full/path/to/lecture.mp4 \
  --output /full/path/to/output/result.json \
  --work-dir /full/path/to/output/work \
  --segmentation-mode structural
```

## 8. Troubleshooting

### Windows PowerShell execution policy

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

### ffmpeg not found

If the CLI reports that `ffmpeg` or `ffprobe` is missing, verify that both
commands are available on `PATH`:

```bash
ffmpeg -version
ffprobe -version
```

### Disk space

The project may download models and generate normalized audio, JSON, Excel,
and intermediate artifacts. Free additional disk space if runs fail midway.

### Optional WhisperX alignment

If alignment fails, retry with:

```bash
lecture-analyzer /full/path/to/lecture.mp4 \
  --output /full/path/to/output/result.json \
  --work-dir /full/path/to/output/work \
  --disable-alignment
```

This avoids the optional WhisperX branch and is usually the fastest recovery
path for local setups.
