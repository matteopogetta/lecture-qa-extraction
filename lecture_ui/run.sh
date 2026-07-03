#!/usr/bin/env bash
# Launch the Lecture QA Viewer (zero dependencies, Python 3.9+).
set -e
cd "$(dirname "$0")"
PY="python3"
# Prefer the project's venv python if present (still no extra deps required).
if [ -x "../.venv/bin/python3" ]; then PY="../.venv/bin/python3"; fi
exec "$PY" serve.py "$@"
