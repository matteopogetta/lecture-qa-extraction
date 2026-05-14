# Docker Demo

## 1. Prerequisites

- Docker Desktop installed and running
- this repository cloned locally

## 2. Prepare the folders

```bash
mkdir -p ~/Documents/ExerPlazaSample/input
mkdir -p ~/Documents/ExerPlazaSample/output
```

## 3. Where to put the video

Place the lecture video here:

```text
~/Documents/ExerPlazaSample/input/lezione.mp4
```

You can also pass a different video path explicitly to the demo script.

## 4. Build Docker

From the repository root:

```bash
docker compose build
```

## 5. Run the demo

```bash
./scripts/run_demo_docker.sh ~/Documents/ExerPlazaSample/input/lezione.mp4
```

If no argument is passed, the script defaults to:

```text
~/Documents/ExerPlazaSample/input/lezione.mp4
```

## 6. Expected outputs

The demo writes outputs outside the repository:

```text
~/Documents/ExerPlazaSample/output/docker_demo_result
~/Documents/ExerPlazaSample/output/docker_demo_work
```

Typical files to check:

- JSON result: `~/Documents/ExerPlazaSample/output/docker_demo_result/<video_name>_structural.json`
- Excel debug workbook: `~/Documents/ExerPlazaSample/output/docker_demo_result/<video_name>_structural.xlsx`
- normalized audio artifacts: `~/Documents/ExerPlazaSample/output/docker_demo_work/normalized_audio/`

## 7. Known limitations

- the first run may download the faster-whisper model
- long videos take time
- alignment and diarization are disabled in this demo
- input and output live outside the repository
- do not commit videos or generated outputs

## 8. Troubleshooting

### Docker is not running

Start Docker Desktop, then rerun:

```bash
docker compose build
```

### Input file not found

Make sure the video exists at:

```text
~/Documents/ExerPlazaSample/input/lezione.mp4
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
