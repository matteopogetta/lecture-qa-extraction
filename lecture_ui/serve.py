#!/usr/bin/env python3
"""Lecture QA Viewer - standalone local viewer for existing session.json outputs.

Milestone 1: load an already-generated pipeline session.json + its normalized
audio and replay the lecture with a synchronized transcript and navigable Q/A.

Design goals:
- zero external dependencies (Python 3.9+ standard library only)
- never modifies the existing project; lives entirely under lecture_ui/
- serves audio with HTTP Range support so seeking works on large WAV files

Run:
    python3 lecture_ui/serve.py            # then open http://localhost:8000
    python3 lecture_ui/serve.py --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
STATIC_DIR = HERE / "static"

# ExerPlazaSample is a sibling of the project (~/Documents/ExerPlazaSample).
# New runs launched from the UI are stored here, mirroring the existing layout:
#   input/<file>                       (source media, added only if missing)
#   output/<label>/<name>_<mode>.json  (session JSON)
#   output/<label>_work/...            (per-run pipeline artifacts)
SAMPLE_ROOT = Path(
    os.getenv("LECTURE_UI_SAMPLE_DIR", str(PROJECT_ROOT.parent / "ExerPlazaSample"))
).expanduser()
SAMPLE_INPUT_DIR = SAMPLE_ROOT / "input"
SAMPLE_OUTPUT_DIR = SAMPLE_ROOT / "output"

# Roots the server is allowed to read session JSON / serve audio from.
ALLOWED_ROOTS = [PROJECT_ROOT, SAMPLE_ROOT]

# Directory / filename patterns that indicate pipeline sidecar artifacts
# (not standalone session outputs) so discovery can skip them.
_SIDECAR_DIR_NAMES = {"alignment", "diarization", "normalized_audio",
                      "sentences", "utterances", "transcription_cache"}
_SIDECAR_SUFFIXES = (".metadata.json", ".transcription.json", ".sentences.json",
                     ".utterances.json", ".alignment.json", ".diarization.json")


def _looks_like_sidecar(path: Path) -> bool:
    name = path.name
    if any(name.endswith(sfx) for sfx in _SIDECAR_SUFFIXES):
        return True
    for part in path.parts:
        if part.endswith("_work") or part in _SIDECAR_DIR_NAMES:
            return True
    return False


def _under_allowed_root(path: Path) -> bool:
    rp = path.resolve()
    for root in ALLOWED_ROOTS:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False

# Directories that may contain generated session.json files.
SESSION_SEARCH_ROOTS = [
    PROJECT_ROOT / "evaluations",
    PROJECT_ROOT / "artifacts" / "ui_sessions",
    PROJECT_ROOT / "artifacts",
    PROJECT_ROOT / "tmp",
]

AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus"}

INPUT_EXTS = AUDIO_EXTS | {".mp4", ".mov", ".mkv", ".avi", ".webm"}

# ---------------------------------------------------------------------------
# Analysis (Milestone 2): wrap the existing pipeline CLI as an external process
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# Display stages (subset of the pipeline's internal stages) with IT labels.
STAGE_ORDER = [
    "session_loading",
    "audio_normalization",
    "transcription",
    "alignment",
    "diarization",
    "sentence_reconstruction",
    "transcript_segmentation",
    "qa_extraction",
    "json_export",
]
STAGE_LABELS = {
    "session_loading": "Caricamento input",
    "audio_normalization": "Normalizzazione audio",
    "transcription": "Trascrizione",
    "alignment": "Alignment",
    "diarization": "Diarizzazione",
    "sentence_reconstruction": "Ricostruzione frasi",
    "transcript_segmentation": "Segmentazione",
    "qa_extraction": "Estrazione Q/A",
    "json_export": "Export JSON",
}
_STAGE_RE = re.compile(
    r"\b(" + "|".join(STAGE_ORDER) + r")\s*:\s*([a-z_]+)"
)
_DUR_RE = re.compile(r"([0-9]+\.[0-9]+)s\b")


def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return name.strip("._") or "session"


def _venv_python() -> str:
    cand = PROJECT_ROOT / ".venv" / "bin" / "python3"
    if cand.is_file():
        return str(cand)
    return sys.executable


def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _build_command(input_paths, output_dir: Path, work_dir: Path, opts: dict) -> list[str]:
    cmd = [_venv_python(), str(PROJECT_ROOT / "main.py")]
    cmd += [str(p) for p in input_paths]
    cmd += ["--output", str(output_dir)]
    cmd += ["--work-dir", str(work_dir), "--log-level", "INFO"]
    sid = (opts.get("session_id") or "").strip()
    if sid:
        cmd += ["--session-id", sid]
    profile = (opts.get("pipeline_profile") or "").strip()
    if profile and profile != "current":
        cmd += ["--pipeline-profile", profile]
    seg = (opts.get("segmentation_mode") or "").strip()
    if seg and seg != "structural":
        cmd += ["--segmentation-mode", seg]
    if _as_bool(opts.get("disable_alignment")):
        cmd += ["--disable-alignment"]
    if _as_bool(opts.get("enable_diarization")):
        cmd += ["--enable-diarization"]
    if _as_bool(opts.get("from_scratch")):
        cmd += ["--from-scratch"]
    if _as_bool(opts.get("export_review_packet")):
        cmd += ["--export-ai-review-packet"]
    if _as_bool(opts.get("export_evaluation")):
        cmd += ["--export-evaluation-run"]
    return cmd


def _build_batch_command(input_path: Path, opts: dict) -> list[str]:
    """Evaluation mode: use the project's run_evaluation_batch.py, which always
    produces an evaluatable run (session.json + review_packet.md + ai_review.json
    + metrics.json) under evaluations/<label>/runs/<id>/ and also writes the
    session JSON under ExerPlazaSample/output. Alignment/diarization/review are
    governed by the chosen profile, matching the canonical CLI flow."""
    py = _venv_python()
    script = PROJECT_ROOT / "scripts" / "run_evaluation_batch.py"
    cmd = [
        py, str(script),
        "--input-dir", str(SAMPLE_INPUT_DIR),
        "--pattern", input_path.name,
        "--profiles", (opts.get("pipeline_profile") or "quality_local"),
        "--segmentation-mode", (opts.get("segmentation_mode") or "structural"),
        "--python", py,
    ]
    if _as_bool(opts.get("from_scratch")):
        cmd += ["--force-recompute"]
    else:
        cmd += ["--resume"]
    prefix = (opts.get("session_id") or "").strip()
    if prefix:
        cmd += ["--input-label-prefix", _sanitize_name(prefix)]
    return cmd


def _find_evaluation_output(started: float, stem: str) -> "Path | None":
    ev = PROJECT_ROOT / "evaluations"
    if not ev.is_dir():
        return None
    runs = list(ev.glob("*/runs/*/session.json"))
    fresh = [p for p in runs if p.stat().st_mtime >= started - 3]
    if fresh:
        return max(fresh, key=lambda p: p.stat().st_mtime)
    slug = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())[:8]
    if slug:
        cands = [p for p in runs
                 if slug[:6] in re.sub(r"[^a-z0-9]+", "", p.parent.parent.name.lower())]
        if cands:
            return max(cands, key=lambda p: p.stat().st_mtime)
    return None


def _update_stage_from_line(job: dict, line: str) -> None:
    m = _STAGE_RE.search(line)
    if not m:
        return
    stage, status = m.group(1), m.group(2)
    d = _DUR_RE.search(line)
    entry = {"status": status}
    if d:
        entry["duration"] = float(d.group(1))
    job["stages"][stage] = entry


def _find_output_json(session_dir: Path) -> Path | None:
    cands = [
        p for p in session_dir.glob("*.json")
        if not p.name.endswith(".metadata.json")
    ]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def _run_job(job_id: str, cmd: list[str]) -> None:
    job = JOBS[job_id]
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        with JOBS_LOCK:
            job["pid"] = proc.pid
        for line in proc.stdout:
            line = line.rstrip("\n")
            with JOBS_LOCK:
                job["log"].append(line)
                if len(job["log"]) > 1000:
                    job["log"] = job["log"][-1000:]
                _update_stage_from_line(job, line)
        proc.wait()
        with JOBS_LOCK:
            job["returncode"] = proc.returncode
            if proc.returncode == 0:
                # any display stage never logged -> treat as skipped
                for st in STAGE_ORDER:
                    job["stages"].setdefault(st, {"status": "skipped"})
                if job.get("mode") == "evaluation":
                    out = _find_evaluation_output(job["started"], job.get("input_stem", ""))
                else:
                    out = _find_output_json(Path(job["session_dir"]))
                if out is not None:
                    job["output_path"] = str(out.resolve())
                    job["status"] = "done"
                else:
                    job["status"] = "error"
                    job["error"] = "Pipeline completata ma nessun JSON di output trovato."
            else:
                job["status"] = "error"
                job["error"] = (
                    "La pipeline e' terminata con codice %d. Vedi il log."
                    % proc.returncode
                )
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(exc)


def _unique_label(base: str) -> str:
    """Return an output label that does not clash with an existing run dir."""
    base = _sanitize_name(base)
    candidate = base
    i = 2
    while (SAMPLE_OUTPUT_DIR / candidate).exists():
        candidate = f"{base}_{i}"
        i += 1
    return candidate


def _place_inputs(files: list[Path]) -> list[Path]:
    """Copy uploaded files into ExerPlazaSample/input, skipping any that
    already exist there (matched by original filename). Returns final paths."""
    SAMPLE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    placed = []
    for fp in files:
        dest = SAMPLE_INPUT_DIR / fp.name
        if dest.is_file() and dest.stat().st_size > 0:
            # already present -> reuse existing, drop the fresh upload
            placed.append(dest)
            try:
                fp.unlink()
            except OSError:
                pass
        else:
            shutil.move(str(fp), str(dest))
            placed.append(dest)
    return placed


def start_analysis(fields: dict, files: list[Path]) -> str:
    input_paths = _place_inputs(files)
    if not input_paths:
        raise ValueError("Nessun file di input ricevuto.")
    evaluation_mode = _as_bool(fields.get("evaluation_mode"))
    started = time.time()
    if evaluation_mode:
        # Canonical evaluatable run via run_evaluation_batch.py (one input file).
        cmd = _build_batch_command(input_paths[0], fields)
        job = {
            "status": "running", "stages": {}, "log": [],
            "mode": "evaluation", "session_dir": str(PROJECT_ROOT / "evaluations"),
            "cmd": cmd, "input_stem": input_paths[0].stem,
            "input_paths": [str(p) for p in input_paths],
            "output_path": None, "error": None, "returncode": None,
            "started": started,
        }
    else:
        # Direct main.py wrapper -> ExerPlazaSample/output/<label>/.
        sid = (fields.get("session_id") or "").strip()
        label = _unique_label(sid if sid else input_paths[0].stem)
        output_dir = SAMPLE_OUTPUT_DIR / label
        work_dir = SAMPLE_OUTPUT_DIR / f"{label}_work"
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        cmd = _build_command(input_paths, output_dir, work_dir, fields)
        job = {
            "status": "running", "stages": {}, "log": [],
            "mode": "direct", "session_dir": str(output_dir), "cmd": cmd,
            "label": label, "input_stem": input_paths[0].stem,
            "input_paths": [str(p) for p in input_paths],
            "output_path": None, "error": None, "returncode": None,
            "started": started,
        }
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return job_id


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def _safe_load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


import datetime as _dt


def _run_time_from_path(path: Path):
    """Return (epoch_seconds, 'YYYY-MM-DD HH:MM') parsed from a run folder name.

    Run folders are named like '2026-06-21_141120_full_adaptive'. Falls back to
    the nearest matching component; returns (None, None) if nothing matches.
    """
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})", str(path))
    if not m:
        return None, None
    y, mo, d, hh, mm, ss = (int(g) for g in m.groups())
    try:
        dt = _dt.datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None, None
    return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M")


def discover_sessions() -> list[dict]:
    """Find session.json files and return lightweight descriptors."""
    found: dict[str, dict] = {}
    for root in SESSION_SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("session.json"):
            rp = str(path.resolve())
            if rp in found:
                continue
            meta = _peek_session(path)
            if meta is not None:
                found[rp] = meta
    ui_root = PROJECT_ROOT / "artifacts" / "ui_sessions"
    if ui_root.is_dir():
        for path in ui_root.rglob("*.json"):
            if _looks_like_sidecar(path):
                continue
            rp = str(path.resolve())
            if rp in found:
                continue
            meta = _peek_session(path)
            if meta is not None:
                meta["origin"] = "ui"
                found[rp] = meta
    if SAMPLE_OUTPUT_DIR.is_dir():
        for path in SAMPLE_OUTPUT_DIR.rglob("*.json"):
            if _looks_like_sidecar(path):
                continue
            rp = str(path.resolve())
            if rp in found:
                continue
            meta = _peek_session(path)
            if meta is not None:
                meta["origin"] = "sample"
                found[rp] = meta
    sessions = list(found.values())
    sessions.sort(key=lambda s: (s.get("run_time") or 0), reverse=True)
    return sessions


def _peek_session(path: Path) -> dict | None:
    """Read minimal fields from a session.json to build a picker entry."""
    data = _safe_load_json(path)
    if not isinstance(data, dict):
        return None
    # Require it to look like a pipeline session output.
    if "qa_candidates" not in data and "sentences" not in data:
        return None
    meta = data.get("session_metadata", {}) or {}
    inner = meta.get("metadata", {}) or {}
    audio_sources = data.get("audio_sources", []) or []
    duration = None
    source_name = None
    if audio_sources:
        duration = audio_sources[0].get("duration_seconds")
        na = audio_sources[0].get("normalized_asset", {}) or {}
        source_name = na.get("source_filename")
    if not source_name:
        isrc = data.get("input_sources", []) or []
        if isrc:
            source_name = isrc[0].get("original_filename")
    try:
        rel = path.resolve().relative_to(PROJECT_ROOT)
        rel_str = str(rel)
    except ValueError:
        rel_str = str(path.resolve())
    run_ts, run_display = _run_time_from_path(path)
    mtime = path.stat().st_mtime
    return {
        "path": str(path.resolve()),
        "rel_path": rel_str,
        "session_id": meta.get("session_id"),
        "source_name": source_name,
        "duration_seconds": duration,
        "language_codes": meta.get("language_codes"),
        "pipeline_profile": inner.get("pipeline_profile"),
        "segmentation_mode": inner.get("segmentation_mode"),
        "sentence_count": len(data.get("sentences", []) or []),
        "qa_count": len(data.get("qa_candidates", []) or []),
        "mtime": mtime,
        "run_time": run_ts if run_ts is not None else mtime,
        "run_time_display": run_display,
        "run_time_source": "run_folder" if run_ts is not None else "file_mtime",
    }


# ---------------------------------------------------------------------------
# Audio path resolution
# ---------------------------------------------------------------------------


def _session_audio_paths(data: dict) -> list[str]:
    paths: list[str] = []
    for src in data.get("audio_sources", []) or []:
        for key in ("audio_path",):
            if src.get(key):
                paths.append(src[key])
        na = src.get("normalized_asset", {}) or {}
        if na.get("derived_path"):
            paths.append(na["derived_path"])
        md = src.get("metadata", {}) or {}
        if md.get("normalized_audio_path"):
            paths.append(md["normalized_audio_path"])
    # de-dup preserving order
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def resolve_audio_file(raw_path: str) -> Path | None:
    """Resolve an audio path recorded in a session to a real local file.

    Handles three cases:
    1. the absolute path exists as-is (normal case on the machine that ran the
       pipeline);
    2. the path was recorded on another machine / different project location:
       re-anchor everything after 'ExerPlazaProject/' onto the current root;
    3. last resort: look for the basename under artifacts/normalized_audio.
    """
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p
    norm = raw_path.replace("\\", "/")
    for anchor, root in (("ExerPlazaProject/", PROJECT_ROOT),
                         ("ExerPlazaSample/", SAMPLE_ROOT)):
        if anchor in norm:
            rel = norm.split(anchor, 1)[1]
            cand = root / rel
            if cand.is_file():
                return cand
    for base in (PROJECT_ROOT / "artifacts" / "normalized_audio",):
        cand = base / p.name
        if cand.is_file():
            return cand
    return None


def resolve_session_audio(data: dict) -> Path | None:
    for raw in _session_audio_paths(data):
        found = resolve_audio_file(raw)
        if found is not None:
            return found
    return None


def _is_allowed_audio(path: Path) -> bool:
    """Only serve audio files that live under an allowed root."""
    if not _under_allowed_root(path):
        return False
    return path.suffix.lower() in AUDIO_EXTS and path.is_file()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Minimal streaming multipart/form-data parser (writes file parts to disk).
# ---------------------------------------------------------------------------


def _parse_part_headers(blob: bytes) -> dict:
    out = {}
    for raw in blob.split(b"\r\n"):
        if b":" not in raw:
            continue
        k, v = raw.split(b":", 1)
        k = k.decode("latin-1").strip().lower()
        v = v.decode("latin-1").strip()
        if k == "content-disposition":
            for m in re.finditer(r'(\w+)="([^"]*)"', v):
                out[m.group(1)] = m.group(2)
    return out


def parse_multipart_to_disk(rfile, boundary: bytes, content_length: int, dest_dir: Path):
    """Stream a multipart body to disk. Returns (fields, [file_paths])."""
    dash = b"--" + boundary
    delim = b"\r\n" + dash
    chunk = 1 << 16
    remaining = [max(0, content_length)]
    buf = bytearray()

    def more() -> bytes:
        if remaining[0] <= 0:
            return b""
        d = rfile.read(min(chunk, remaining[0]))
        remaining[0] -= len(d)
        return d

    while dash not in buf:
        d = more()
        if not d:
            break
        buf += d
    idx = buf.find(dash)
    if idx < 0:
        return {}, []
    del buf[: idx + len(dash)]

    fields, files = {}, []
    while True:
        while len(buf) < 2:
            d = more()
            if not d:
                break
            buf += d
        if buf[:2] == b"--":
            break
        if buf[:2] == b"\r\n":
            del buf[:2]
        while b"\r\n\r\n" not in buf:
            d = more()
            if not d:
                break
            buf += d
        he = buf.find(b"\r\n\r\n")
        if he < 0:
            break
        headers = _parse_part_headers(bytes(buf[:he]))
        del buf[: he + 4]
        name = headers.get("name")
        filename = headers.get("filename")
        if filename:
            base = Path(filename).name
            if base in ("", ".", ".."):
                base = "upload.bin"
            out_path = dest_dir / base
            with open(out_path, "wb") as fh:
                while True:
                    di = buf.find(delim)
                    if di >= 0:
                        fh.write(buf[:di])
                        del buf[: di + len(delim)]
                        break
                    keep = len(delim)
                    if len(buf) > keep:
                        fh.write(buf[:-keep])
                        del buf[:-keep]
                    d = more()
                    if not d:
                        fh.write(buf)
                        buf.clear()
                        break
                    buf += d
            files.append(out_path)
        else:
            val = bytearray()
            while True:
                di = buf.find(delim)
                if di >= 0:
                    val += buf[:di]
                    del buf[: di + len(delim)]
                    break
                keep = len(delim)
                if len(buf) > keep:
                    val += buf[:-keep]
                    del buf[:-keep]
                d = more()
                if not d:
                    val += buf
                    buf.clear()
                    break
                buf += d
            if name:
                fields[name] = val.decode("utf-8", "replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "LectureQAViewer/1.0"

    # quieter logging
    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[viewer] %s\n" % (fmt % args))

    # -- helpers ----------------------------------------------------------
    def _send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status=status)

    def _validated_session_path(self, raw: str) -> Path | None:
        """Only allow session json files under an allowed root."""
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if not _under_allowed_root(p):
            return None
        if p.is_file() and p.suffix.lower() == ".json":
            return p
        return None

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)

        if route == "/" or route == "/index.html":
            return self._serve_static("index.html")
        if route.startswith("/static/"):
            return self._serve_static(route[len("/static/"):])
        if route == "/api/sessions":
            return self._send_json({"sessions": discover_sessions()})
        if route == "/api/session":
            return self._api_session(qs)
        if route == "/api/audio":
            return self._api_audio(qs)
        if route == "/api/analyze/status":
            return self._api_analyze_status(qs)
        return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/analyze":
            return self._api_analyze()
        return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    do_HEAD = do_GET

    # -- endpoints --------------------------------------------------------
    def _api_session(self, qs):
        raw = (qs.get("path") or [""])[0]
        path = self._validated_session_path(raw)
        if path is None:
            return self._send_error_json(
                HTTPStatus.BAD_REQUEST, "invalid or missing session path"
            )
        data = _safe_load_json(path)
        if not isinstance(data, dict):
            return self._send_error_json(
                HTTPStatus.UNPROCESSABLE_ENTITY, "could not parse session json"
            )
        audio = resolve_session_audio(data)
        audio_url = None
        audio_exists = False
        if audio is not None:
            audio_exists = True
            audio_url = "/api/audio?path=" + _quote(str(audio.resolve()))
        return self._send_json(
            {
                "session": data,
                "audio_url": audio_url,
                "audio_available": audio_exists,
                "session_path": str(path.resolve()),
            }
        )

    def _api_audio(self, qs):
        raw = (qs.get("path") or [""])[0]
        if not raw:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, "missing path")
        path = Path(raw)
        if not _is_allowed_audio(path):
            return self._send_error_json(HTTPStatus.FORBIDDEN, "audio not allowed")
        return self._serve_file_with_range(path)

    def _api_analyze(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._send_error_json(
                HTTPStatus.BAD_REQUEST, "atteso multipart/form-data"
            )
        m = re.search(r"boundary=(.+)$", ctype)
        if not m:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, "boundary mancante")
        boundary = m.group(1).strip().strip('"').encode("latin-1")
        length = int(self.headers.get("Content-Length", "0") or "0")
        tmpdir = Path(tempfile.mkdtemp(prefix="lqa_upload_"))
        try:
            fields, files = parse_multipart_to_disk(
                self.rfile, boundary, length, tmpdir
            )
        except Exception as exc:  # noqa: BLE001
            return self._send_error_json(
                HTTPStatus.BAD_REQUEST, "errore parsing upload: %s" % exc
            )
        files = [f for f in files if f.suffix.lower() in INPUT_EXTS and f.stat().st_size > 0]
        if not files:
            return self._send_error_json(
                HTTPStatus.BAD_REQUEST, "nessun file audio/video valido caricato"
            )
        try:
            job_id = start_analysis(fields, files)
        except Exception as exc:  # noqa: BLE001
            return self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        return self._send_json({"job_id": job_id})

    def _api_analyze_status(self, qs):
        jid = (qs.get("job") or [""])[0]
        job = JOBS.get(jid)
        if not job:
            return self._send_error_json(HTTPStatus.NOT_FOUND, "job non trovato")
        with JOBS_LOCK:
            stages = []
            for st in STAGE_ORDER:
                e = job["stages"].get(st, {"status": "pending"})
                stages.append({"name": st, "label": STAGE_LABELS[st], **e})
            snap = {
                "status": job["status"],
                "returncode": job["returncode"],
                "error": job["error"],
                "output_path": job["output_path"],
                "stages": stages,
                "log_tail": job["log"][-60:],
                "elapsed": round(time.time() - job["started"], 1),
            }
        return self._send_json(snap)

    # -- static + files ---------------------------------------------------
    def _serve_static(self, rel: str):
        # prevent path traversal
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._send_error_json(HTTPStatus.FORBIDDEN, "forbidden")
        if not target.is_file():
            return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _serve_file_with_range(self, path: Path):
        file_size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1

        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                g1, g2 = m.group(1), m.group(2)
                if g1 == "" and g2 != "":  # suffix range: last N bytes
                    length = min(int(g2), file_size)
                    start = file_size - length
                    end = file_size - 1
                else:
                    start = int(g1) if g1 else 0
                    end = int(g2) if g2 else file_size - 1
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % file_size)
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        else:
            status = HTTPStatus.OK

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header(
                "Content-Range", "bytes %d-%d/%d" % (start, end, file_size)
            )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if self.command == "HEAD":
            return
        chunk = 256 * 1024
        remaining = length
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                while remaining > 0:
                    buf = fh.read(min(chunk, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            # client seeked/closed - normal for audio players
            pass


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lecture QA Viewer (local).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not auto-open a browser"
    )
    args = parser.parse_args(argv)

    if not (STATIC_DIR / "index.html").is_file():
        print("ERROR: static/index.html not found next to serve.py", file=sys.stderr)
        return 2

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 60)
    print(" Lecture QA Viewer")
    print(" Project root:", PROJECT_ROOT)
    n = len(discover_sessions())
    print(f" Discovered {n} session.json file(s).")
    print(" Open:", url)
    print(" Press Ctrl+C to stop.")
    print("=" * 60)

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
