#!/usr/bin/env bash

set -euo pipefail

# Resolve the repository root from the script location so the command can be
# launched from any working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SAMPLE_ROOT="${HOME}/Documents/ExerPlazaSample"
INPUT_DIR="${SAMPLE_ROOT}/input"
OUTPUT_DIR="${SAMPLE_ROOT}/output"
DEFAULT_INPUT_PATH="${INPUT_DIR}/lezione.mp4"

expand_path() {
  local raw_path="$1"

  if [[ "${raw_path}" == "~" ]]; then
    printf '%s\n' "${HOME}"
    return
  fi

  if [[ "${raw_path}" == "~/"* ]]; then
    printf '%s/%s\n' "${HOME}" "${raw_path#~/}"
    return
  fi

  printf '%s\n' "${raw_path}"
}

INPUT_PATH="$(expand_path "${1:-${DEFAULT_INPUT_PATH}}")"
INPUT_FILENAME="$(basename "${INPUT_PATH}")"
INPUT_STEM="${INPUT_FILENAME%.*}"
SANITIZED_STEM="$(
  printf '%s' "${INPUT_STEM}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//'
)"

if [[ -z "${SANITIZED_STEM}" ]]; then
  SANITIZED_STEM="session"
fi

mkdir -p "${INPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

if [[ ! -f "${INPUT_PATH}" ]]; then
  echo "Input video not found: ${INPUT_PATH}" >&2
  echo >&2
  echo "Place your video here:" >&2
  echo "  ${DEFAULT_INPUT_PATH}" >&2
  echo >&2
  echo "Example command:" >&2
  echo "  ./scripts/run_demo_docker.sh ${DEFAULT_INPUT_PATH}" >&2
  exit 1
fi

CONTAINER_INPUT_PATH="/sample/input/${INPUT_FILENAME}"
CONTAINER_OUTPUT_PATH="/sample/output/docker_demo_result"
CONTAINER_WORK_PATH="/sample/output/docker_demo_work"
HOST_RESULT_DIR="${OUTPUT_DIR}/docker_demo_result"
HOST_WORK_DIR="${OUTPUT_DIR}/docker_demo_work"
EXPECTED_JSON_PATH="${HOST_RESULT_DIR}/${SANITIZED_STEM}_structural.json"
EXPECTED_EXCEL_PATH="${HOST_RESULT_DIR}/${SANITIZED_STEM}_structural.xlsx"
NORMALIZED_AUDIO_DIR="${HOST_WORK_DIR}/normalized_audio"

mkdir -p "${HOST_RESULT_DIR}"
mkdir -p "${HOST_WORK_DIR}"

cd "${PROJECT_ROOT}"

echo "Running Docker demo with input:"
echo "  ${INPUT_PATH}"
echo

docker compose run --rm \
  -v "${SAMPLE_ROOT}:/sample" \
  lecture-analyzer \
  "${CONTAINER_INPUT_PATH}" \
  --output "${CONTAINER_OUTPUT_PATH}" \
  --work-dir "${CONTAINER_WORK_PATH}" \
  --session-id docker_demo \
  --disable-alignment \
  --segmentation-mode structural

echo
echo "Demo completed."
echo "JSON output:"
echo "  ${EXPECTED_JSON_PATH}"
echo "Excel debug output:"
echo "  ${EXPECTED_EXCEL_PATH}"
echo "Normalized audio directory:"
echo "  ${NORMALIZED_AUDIO_DIR}"
