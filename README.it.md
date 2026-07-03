# Lecture QA Extraction

## Demo Docker Rapida

Consigliata per professori, colleghi e valutazione veloce.

Requisiti minimi:

- Docker Desktop
- circa 5-10 GB liberi consigliati
- connessione internet al primo avvio per il download dei modelli

Per la guida completa passo-passo, vedi `docs/demo_docker.md`.

```bash
git clone https://github.com/matteopogetta/lecture-qa-extraction.git
cd lecture-qa-extraction
docker compose build
mkdir -p ~/Documents/LectureQASample/input ~/Documents/LectureQASample/output
# Metti il video in ~/Documents/LectureQASample/input/lecture.mp4
./scripts/run_demo_docker.sh ~/Documents/LectureQASample/input/lecture.mp4
```

## Demo Docker Full Pipeline

Usa questa modalita quando vuoi la pipeline completa, con alignment attivo,
accettando tempi e download piu pesanti.

- script helper: `./scripts/run_full_pipeline_docker.sh`
- esempi CLI diretti: `docs/demo_docker.md`
- la diarization resta opzionale e sperimentale

## Installazione Locale Python

Disponibile per sviluppo locale avanzato, ma sconsigliata per una valutazione
rapida. E piu fragile della demo Docker e dipende da `ffmpeg` e da pacchetti
ML opzionali sul sistema host.

Guida: `docs/local_installation.md`

