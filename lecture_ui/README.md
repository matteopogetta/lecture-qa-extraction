# Lecture QA Viewer (Milestone 1)

Standalone, zero-dependency local viewer for lecture pipeline outputs.
It loads an already-generated `session.json` plus its normalized audio and lets
you "re-enter" the lecture: synchronized transcript, navigable Q/A, and a
timeline with markers.

**This tool does not modify the project.** It only reads existing files under
the project root and lives entirely in `lecture_ui/`.

## Run

```bash
python3 lecture_ui/serve.py
# or
./lecture_ui/run.sh
```

Then open http://localhost:8000 (a browser opens automatically).

Options: `--port 9000`, `--host 0.0.0.0`, `--no-browser`.

## What it does

1. Discovers every `session.json` under `evaluations/`, `artifacts/` and `tmp/`.
2. Pick one from the dropdown (type in the filter box to narrow), press **Apri**.
3. Playback:
   - fixed audio player (WAV/FLAC/MP3, with seeking via HTTP range requests);
   - the active sentence is highlighted and the transcript auto-scrolls;
   - the timeline shows Q/A markers (colored by confidence) and segment bands;
   - click any sentence, marker, or Q/A card to jump there.
4. Click a Q/A to open the **Q/A/C detail**: question, answer, context,
   confidence, question type, review flags, reason codes, speakers and timing,
   with "jump to question / answer" buttons.
5. **JSON** button downloads the loaded `session.json`.

Keyboard: `Space` play/pause, `←/→` seek 5s.

## "Struttura" — structured data view

The **Struttura** button (top bar, enabled once a session is loaded) opens a
full-screen, graphical view of the session JSON — an alternative to reading the
long raw text:

- overview stat cards (duration, sentences, utterances, segments, Q/A, speakers,
  languages, pipeline time) plus configuration chips;
- pipeline timing per stage showing **real time (cold or warm)** with a status
  badge (executed / cache / artifact / skipped) plus the **expected cold time**
  as a grey ghost bar behind it. The cold estimate is derived from previous
  evaluation runs of the same lesson (the oldest fully-cold run when available,
  otherwise the longest real-executed time per stage across history); stages
  never run cold in history show `cold n/d`. A run-level badge marks whether the
  loaded run itself was warm (cache reuse) or cold;
- speaker talk-time, detected languages and sentence review-priority as bars;
- Q/A distribution by confidence and by question type, plus a Q/A table with
  inline confidence bars — click a row to open it in the player;
- scrollable Segments and Sentences tables (sentences are filterable by text or
  speaker) — click a row to jump to that point in the audio;
- a **JSON grezzo** toggle to still see the formatted raw JSON when needed.

Everything is computed client-side from the already-loaded session, so it works
for any run in the picker.

## Notes on audio resolution

Audio is located from the session's `audio_sources[].audio_path`. If that
absolute path does not exist (e.g. the project moved), the server re-anchors the
part after `ExerPlazaProject/` onto the current project root, then falls back to
`artifacts/normalized_audio/<basename>`. Only audio files under the project root
or under `ExerPlazaSample/` are served.

## Nuova analisi (Milestone 2)

Click **+ Nuova analisi** in the header to run the pipeline on a new recording:

1. Drag & drop (or pick) one or more audio/video files — multiple files are
   treated as a single ordered session.
2. Set the options: session id, language (auto), segmentation
   (`structural`/`adaptive`/`both`), quality-vs-speed preset (mapped to the
   pipeline profiles `light`/`quality_local`/`quality`/`full`), alignment
   on/off, speaker diarization on/off, from-scratch (ignore cache).
3. Press **Analizza**. The pipeline runs as an external process, the modal
   shows a live step timeline (parsed from the pipeline log), and on completion
   the produced JSON opens automatically in the viewer.

### Two run modes (toggle in the options)

- **Modalità valutazione (default, ON)** — runs the project's canonical
  `scripts/run_evaluation_batch.py` for the uploaded file. This always produces
  an *evaluatable* run under `evaluations/<input_label>/runs/<run_id>/` with:

  ```
  session.json      review_packet.md      ai_review.json      metrics.json
  ```

  It mirrors the manual command:

  ```
  python scripts/run_evaluation_batch.py --resume \
    --profiles quality_local --segmentation-mode structural --pattern "*NAME*"
  ```

  Alignment/diarization follow the chosen profile; review + metrics are always
  written. `from scratch` maps to `--force-recompute` (otherwise `--resume`).
  The external-AI review file to fill in is `review_packet.md`; save the AI
  response into `ai_review.json`, then refresh the comparison with
  `lecture-analyzer-compare-evaluations evaluations/<input_label>`. One file per
  evaluatable run.

- **Modalità diretta (toggle OFF)** — the lighter `main.py` wrapper that writes
  straight to `ExerPlazaSample/output/<label>/` + `<label>_work/`. Here the
  alignment / diarization / review-packet / quality checkboxes apply, and the
  debug Excel only appears with the `full` preset.

### Where runs are saved

New runs are written into the sibling **`ExerPlazaSample/`** folder, mirroring
its existing layout:

```
ExerPlazaSample/
├── input/<file>                       # uploaded source, added only if missing
└── output/
    ├── <label>/<name>_<mode>.json     # the session JSON
    └── <label>_work/                  # per-run pipeline artifacts (normalized_audio, ...)
```

- `<label>` is the session id you type, otherwise the first input's filename
  stem. If a label already exists it is suffixed (`_2`, `_3`, ...) so nothing is
  overwritten.
- Uploaded files are placed in `ExerPlazaSample/input/` **only if a file with
  the same name is not already there**; otherwise the existing one is reused
  (which also lets the pipeline reuse the transcription cache stored next to it).
- The sample location can be overridden with the `LECTURE_UI_SAMPLE_DIR`
  environment variable.

All of these runs — plus everything already under `ExerPlazaSample/output/` and
the project's `evaluations/` — appear in the session picker, so you can always
re-open past runs, not only generate new ones.

Notes:
- The wrapper uses the project's `.venv` Python if present, otherwise the
  interpreter running the server. Heavy branches (alignment, diarization,
  semantic QA) require the corresponding ML dependencies to be installed, same
  as running the CLI directly.
- Language selection is currently `auto` only, because the pipeline CLI
  auto-detects language (it already supports mixed it/en) and exposes no
  language flag. Nothing in the existing project is modified.

## Scope

Milestones 1 (viewer) and 2 (analyze wrapper) with a live progress UI are done.
Remaining: further Q/A refinements and stand-alone packaging.
