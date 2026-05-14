# Docker Demo

## Quick start

```bash
git clone https://github.com/matteopogetta/lecture-qa-extraction.git
cd lecture-qa-extraction
docker compose build
mkdir -p ~/Documents/LectureQASample/input ~/Documents/LectureQASample/output
# Put your video in ~/Documents/LectureQASample/input/lecture.mp4
./scripts/run_demo_docker.sh ~/Documents/LectureQASample/input/lecture.mp4
```

## Minimum requirements

- Docker Desktop installed and running
- about 5-10 GB of free disk space recommended
- internet access on the first run for model downloads
- this repository cloned locally

The Docker demo runs the official src-owned pipeline under
`src/lecture_analyzer`.

## 2. Prepare the folders

```bash
mkdir -p ~/Documents/LectureQASample/input
mkdir -p ~/Documents/LectureQASample/output
```

## 3. Where to put the video

Place the lecture media file here:

```text
~/Documents/LectureQASample/input/lecture.mp4
```

You can also pass a different video path explicitly to the demo script.

## 4. Build Docker

From the repository root:

```bash
docker compose build
```

## 5. Quick demo (recommended)

This path is the recommended public demo:

- stable
- faster than the full pipeline
- alignment disabled on purpose
- avoids the heaviest optional branches

### Helper script

```bash
./scripts/run_demo_docker.sh ~/Documents/LectureQASample/input/lecture.mp4
```

If no argument is passed, the script defaults to:

```text
~/Documents/LectureQASample/input/lecture.mp4
```

### Direct CLI command

```bash
docker compose run --rm \
  -v ~/Documents/LectureQASample:/sample \
  lecture-analyzer \
  /sample/input/lecture.mp4 \
  --output /sample/output/docker_demo_result \
  --work-dir /sample/output/docker_demo_work \
  --session-id docker_demo \
  --disable-alignment \
  --segmentation-mode structural
```

## 6. Full pipeline demo

This path keeps alignment enabled and uses the full lecture-processing path
that is most useful for final outputs.

- slower than the quick demo
- downloads more model assets
- requires more RAM and disk space
- still keeps diarization disabled by default

### Helper script

```bash
./scripts/run_full_pipeline_docker.sh ~/Documents/LectureQASample/input/lecture.mp4
```

### Direct CLI command

```bash
docker compose run --rm \
  -v ~/Documents/LectureQASample:/sample \
  lecture-analyzer \
  /sample/input/lecture.mp4 \
  --output /sample/output/docker_full_result \
  --work-dir /sample/output/docker_full_work \
  --session-id docker_full \
  --segmentation-mode structural
```

### Optional experimental diarization

If you want to explore the heavier speaker branch, add:

```bash
--enable-diarization
```

Treat this as experimental and slower than the default documented flows.

## 7. Expected outputs

Quick demo outputs:

```text
~/Documents/LectureQASample/output/docker_demo_result
~/Documents/LectureQASample/output/docker_demo_work
```

Full pipeline outputs:

```text
~/Documents/LectureQASample/output/docker_full_result
~/Documents/LectureQASample/output/docker_full_work
```

Typical files to check:

- JSON result: `~/Documents/LectureQASample/output/docker_demo_result/<video_name>_structural.json`
- Excel debug workbook: `~/Documents/LectureQASample/output/docker_demo_work/debug_<video_name>_structural.xlsx`
- normalized audio artifacts: `~/Documents/LectureQASample/output/docker_demo_work/normalized_audio/`
- full-pipeline alignment artifacts: `~/Documents/LectureQASample/output/docker_full_work/alignment/`

## 8. Known limitations

- the first run may download the faster-whisper model
- the full pipeline may also download alignment assets
- long videos take time
- the quick demo disables alignment on purpose
- diarization is not part of the recommended demo flow
- input and output live outside the repository
- do not commit videos or generated outputs

## 9. Troubleshooting

### Docker is not running

Start Docker Desktop, then rerun:

```bash
docker compose build
```

### Input file not found

Make sure the video exists at:

```text
~/Documents/LectureQASample/input/lecture.mp4
```

or pass an explicit path:

```bash
./scripts/run_demo_docker.sh /full/path/to/your/video.mp4
```

### faster-whisper or model error

Retry after `docker compose build`. The first run may need extra time to fetch
runtime assets.

### Disk space issues

The demo can generate normalized audio, JSON, Excel, and intermediate
artifacts. Free some disk space and rerun the script if needed.
