# Prototipo Di Elaborazione Lezioni

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
- la pipeline ufficiale del progetto vive oggi nei moduli root `core/`,
  `input/`, `preprocessing/`, `transcription/`, `analysis/`, `output/`
- la segmentazione usa prima di tutto il layer delle sentences e fa fallback
  sul merged transcript solo se le sentences non sono disponibili
- l'estrazione QA e sentence-aware e supporta retrieval e reranking semantici
  opzionali
- `analysis/speaker_role.py` esiste ancora, ma resta un modulo placeholder e
  non fa parte del flusso principale
- `src/lecture_analyzer` non e ancora la pipeline principale: oggi e un
  bootstrap/package skeleton e la destinazione futura della migrazione
- Docker ora include anche i moduli root reali e supporta la CLI ufficiale e
  la smoke mode, ma resta un setup transitorio in vista della migrazione
  src-based

Documenti utili di stato:

- `docs/repository_status.md`
- `docs/simplification_plan.md`

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
- il sentence splitter preferito e `wtpsplit`; se non e disponibile la
  pipeline usa un fallback conservativo a regole

## Avvio Rapido

Run normale:

```bash
./.venv-system/bin/python main.py sample_inputs --output artifacts/session.json
```

Run completa da zero:

```bash
./.venv-system/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --from-scratch
```

Run con segmentazione adaptive e diarization:

```bash
./.venv-system/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --segmentation-mode adaptive --enable-diarization
```

Run con tutte le modalita di segmentazione in una sola esecuzione:

```bash
./.venv-system/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --segmentation-mode both
```

Run senza alignment, mantenendo il ramo basato solo sulla trascrizione:

```bash
./.venv-system/bin/python main.py sample_inputs/SSL1P1.mp4 --output artifacts/session.json --disable-alignment
```

Run con `compute_type` esplicito per faster-whisper:

```bash
./.venv-system/bin/python main.py sample_inputs --output artifacts/session.json --transcription-compute-type float32
```

Nota:

- `main.py` root resta temporaneamente il wrapper ufficiale
- `lecture-analyzer` e la CLI ufficiale, anche se passa ancora attraverso il
  layer transitorio `src/lecture_analyzer` mentre la pipeline reale resta nei
  moduli root

## Superficie CLI

`main.py` espone la CLI operativa attuale.

Opzioni principali:

- `inputs` posizionale
- `--output`
- `--session-id`
- `--work-dir`
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

Comportamenti importanti:

- la modalita normale puo riusare cache e artefatti intermedi compatibili
- `--from-scratch` disabilita il riuso e forza il ricalcolo
- la preparazione del transcript viene eseguita una sola volta anche quando si
  richiedono piu modalita di segmentazione
- se `--output` non viene fornito, il processamento avviene comunque ma export
  JSON e debug Excel vengono saltati

## Comportamento Attuale Della Pipeline

### 1. Caricamento input

`input/session_loader.py`

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

`preprocessing/audio_normalizer.py`

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

`transcription/backend.py`

Responsabilita:

- isolare la logica specifica del backend STT
- mantenere il resto della pipeline backend-agnostic

Backend attuale:

- `faster-whisper`

`transcription/transcriber.py`

Responsabilita:

- trascrivere ogni sorgente audio normalizzata
- riusare artefatti di cache compatibili
- costruire gli oggetti condivisi `TranscriptChunk`

Metodi pubblici principali:

- `transcribe_session(session) -> list[TranscriptChunk]`
- `transcribe_sources(audio_sources) -> list[TranscriptChunk]`
- `transcribe_source(audio_source) -> list[TranscriptChunk]`

### 4. Alignment

`transcription/whisperx_aligner.py`

Responsabilita:

- rifinire il timing ASR a livello segmento e parola
- persistire un artifact di alignment riusabile per sorgente
- degradare in modo controllato sui fallimenti per singola sorgente

Metodi pubblici principali:

- `align_session(session) -> list[AlignedTranscript]`
- `align_source(audio_source, transcript_chunks) -> AlignedTranscript`

### 5. Utterance building

`analysis/utterance_builder.py`

Responsabilita:

- costruire utterances tracciabili a partire da segmenti e parole allineate
- usare gap temporali tra parole come euristica di split
- persistire artefatti utterance riusabili

Metodi pubblici principali:

- `build_session(session) -> list[Utterance]`
- `build_source(audio_source, aligned_transcript) -> UtteranceCollection`

### 6. Diarization

`transcription/pyannote_diarizer.py`

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

`analysis/speaker_attribution.py`

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

- `analysis/audio_quality.py`
- `analysis/speaker_stability.py`

### 8. Sentence reconstruction

`analysis/sentence_reconstruction.py`

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

- `analysis/sentence_provenance.py`

### 9. Merge e normalizzazione transcript

`transcription/transcript_merger.py`

Responsabilita:

- costruire un ordinamento deterministico del transcript a livello sessione
- preservare la tracciabilita dei chunk
- mantenere visibili overlap e anomalie invece di nasconderli

`transcription/transcript_normalizer.py`

Responsabilita:

- applicare solo pulizia formale conservativa
- preservare il contenuto raw per auditabilita

### 10. Segmentazione

`analysis/segmenter.py`

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

`analysis/qa_extractor.py`

Responsabilita:

- rilevare candidate domanda dalle sentences
- cercare risposte locali in modo deterministico
- estendere opzionalmente la ricerca con retrieval semantico
- riordinare opzionalmente le candidate con un reranker semantico
- preservare grounding verso sentences, utterances, segments e timing

Metodo pubblico principale:

- `extract(session) -> list[QAPairCandidate]`

Moduli di supporto:

- `analysis/qa_rules.py`
- `analysis/semantic_retrieval.py`
- `analysis/semantic_reranking.py`

### 12. Export

`output/json_exporter.py`

Responsabilita:

- serializzare il risultato completo di sessione
- derivare filename deterministici dalla sessione e dalla modalita di
  segmentazione

Metodi pubblici principali:

- `export(session, output_path, segmentation_mode=None) -> Path`
- `export_many(sessions_by_mode, output_path=None) -> dict[str, Path]`

`output/debug_excel_exporter.py`

Responsabilita:

- costruire un workbook per revisione umana a partire dal JSON esportato
- esporre diagnostiche su utterances, sentences, QA, summary e provenance

Modulo di supporto:

- `output/sentence_provenance_validation.py`

## Layer Core Condiviso

### `core/config.py`

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

### `core/models.py`

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

### `core/pipeline.py`

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

### `core/timing.py`

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

- `output/schema_notes.md`

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

- `analysis/speaker_role.py` e ancora un placeholder e non e integrato nel
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
- `core/`
- `input/`
- `preprocessing/`
- `transcription/`
- `analysis/`
- `output/`
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