Setup locale minimo:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
pip install -r requirements.txt
```

Dopo l'attivazione, la CLI e disponibile come:

```bash
lecture-analyzer --help
```

Per valutare qualita QA/C e tempi di esecuzione, vedi
`docs/quality_evaluation.md`.

Questo repository contiene un prototipo Python standalone per trasformare media
di lezione audio/video in artefatti JSON strutturati e tracciabili.

La regola architetturale di fondo e stabile in tutto il progetto:

- i file audio sono accettati direttamente
- i file video sono accettati solo come input
- dopo l'ingestione, tutta la pipeline interna lavora solo su audio
  normalizzato

Questo README e la mappa del progetto. Va aggiornato ogni volta che cambiano:

- l'ordine della pipeline
- i moduli o i metodi pubblici
- il contratto dell'output JSON
- i requisiti di runtime
- lo stato reale dell'implementazione

## Snapshot Del Progetto

La codebase attuale implementa una pipeline multi-stage con artefatti
intermedi persistiti:

1. Caricare uno o piu input lezione
2. Riconoscere audio o video per ogni input
3. Normalizzare ogni sorgente in un artifact audio riusabile
4. Riusare audio normalizzato valido quando disponibile
5. Trascrivere l'audio normalizzato
6. Rifinire opzionalmente il timing con WhisperX
7. Costruire le utterances allineate
8. Eseguire opzionalmente la diarization con pyannote
9. Attribuire speaker anonimi alle utterances
10. Ricostruire spans sentence-level
11. Unire i chunk di transcript in un transcript di sessione
12. Normalizzare il testo in modo conservativo
13. Segmentare il contenuto
14. Estrarre candidate Q/A
15. Esportare JSON e, opzionalmente, un file Excel di debug

Stato attuale:

- gli step da 1 a 15 sono collegati nella pipeline eseguibile
- la pipeline ufficiale del progetto vive ora sotto `src/lecture_analyzer/`
  nei sottopackage `core/`, `input/`, `preprocessing/`, `transcription/`,
  `analysis/`, `output/`
- la segmentazione usa prima di tutto il layer delle sentences e fa fallback
  sul merged transcript solo se le sentences non sono disponibili
- l'estrazione QA e sentence-aware e supporta retrieval e reranking semantici
  opzionali
- `src/lecture_analyzer/analysis/speaker_role.py` esiste ancora, ma resta un
  modulo placeholder e
  non fa parte del flusso principale
- Docker e allineato alla struttura attuale src-based

Documenti utili di stato:

- `docs/repository_status.md`
- `docs/simplification_plan.md`

Namespace pubblici attualmente disponibili:

- `lecture_analyzer.core.*`
- `lecture_analyzer.input.*`
- `lecture_analyzer.preprocessing.*`
- `lecture_analyzer.transcription.*`
- `lecture_analyzer.analysis.*`
- `lecture_analyzer.output.*`

Gli import root legacy sono stati rimossi. Gli import di progetto devono usare
solo `lecture_analyzer.*`.

## Requisiti Di Runtime

Installa le dipendenze Python:

```bash
pip install -r requirements.txt
```

Installa separatamente gli strumenti di sistema:

- `ffmpeg`
- `ffprobe`

Note:

- l'alignment richiede dipendenze compatibili con WhisperX
- la diarization richiede `pyannote.audio` e puo richiedere un token Hugging
  Face
- l'export Excel di debug richiede `openpyxl`
- il sentence splitter dei profili qualita' e `wtpsplit`; se e richiesto ma
  non disponibile, la pipeline fallisce chiaramente invece di usare fallback
  silenzioso. Il fallback a regole resta disponibile solo se scelto
  esplicitamente come backend.

### Token Hugging Face Opzionale

La diarization e opzionale e puo richiedere un token Hugging Face per modelli
pyannote gated. La pipeline controlla questi valori in ordine:

1. `PipelineConfig.diarization_auth_token`
2. `HUGGINGFACE_HUB_TOKEN`
3. `HF_TOKEN`

Per una singola sessione terminale:

```bash
export HUGGINGFACE_HUB_TOKEN="hf_xxxxxxxxxxxxxxxxx"
```

Oppure crea un file locale `.env` nella root del progetto:

```env
HUGGINGFACE_HUB_TOKEN=hf_xxxxxxxxxxxxxxxxx
```

`.env` e ignorato da Git. Non committare mai token reali.

## Avvio Rapido

Run normale:

```bash
./.venv/bin/python main.py sample_inputs --output artifacts/session.json
```

Run completa da zero:

```bash
./.venv/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --from-scratch
```

Run con segmentazione adaptive e diarization:

```bash
./.venv/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --segmentation-mode adaptive --enable-diarization
```

Run con tutte le modalita di segmentazione in una sola esecuzione:

```bash
./.venv/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --segmentation-mode both
```

Run senza alignment, mantenendo il ramo basato solo sulla trascrizione:

```bash
./.venv/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --disable-alignment
```

Run con `compute_type` esplicito per faster-whisper:

```bash
./.venv/bin/python main.py sample_inputs --output artifacts/session.json --transcription-compute-type float32
```

Profili pipeline opt-in:

- `current`: default; conserva il comportamento esistente
- `light`: disattiva i rami opzionali piu pesanti per controlli locali rapidi
- `quality`: abilita alignment e QA semantico senza diarizzazione
- `quality_local`: abilita alignment e QA locale con guardrail senza diarizzazione
- `full`: abilita i rami opzionali orientati alla massima qualita
- `diagnostic`: abilita rami di confronto e debug

Esempio:

```bash
./.venv/bin/python main.py sample_inputs --output artifacts/session.json --pipeline-profile light
```

Esporta un packet Markdown locale per revisione umana o chatbot:

```bash
./.venv/bin/python main.py sample_inputs --output artifacts/session.json --export-ai-review-packet
```

Salva una evaluation run persistente nella cartella locale ignorata
`evaluations/`:

```bash
./.venv/bin/python main.py sample_inputs --output artifacts/session.json --pipeline-profile light --export-evaluation-run
```

Usa `--pipeline-profile quality` per valutare QA semantico con alignment ma
senza diarization. Usa `--pipeline-profile quality_local` per misurare QA locale
con guardrail senza il costo di semantic retrieval/reranking. I guardrail locali
filtrano check-in di classe poco informativi, poll numerici, domande-frammento,
risposte che sono ancora domande, echo same-sentence e deferred answer lontane
con segnale debole, frammenti con bassa autonomia, contesti troppo sottili,
risposte poco responsive e rischi di same-speaker/self-continuation.

Confronta le evaluation run salvate per uno stesso input:

```bash
./.venv/bin/lecture-analyzer-compare-evaluations evaluations/icwros
```

Nota:

- `lecture-analyzer` e la CLI ufficiale
- `main.py` root resta solo un wrapper di compatibilita temporaneo
- per i nuovi import package-facing conviene preferire i namespace
  `lecture_analyzer.*`
- gli import root restano disponibili solo per retrocompatibilita

Per una demo semplice solo Docker, vedi `docs/demo_docker.md`.

## Superficie CLI

`main.py` espone la CLI operativa attuale.

Opzioni principali:

- `inputs` posizionale
- `--output`
- `--session-id`
- `--work-dir`
- `--pipeline-profile`
- `--normalized-audio-format`
- `--force-normalization`
- `--transcription-cache-dir`
- `--disable-transcription-cache`
- `--transcription-compute-type`
- `--from-scratch`
- `--disable-alignment`
- `--alignment-model`
- `--alignment-device`
- `--enable-diarization`
- `--diarization-device`
- `--num-speakers`
- `--min-speakers`
- `--max-speakers`
- `--segmentation-mode`
- `--export-ai-review-packet`
- `--ai-review-packet-path`
- `--export-evaluation-run`
- `--evaluation-root`
- `--evaluation-input-label`
- `--evaluation-run-label`

Comportamenti importanti:

- la modalita normale puo riusare cache e artefatti intermedi compatibili
- `--from-scratch` disabilita il riuso e forza il ricalcolo
- la preparazione del transcript viene eseguita una sola volta anche quando si
  richiedono piu modalita di segmentazione
- se `--output` non viene fornito, il processamento avviene comunque ma export
  JSON e debug Excel vengono saltati

## Comportamento Attuale Della Pipeline

### 1. Caricamento input

`src/lecture_analyzer/input/session_loader.py`

Responsabilita:

- accettare un file, molti file o una directory
- preservare l'ordine esplicito dei path
- espandere le directory solo al primo livello
- ordinare deterministicamente i file trovati in directory
- ignorare i file non supportati dentro le directory e registrarli nei metadati
  di sessione
- evitare di reinterpretare come input i sidecar generati dal progetto

Metodi pubblici principali:

- `load_session(input_paths, session_id=None) -> LectureSession`
- `detect_media_type(path: Path) -> MediaType`

### 2. Normalizzazione audio

`src/lecture_analyzer/preprocessing/audio_normalizer.py`

Responsabilita:

- convertire audio o video in un singolo asset audio normalizzato per sorgente
- validare le proprieta tecniche con `ffprobe`
- salvare un sidecar di metadati
- riusare un artifact esistente solo se fingerprint della sorgente e contratto
  tecnico coincidono ancora

Contratto normalizzato di default:

- mono
- 16000 Hz
- 16-bit
- `wav`

Output opzionale:

- `flac`

Metodi pubblici principali:

- `normalize_sources(input_sources) -> list[AudioSource]`
- `normalize_source(source: InputSource) -> AudioSource`

### 3. Trascrizione

`src/lecture_analyzer/transcription/backend.py`

Responsabilita:

- isolare la logica specifica del backend STT
- mantenere il resto della pipeline backend-agnostic

Backend attuale:

- `faster-whisper`

`src/lecture_analyzer/transcription/transcriber.py`

Responsabilita:

- trascrivere ogni sorgente audio normalizzata
- riusare artefatti di cache compatibili
- costruire gli oggetti condivisi `TranscriptChunk`

Metodi pubblici principali:

- `transcribe_session(session) -> list[TranscriptChunk]`
- `transcribe_sources(audio_sources) -> list[TranscriptChunk]`
- `transcribe_source(audio_source) -> list[TranscriptChunk]`

### 4. Alignment

`src/lecture_analyzer/transcription/whisperx_aligner.py`

Responsabilita:

- rifinire il timing ASR a livello segmento e parola
- persistire un artifact di alignment riusabile per sorgente
- degradare in modo controllato sui fallimenti per singola sorgente

Metodi pubblici principali:

- `align_session(session) -> list[AlignedTranscript]`
- `align_source(audio_source, transcript_chunks) -> AlignedTranscript`

### 5. Utterance building

`src/lecture_analyzer/analysis/utterance_builder.py`

Responsabilita:

- costruire utterances tracciabili a partire da segmenti e parole allineate
- usare gap temporali tra parole come euristica di split
- persistire artefatti utterance riusabili

Metodi pubblici principali:

- `build_session(session) -> list[Utterance]`
- `build_source(audio_source, aligned_transcript) -> UtteranceCollection`

### 6. Diarization

`src/lecture_analyzer/transcription/pyannote_diarizer.py`

Responsabilita:

- eseguire la diarization opzionale su audio normalizzato
- persistire artefatti di diarization riusabili
- normalizzare forme diverse dell'output pyannote
- preferire, quando disponibile e configurato, l'output exclusive

Metodi pubblici principali:

- `diarize_session(session) -> list[DiarizationSegment]`
- `diarize_source(audio_source) -> DiarizationResult`

Comportamento token:

- usa `PipelineConfig.diarization_auth_token` se impostato
- altrimenti controlla `HUGGINGFACE_HUB_TOKEN` e `HF_TOKEN`

### 7. Speaker attribution

`src/lecture_analyzer/analysis/speaker_attribution.py`

Responsabilita:

- assegnare speaker anonimi alle utterances usando l'overlap con la
  diarization
- scartare casi con overlap basso o ambiguo
- usare euristiche di qualita audio per evitare flip fragili
- smussare cambi speaker troppo brevi o degradati

Metodi pubblici principali:

- `attribute_session(session) -> list[Utterance]`
- `attribute_utterance(utterance, diarization_segments, audio_source)`

Moduli di supporto:

- `src/lecture_analyzer/analysis/audio_quality.py`
- `src/lecture_analyzer/analysis/speaker_stability.py`

### 8. Sentence reconstruction

`src/lecture_analyzer/analysis/sentence_reconstruction.py`

Responsabilita:

- ricostruire spans sentence-level dalle utterances finali
- mantenere la provenance verso le utterances sorgente
- usare `wtpsplit`/SaT quando disponibile
- usare un fallback conservativo a regole quando necessario
- consolidare l'evidenza speaker a livello frase
- persistire artefatti sentence riusabili

Metodi pubblici principali:

- `reconstruct_session(session) -> list[Sentence]`
- `build_source(audio_source, utterances) -> SentenceCollection`

Validazione di supporto:

- `src/lecture_analyzer/analysis/sentence_provenance.py`

### 9. Merge e normalizzazione transcript

`src/lecture_analyzer/transcription/transcript_merger.py`

Responsabilita:

- costruire un ordinamento deterministico del transcript a livello sessione
- preservare la tracciabilita dei chunk
- mantenere visibili overlap e anomalie invece di nasconderli

`src/lecture_analyzer/transcription/transcript_normalizer.py`

Responsabilita:

- applicare solo pulizia formale conservativa
- preservare il contenuto raw per auditabilita

### 10. Segmentazione

`src/lecture_analyzer/analysis/segmenter.py`

Responsabilita:

- costruire segmenti di contenuto di livello piu alto
- lavorare principalmente sulle sentences ricostruite
- fare fallback esplicito sui merged transcript units quando il layer sentence
  manca

Modalita supportate:

- `structural`
- `windowed`
- `adaptive`
- `both` a livello CLI, che fa fan-out verso tutte e tre le modalita di export

Metodi pubblici principali:

- `resolved_mode(mode=None) -> str`
- `segment_session(session, mode=None) -> list[Segment]`

### 11. Estrazione QA

`src/lecture_analyzer/analysis/qa_extractor.py`

Responsabilita:

- rilevare candidate domanda dalle sentences
- cercare risposte locali in modo deterministico
- estendere opzionalmente la ricerca con retrieval semantico
- riordinare opzionalmente le candidate con un reranker semantico
- preservare grounding verso sentences, utterances, segments e timing

Metodo pubblico principale:

- `extract(session) -> list[QAPairCandidate]`

Moduli di supporto:

- `src/lecture_analyzer/analysis/qa_rules.py`
- `src/lecture_analyzer/analysis/semantic_retrieval.py`
- `src/lecture_analyzer/analysis/semantic_reranking.py`

### 12. Export

`src/lecture_analyzer/output/json_exporter.py`

Responsabilita:

- serializzare il risultato completo di sessione
- derivare filename deterministici dalla sessione e dalla modalita di
  segmentazione

Metodi pubblici principali:

- `export(session, output_path, segmentation_mode=None) -> Path`
- `export_many(sessions_by_mode, output_path=None) -> dict[str, Path]`

`src/lecture_analyzer/output/debug_excel_exporter.py`

Responsabilita:

- costruire un workbook per revisione umana a partire dal JSON esportato
- esporre diagnostiche su utterances, sentences, QA, summary e provenance

Modulo di supporto:

- `src/lecture_analyzer/output/sentence_provenance_validation.py`

## Layer Core Condiviso

### `src/lecture_analyzer/core/config.py`

Scopo:

- configurazione runtime centrale
- convenzioni di path
- impostazioni di normalizzazione, trascrizione, alignment, diarization,
  sentence reconstruction, QA ed export

API pubblica principale:

- `PipelineConfig`
- `ensure_working_directories()`
- `audio_artifacts_directory`
- `alignment_artifacts_directory`
- `diarization_artifacts_directory`
- `utterance_artifacts_directory`
- `sentence_artifacts_directory`
- `pipeline_execution_mode`

### `src/lecture_analyzer/core/models.py`

Scopo:

- modello dati tipizzato usato da tutto il progetto
- contratto di serializzazione verso il JSON

Famiglie di modelli principali:

- input e audio: `InputSource`, `AudioSource`
- transcript: `TranscriptChunk`, `MergedTranscript`, `MergedTranscriptUnit`
- alignment: `AlignedTranscript`, `AlignedTranscriptSegment`, `AlignedWord`
- layer speaker e sentence: `Utterance`, `Sentence`, `DiarizationSegment`
- output di analisi: `Segment`, `QAPairCandidate`, `SpeakerRoleEstimate`
- dati operativi: `PipelineStageTiming`, `PipelineTiming`
- aggregato principale: `LectureSession`

### `src/lecture_analyzer/core/pipeline.py`

Scopo:

- coordinare il flusso eseguibile end-to-end
- misurare i timing degli stage
- fare fan-out delle segmentation modes
- attivare export JSON e debug Excel

Metodi pubblici principali:

- `ingest(input_paths, session_id=None) -> LectureSession`
- `transcribe(session) -> LectureSession`
- `align_transcript(session) -> LectureSession`
- `build_utterances(session) -> LectureSession`
- `diarize_speakers(session) -> LectureSession`
- `attribute_utterance_speakers(session) -> LectureSession`
- `reconstruct_sentences(session) -> LectureSession`
- `post_process_transcript(session) -> LectureSession`
- `segment_transcript(session, segmentation_mode=None) -> LectureSession`
- `extract_qa_candidates(session) -> LectureSession`
- `process(input_paths, output_path=None, session_id=None)`

### `src/lecture_analyzer/core/timing.py`

Scopo:

- timing operativo leggero per gli stage principali della pipeline
- misure di durata con clock monotonic e timestamp UTC leggibili

## Struttura Dell'Output JSON

Il JSON esportato e organizzato per layer di processamento.

Sezioni top-level attuali:

- `session_metadata`
- `input_sources`
- `audio_sources`
- `transcript`
- `merged_transcript`
- `transcript_chunks`
- `aligned_transcripts`
- `diarization_segments`
- `utterances`
- `sentences`
- `segments`
- `speaker_role_estimates`
- `qa_candidates`
- `pipeline_timing`

Le note sul contratto attuale vivono in:

- `docs/schema_notes.md`

Versione di schema esportata da `LectureSession`:

- `0.6.0`

## Riuso Artefatti E Working Directories

La pipeline persiste piu famiglie di artefatti riusabili.

Famiglie attuali:

- audio normalizzato
- cache di trascrizione
- alignment
- diarization
- utterances
- sentences

Layout di default della working directory:

- `artifacts/normalized_audio`
- `artifacts/alignment`
- `artifacts/diarization`
- `artifacts/utterances`
- `artifacts/sentences`

Regole di riuso:

- la modalita normale puo riusare cache e artefatti compatibili
- `--from-scratch` disabilita il riuso e forza il ricalcolo
- ogni stage registra se ha riusato cache, riusato un artifact o eseguito da
  zero

## Osservabilita

La sezione `pipeline_timing` esportata registra dati strutturati sugli stage.

Famiglie di stage tracciate:

- loading e normalizzazione
- trascrizione
- alignment
- utterance building
- diarization
- speaker attribution
- sentence reconstruction
- transcript post-processing
- segmentazione
- QA extraction
- export JSON
- export debug Excel
- esecuzione totale della pipeline

Il summary dei timing espone:

- modalita di esecuzione della pipeline
- profilo di run come cold, warm, mixed o forced recompute
- durata totale
- conteggi per stato
- presenza o meno di riuso cache/artifact
- stage piu costoso

## Test

La directory `tests/` copre i layer principali attuali:

- normalizzazione audio
- backend e cache di trascrizione
- alignment WhisperX
- diarization pyannote
- utterance building
- speaker attribution e stabilizzazione
- sentence reconstruction e validazione della provenance
- segmentazione
- estrazione QA
- export Excel di debug
- pipeline timing
- parsing CLI

Nota pratica di verifica locale:

- `pytest -q` puo fallire se la root del progetto non e nel `PYTHONPATH`
- la suite attuale passa con:

```bash
PYTHONPATH=. pytest -q
```

## Limiti Noti

- `src/lecture_analyzer/analysis/speaker_role.py` e ancora un placeholder e
  non e integrato nel
  flusso eseguibile
- il contratto JSON e un contratto tecnico di lavoro, non ancora un JSON
  Schema formale
- gli stage avanzati dipendono da stack runtime piu pesanti e in parte
  opzionali
- alcune regolazioni avanzate esistono in `PipelineConfig` ma non sono ancora
  esposte via CLI

## Riferimenti Rapidi Nel Repository

File e cartelle utili:

- `main.py`
- `src/lecture_analyzer/core/`
- `src/lecture_analyzer/input/`
- `src/lecture_analyzer/preprocessing/`
- `src/lecture_analyzer/transcription/`
- `src/lecture_analyzer/analysis/`
- `src/lecture_analyzer/output/`
- `tests/`
- `sample_inputs/`
- `SNAPSHOT_PROGETTO.md`

## Regola Di Manutenzione

Quando una modifica al codice cambia il comportamento visibile del progetto,
aggiorna anche questo README nello stesso commit, soprattutto se cambia:

- l'ordine della pipeline
- lo stato di implementazione
- l'API pubblica
- la struttura del JSON esportato
- i requisiti di runtime
