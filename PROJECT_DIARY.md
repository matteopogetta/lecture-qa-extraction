# ExerPlaza Project Diary

Questo file e' il diario operativo del progetto. Deve essere aggiornato quando
vengono prese decisioni tecniche rilevanti, introdotte regole logiche,
modificati parametri, aggiunti profili di pipeline, cambiati workflow di
valutazione o fatte scelte che in futuro potrebbero spiegare perche' una run ha
funzionato meglio o peggio di un'altra.

Regola globale di progetto:

- Ogni sezione deve avere una data.
- Ogni modifica importante deve indicare cosa e' stato cambiato, perche', quali
  parametri sono stati scelti e quale effetto atteso ha sulla qualita', sul
  tempo di esecuzione o sulla manutenibilita'.
- Non inserire token, segreti, credenziali o dati sensibili.
- Le valutazioni e gli output locali restano in `evaluations/` e non devono
  essere pubblicati su GitHub.
- Il progetto canonico e' solo
  `/Users/matteopogetta/Documents/ExerPlazaProject`.
- Il path
  `/Users/matteopogetta/.codex/worktrees/f124/ExerPlazaProject`
  e' deprecato e non deve essere usato per modifiche progettuali.

## 2026-06-21 - Consolidamento progetto, Git e direzione pipeline

Il progetto e' stato portato verso una forma piu' ordinata e condivisibile con
Git. La logica esistente e' stata congelata su branch dedicato, evitando di
lavorare direttamente sul principale mentre si introducono modifiche alla
pipeline.

Decisione:

- Usare un branch unico per le prime fasi di stabilizzazione, almeno fino alla
  Fase 3, invece di creare molti branch piccoli.
- Branch di lavoro: `codex/pipeline-stabilization`.
- Obiettivo: preservare compatibilita' di input/output mentre si migliora la
  pipeline interna.

Motivazione:

- Ridurre la complessita' di gestione Git.
- Mantenere confrontabile la pipeline prima/dopo.
- Evitare cambiamenti all'interfaccia esterna finche' non sono necessari.

## 2026-06-21 - Chiarimento ambiente virtuale

E' stato chiarito che `.venv` e' una cartella locale dentro il progetto:

`/Users/matteopogetta/Documents/ExerPlazaProject/.venv`

Decisione:

- I comandi documentati devono assumere che l'utente sia gia' nella virtualenv
  quando il prompt mostra `(.venv)`.
- Evitare comandi del tipo `.venv/bin/python` quando l'utente lancia dal
  terminale gia' attivato.
- Non usare `.venv-system` nei README: quel path non esiste nel setup attuale.

Motivazione:

- Ridurre confusione tra virtualenv del progetto e Python di sistema/conda.
- Rendere i comandi piu' copiabili dal terminale reale dell'utente.

Nota:

- La cartella `.venv` non rende automaticamente il progetto pronto per chiunque
  lo scarichi. Un'altra persona dovra' creare/attivare un ambiente Python e
  installare le dipendenze.

## 2026-06-21 - Token Hugging Face

E' stata documentata la necessita' del token Hugging Face per componenti che
usano modelli gated, in particolare la diarizzazione Pyannote.

Regola:

- Il token non deve essere scritto nei file del progetto.
- Deve essere impostato come variabile d'ambiente nel terminale o in un file
  locale non versionato.

Motivazione:

- Privacy e sicurezza.
- Evitare che segreti finiscano su GitHub.

## 2026-06-21 - Struttura valutazioni locali

E' stata introdotta la struttura locale:

```text
evaluations/
  <input_label>/
    runs/
      <auto_run_label>/
        session.json
        review_packet.md
        ai_review.json
        metrics.json
    comparison.json
    comparison.md
```

Decisione:

- `evaluations/` e' la radice stabile per storico e valutazioni.
- `evaluations/` resta locale e ignorata da Git.
- Ogni run ha un'identita' unica generata automaticamente.
- `ai_review.json` e' il punto in cui salvare la valutazione AI esterna.
- `metrics.json` contiene identita' run, profilo pipeline, parametri, snapshot
  Git, timing e metriche oggettive.

Motivazione:

- Rendere confrontabili nel tempo run diverse dello stesso input.
- Separare output locali/sensibili dal codice versionato.
- Permettere analisi future sulle run migliori, anche quando i parametri non
  servono alla valutazione immediata.

## 2026-06-21 - Commit Fase 2

Fase 2 completata e committata:

- Commit: `e47596b Add local evaluation workflow`.

Contenuto logico:

- Export locale delle run di valutazione.
- Packet Markdown per revisione AI/umana.
- `metrics.json` con timing, configurazione e snapshot codice.
- Comparatore per run dello stesso input.

## 2026-06-22 - Obiettivo qualitativo del progetto

Obiettivo dichiarato:

- Estrarre Q/A di qualita' nel minor tempo possibile.
- Passare da Q/A a Q/A/C, aggiungendo un contesto leggibile che renda chiaro il
  senso della coppia domanda/risposta.
- Preferire strumenti locali, open source, senza token a pagamento dove
  possibile, stabili e con bassa manutenzione.
- Tenere i dati in locale per privacy e riservatezza.

Conseguenza progettuale:

- La qualita' non va misurata solo con numeri oggettivi.
- Serve anche una valutazione AI esterna o umana che legga trascrizione,
  Q/A/C, contesto e significato.

## 2026-06-22 - Valutazione esterna AI

E' stato predisposto un workflow in cui un chatbot esterno, per esempio Claude,
legge le run in `evaluations/`, valuta solo quelle senza `ai_review.json`
completato, e scrive l'output nella posizione corretta.

Regole per la valutazione esterna:

- Non mantenere memoria tra una run e l'altra.
- Non rivalutare run gia' valutate.
- Considerare `review_packet.md`, `session.json`, `metrics.json` e poi scrivere
  `ai_review.json`.
- Non leggere file fuori dalla struttura di valutazione indicata.
- Non usare file locali sensibili o irrilevanti.

Motivazione:

- Rendere ripetibile la revisione.
- Evitare bias tra una valutazione e l'altra.
- Automatizzare senza copiare manualmente lunghi contenuti in chat.

## 2026-06-22 - Timing, cache e run cold/warm

E' stato deciso che la valutazione delle performance deve distinguere:

- Run cold: tempo realistico senza riuso artefatti.
- Run warm/cache: tempo con audio, trascrizione, alignment o altri stadi gia'
  disponibili.
- Tempo ricostruito: stima del tempo che sarebbe servito senza cache, usando
  riferimenti cold disponibili per lo stesso input/stadio.

Regola:

- Le prime fasi possono essere a tempo quasi zero quando riusano artefatti.
- Questo non deve far credere che la pipeline completa sia realmente cosi'
  veloce.
- Il confronto deve indicare chiaramente cache hit, artifact reuse e stadi
  realmente eseguiti.

Motivazione:

- Evitare false valutazioni di velocita'.
- Confrontare profili diversi in modo equo.

## 2026-06-22 - Risultato primo confronto Light vs Full

Creati localmente:

- `evaluations/performance_overview.md`
- `evaluations/performance_overview.json`

Risultati medi osservati:

- Light quality media: `2.50`.
- Full quality media: `2.83`.
- Delta medio full-light: `+0.33`.
- Costo cold medio full/light: `4.37x`.

Conclusione:

- `full` migliora poco rispetto al costo.
- `full` non deve essere il default operativo.
- `full` resta utile come riferimento diagnostico o high precision.

## 2026-06-22 - Profilo `quality`

E' stato introdotto un profilo intermedio `quality`.

Parametri principali:

- `transcript_alignment_enabled=True`
- `diarization_enabled=False`
- `qa_answer_search_strategy="semantic_retrieval"`
- `qa_semantic_retrieval_enabled=True`
- `qa_answer_ranking_strategy="semantic_reranker"`
- `qa_semantic_reranking_enabled=True`
- `export_debug_excel=False`

Motivazione:

- Cercare di avvicinare la qualita' del `full` evitando il costo enorme della
  diarizzazione.
- Conservare alignment e modelli semantici per migliorare QA/C.
- Tenere bassa la produzione di file diagnostici quando non necessaria.

Test eseguiti al momento dell'introduzione:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_pipeline_config tests.test_main_cli
```

Risultato: 14 test OK.

## 2026-06-22 - Batch runner

Creato script:

`scripts/run_evaluation_batch.py`

Default:

- Input: `/Users/matteopogetta/Documents/ExerPlazaSample/input`
- Output: `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_<timestamp>`
- Evaluation root: `/Users/matteopogetta/Documents/ExerPlazaProject/evaluations`
- Profili default: `light full`
- Segmentazione default: `structural`
- Python default: `python`, assumendo virtualenv gia' attiva.

Regola:

- Non togliere alignment e diarizzazione dal profilo `full`.
- Non usare opzioni tipo `--disable-diarization` per il full di riferimento.
- Il batch deve fare preflight degli import per `full`, cosi' non produce run
  degradate quando alignment o diarizzazione non partono.

Motivazione:

- Automatizzare run su piu' file senza ripetizione manuale.
- Evitare valutazioni false dovute a full non realmente full.

## 2026-06-22 - Problemi ambiente risolti

Problema `tokenizers`:

- `tokenizers==0.23.1` non era compatibile con il vincolo richiesto da
  Transformers nel setup corrente: `tokenizers>=0.22.0,<=0.23.0`.
- `tokenizers==0.23.0` non risultava disponibile stabile.
- Soluzione consigliata: usare una versione `<0.23.0`, per esempio `0.22.2`.

Problema `protobuf`:

- Pyannote falliva con:
  `cannot import name 'api_implementation' from 'google.protobuf.internal'`.
- Installato `protobuf 4.25.9`.
- Verifica riuscita:
  `pyannote.audio OK`, `whisperx alignment OK`.

Nota ambiente:

- Il prompt mostra `(.venv) (base)`, quindi la virtualenv e' attiva dentro
  conda base.
- Sono presenti warning AVFoundation/ffmpeg duplicati tra Anaconda e `.venv`.
  Non hanno bloccato le run, ma indicano un potenziale conflitto runtime.

## 2026-06-22 - Pulizia run fallite

Sono state eliminate vecchie run `full` fallite/degradate che avevano stage
alignment/diarization falliti.

Regola:

- Una run con stage `failed` non deve essere considerata valida per resume o
  confronto.

Motivazione:

- Evitare che una run parzialmente degradata venga confrontata come se fosse un
  profilo completo.

## 2026-06-22 - Risultato Phase 3 Quality Overview

Creati localmente:

- `evaluations/phase3_quality_overview.md`
- `evaluations/phase3_quality_overview.json`

Risultati medi:

- Avg full quality: `2.83`
- Avg quality structural quality: `2.33`
- Avg quality adaptive quality: `2.33`
- Quality structural minus full: `-0.50`
- Quality adaptive minus full: `-0.50`
- Quality structural/full cold runtime ratio: `0.28x`
- Quality adaptive/full cold runtime ratio: `0.28x`

Conclusione:

- `quality` costa molto meno del `full`, ma non supera la qualita' del `full`.
- `adaptive` non migliora rispetto a `structural`.
- Non conviene investire subito su `adaptive`.
- Il prossimo lavoro deve concentrarsi su `qa_extraction` e sulla qualita' del
  contesto.

## 2026-06-22 - Audit cache per quality

Controllo sulle run `quality` structural/adaptive:

- `transcript_segmentation`: eseguito.
- `qa_extraction`: eseguito.
- Stadi a monte spesso riusati da cache/artefatti:
  audio normalization, transcription, alignment, utterance building.
- In alcune adaptive `sentence_reconstruction` e' stata riusata.

Interpretazione:

- Per test su segmentazione e QA, la cache a monte e' accettabile.
- Se si modifica sentence reconstruction o alignment, bisogna invalidare cache
  o forzare recompute.

## 2026-06-22 - Failure mode principali dalla review AI esterna

Pattern ricorrenti nelle review:

- Domande dichiarative o retoriche promosse a domande vere.
- `deferred_answer_search` troppo permissiva, con risposte lontane 100-220s.
- Risposte topically adjacent ma non realmente responsive.
- Contesti vuoti o quasi vuoti.
- Risposte che sono hand-off del moderatore o boilerplate.
- Risposte che ripetono la domanda invece di risponderla.
- Sentence reconstruction spesso in fallback per `wtpsplit` non disponibile o
  fallito.
- Senza diarizzazione, i panel multi-speaker soffrono per assenza di speaker
  boundary.

Decisione:

- Prima di fare nuove run costose, intervenire in `qa_extraction`.
- Non fare una nuova batteria completa finche' non sono introdotti guardrail sui
  failure mode principali.

## 2026-06-23 - Guardrail QA extraction

File modificato:

`src/lecture_analyzer/analysis/_qa_extractor_impl.py`

Scopo:

- Ridurre rumore nei candidati Q/A/C prima della prossima valutazione AI
  esterna.
- Migliorare qualita' senza aggiungere dipendenze o cambiare interfaccia
  input/output.

Regole logiche introdotte:

1. Penalita' per domande check-in retoriche:

   Esempi:

   - `ci siamo`
   - `che cos e`
   - `lo conoscete`
   - `ok`
   - `okay`
   - `right`
   - `vero`

   Parametro implementato:

   - Penalita': `-0.28`.

   Motivazione:

   - Le review AI hanno segnalato che check-in di classe e domande retoriche
     diventavano falsi candidati didattici.

2. Penalita' per domande implicite dichiarative:

   Regola:

   - Se manca `?` e la frase non parte con parola interrogativa, applicare
     penalita'.

   Parametro:

   - Penalita': `-0.18`.

   Motivazione:

   - Molte frasi dichiarative venivano promosse a domanda tramite cue deboli.

3. Quality gate sulle risposte:

   Penalita':

   - Hand-off/moderator answer: `-0.22`.
   - Boilerplate/filler answer: `-0.14`.
   - Same-sentence answer che coincide con la frase domanda: `-0.16`.
   - Answer che ecoa quasi solo la domanda: `-0.16`.

   Motivazione:

   - Le review AI hanno segnalato risposte tipo passaggi al moderatore,
     filler, audience cross-talk o risposte che ripetono la domanda.

4. Deferred answer search piu' conservativa:

   Regole:

   - Ignorare match basati solo su keyword deboli.
   - Bloccare candidate hand-off/moderator.
   - Richiedere segnale piu' forte se la risposta e' lontana.

   Keyword deboli escluse dal segnale forte:

   - `answer`
   - `ask`
   - `question`
   - `questions`
   - `thing`
   - `things`
   - `think`
   - `towards`
   - `want`

   Soglie:

   - Se `distance_units > 12`, richiedere `signal_score >= 0.24` oppure almeno
     2 keyword forti.
   - Se `gap_seconds > 90.0`, richiedere `signal_score >= 0.28` oppure almeno
     2 keyword forti.

   Motivazione:

   - Le run quality sbagliavano spesso scegliendo risposte lontane solo per
     sovrapposizione lessicale debole.

5. Fallback context:

   Regola:

   - Se il context resta vuoto, recuperare fino a due unita' precedenti non
     filler come `fallback_previous_context`.

   Parametri:

   - `context_strategy="fallback_previous_context"`
   - `context_confidence="low"`

   Motivazione:

   - Molte Q/A/C avevano C vuoto, rendendo difficile la revisione umana o AI.
   - Il fallback e' intenzionalmente marcato low confidence per non nascondere
     l'incertezza.

Test aggiunti:

- Check-in retorico non produce candidato.
- Deferred search ignora ricorrenza lontana basata solo su keyword deboli.
- Deferred context usa fallback vicino quando manca overlap tematico.

Verifiche:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_pipeline_config tests.test_main_cli
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/private/tmp/exerplaza_pycache python -m py_compile scripts/run_evaluation_batch.py
```

Risultati:

- 42 test mirati OK.
- 139 test discovery OK.
- `run_evaluation_batch.py` compila OK con pycache spostata in `/private/tmp`.

## 2026-06-23 - Correzione resume batch

File modificato:

`scripts/run_evaluation_batch.py`

Problema:

- `--resume` saltava run vecchie quando input/profilo/segmentazione coincidevano,
  anche se il codice era cambiato.

Decisione:

- `--resume` deve saltare solo run valide prodotte dallo stesso snapshot codice.

Regola implementata:

- La run esistente e' riusabile solo se:
  - profilo uguale;
  - segmentation mode uguale;
  - nessuno stage `failed`;
  - `git_commit` uguale;
  - stato dirty uguale;
  - `git_status_short` uguale.

Motivazione:

- Evitare false valutazioni dopo modifiche alla QA extraction.
- Se il codice cambia, una run vecchia non deve essere considerata equivalente.

Nota:

- Finche' il working tree e' dirty, lo snapshot include anche lo stato dirty.
  Questo rende il resume prudente durante esperimenti non ancora committati.

## 2026-06-23 - Prossima valutazione consigliata

Non lanciare subito tutto il batch.

Run sentinella consigliate:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality --segmentation-mode structural --pattern "*Deep Time*"
python scripts/run_evaluation_batch.py --resume --profiles quality --segmentation-mode structural --pattern "*Stanford*"
python scripts/run_evaluation_batch.py --resume --profiles quality --segmentation-mode structural --pattern "*L25P09*"
```

Motivazione:

- `Deep Time`: panel multi-speaker, molto sensibile a deferred search e
  hand-off.
- `Stanford`: Q/A reale con audience, utile per testare risposte lontane e
  cross-talk.
- `L25P09`: caso con frammenti, italiano/ASR noise e problemi di contesto.

Criteri di successo prima di estendere il batch:

- Meno reject dovuti a risposte lontane non responsive.
- Meno context vuoti.
- Meno domande dichiarative/retoriche.
- Qualita' `quality structural` piu' vicina a `full`.
- Runtime QA non significativamente peggiorato.

## 2026-06-23 - Esito run sentinella e secondo micro-ciclo QA

Sono state valutate con AI esterna tre run sentinella `quality structural`
generate dopo i primi guardrail QA:

- Deep Time: score `2 -> 2`, candidati `11 -> 9`, context coverage `11/11 -> 9/9`.
- Stanford: score `3 -> 3`, candidati `40 -> 34`, reject `8 -> 5`,
  context coverage `29/40 -> 34/34`, medie Q/A/C/G migliorate.
- L25P09: score `2 -> 2`, candidati `11 -> 8`, context coverage `6/11 -> 8/8`,
  ma answer quality peggiorata.

Decisione:

- Tenere i guardrail introdotti: migliorano selettivita' e copertura del
  contesto, soprattutto su Stanford.
- Non lanciare ancora tutto il batch.
- Implementare un secondo micro-ciclo QA prima di altre valutazioni costose.

Modifiche logiche del secondo micro-ciclo:

- Scartare una QA quando tutte le risposte candidate sono esse stesse domande.
- Aumentare la penalita' answer-is-question nello scoring da `-0.16` a `-0.35`.
- Aumentare la penalita' answer-is-question nella confidence da `0.12` a `0.24`.
- Rafforzare la penalita' per answer che ecoa la domanda: `-0.28` quando la
  risposta aggiunge al massimo 1 token informativo rispetto alla domanda.
- Non attivare deferred search se esiste una risposta locale forte:
  score `>= 0.52`, gap `<= 10s`, non domanda, non distant segment e con keyword
  o numeri condivisi.
- Rendere deferred piu' severo:
  - oltre `60s`, richiedere `signal_score >= 0.28`;
  - oltre `120s`, richiedere `signal_score >= 0.34` e almeno 3 keyword forti
    oppure numeri condivisi;
  - rifiutare hand-off, filler e boilerplate tipo `I had a thought` o
    `edging towards an answer`.

Nuovo profilo:

- `quality_local`: alignment attivo, diarizzazione disattiva, QA locale
  rule-based, semantic retrieval disattivo, semantic reranker disattivo,
  debug Excel disattivo.

Motivazione:

- Capire se il costo del profilo `quality` semantico produce qualita' sufficiente
  rispetto a un profilo locale con gli stessi guardrail.
- Ridurre risposte-domanda, risposte-eco e deferred answer lontani/non
  responsive prima di una nuova valutazione esterna.

## 2026-06-23 - Esito confronto `quality` vs `quality_local`

Sono state eseguite e valutate con AI esterna quattro run:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality quality_local --segmentation-mode structural --pattern "*Stanford*"
python scripts/run_evaluation_batch.py --resume --profiles quality quality_local --segmentation-mode structural --pattern "*L25P09*"
```

Run principali:

- Stanford `quality`: `2026-06-23_110137_quality_structural`
- Stanford `quality_local`: `2026-06-23_110140_quality_local_structural`
- L25P09 `quality`: `2026-06-23_110158_quality_structural`
- L25P09 `quality_local`: `2026-06-23_110200_quality_local_structural`

Risultati Stanford:

- `quality`: score `3`, runtime value `2`, candidati `32`, keep/revise/reject
  `17/11/4`, QA time warm `52.255s`.
- `quality_local`: score `4`, runtime value `5`, candidati `60`,
  keep/revise/reject `40/13/7`, QA time `0.100s`.
- `quality_local` riduce il costo di QA di circa 500x rispetto al profilo
  semantico in questa run, con qualita' AI esterna migliore.

Risultati L25P09:

- `quality`: score `3`, runtime value `2`, candidati `7`, keep/revise/reject
  `2/4/1`, QA time warm `17.044s`.
- `quality_local`: score `3`, runtime value `4`, candidati `22`,
  keep/revise/reject `3/9/10`, QA time `0.025s`.
- `quality_local` e' molto piu' veloce, ma su L25P09 aumenta il recall e porta
  piu' rumore: domande retoriche, poll/frammenti e same-sentence echo.

Decisione:

- `quality_local` diventa il miglior candidato sperimentale per il rapporto
  qualita'/tempo.
- Il profilo semantico `quality` non giustifica il costo nelle run sentinella
  attuali: non migliora abbastanza la qualita' e in Stanford peggiora rispetto
  al locale.
- Non investire ora sul semantic reranker come leva principale.
- Prossimo micro-ciclo: migliorare precision filtering in QA locale.

Failure mode da affrontare prima di altre run ampie:

- Declarative `right?` e cue-word statements promossi a domande.
- Poll/check-in di classe: `uno/due/tre`, `1 o 3?`, `mi state vedendo`,
  `vero?`, `ci siamo?`.
- Frammenti mid-sentence senza testa interrogativa autonoma.
- Same-sentence answers che sono provenance/asides o continuazioni non
  informative.
- Deferred answer ancora troppo permissivo in pochi casi residui.
- `wtpsplit` ancora in fallback: resta un limite strutturale per sentence
  boundary e frammentazione.

## 2026-06-23 - Terzo micro-ciclo QA: precision filtering locale

Obiettivo:

- Ridurre il rumore visto in `quality_local`, soprattutto su L25P09, senza
  aumentare il costo della pipeline.
- Mantenere `quality_local` come candidato sperimentale principale per rapporto
  qualita'/tempo.

Modifiche implementate:

- Corretto il criterio generale: evitare blacklist di frasi viste nei test
  sentinella. I casi come `ci siamo?` o `mi state vedendo?` devono essere
  intercettati da segnali strutturali deboli, non da match testuale esatto.
- Il controllo check-in diretto resta limitato a micro-tag discorsivi generici
  come `ok`, `right`, `vero`.
- Aggiunto riconoscimento di poll numerici/verbali:
  - esempi: `1 o 3?`, `uno due tre?`, `one or three?`.
  - reason code: `rhetorical_poll_question`.
  - penalita' domanda: `-0.42`.
  - penalita' didactic usefulness: `-0.42`.
- Aggiunto riconoscimento di tag declarativi senza vera testa interrogativa:
  - esempio: `This construction is the natural basis, right?`.
  - reason code: `declarative_tag_question`.
  - penalita' domanda: `-0.30`.
  - penalita' didactic usefulness: `-0.30`.
- Aggiunto riconoscimento di domande-frammento senza testa interrogativa:
  - esempio: `And this one?`.
  - reason code: `fragment_question`.
  - penalita' domanda: `-0.22`.
  - penalita' didactic usefulness: `-0.18`.
  - eccezione intenzionale: follow-up anaforici come `Is that true?`,
    `Does it?`, `Why?` restano gestiti dalla logica di context expansion.
- Bloccata la deferred answer search per candidati marcati come:
  `rhetorical_poll_question`, `fragment_question`, `declarative_tag_question`.
- Aggiunti review flag esportati per:
  `rhetorical_poll_question`, `fragment_question`,
  `declarative_tag_question`, `same_sentence_echo`.

Motivazione:

- I commenti AI esterni indicavano che una parte importante dei reject non era
  dovuta alla ricerca della risposta, ma alla promozione iniziale di frasi
  poco informative a domande QA.
- Le euristiche devono restare conservative e generalizzabili: i sentinella
  servono a scoprire categorie di errore, non a costruire liste di eccezioni
  legate a un singolo audio.
- Questo micro-ciclo lavora prima della fase costosa di valutazione e non
  introduce dipendenze, modelli o token.

Test aggiunti:

- Poll numerici/italiani filtrati.
- Tag declarativo `right?` filtrato.
- Frammento `And this one?` filtrato.
- Echo same-sentence marcato con review flag.

Verifica eseguita:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_pipeline_config tests.test_main_cli
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- Set mirato: `50` test OK.
- Suite completa: `147` test OK.

Punto rimasto aperto:

- Era previsto un ulteriore irrigidimento specifico del deferred search per
  `quality_local` quando il gap e' oltre `30s`. La patch e' stata bloccata dal
  sistema di approvazione del tool durante questa sessione, quindi non e'
  stata applicata. Da riprendere solo dopo aver valutato questo micro-ciclo,
  per non mescolare troppi cambiamenti nella prossima run sentinella.

## 2026-06-23 - Correzione resume evaluation: hash del worktree

Problema osservato:

- Le run sentinella `quality_local` lanciate con `--resume` dopo il micro-ciclo
  QA sono state saltate come `skipped existing evaluation`.
- La causa era che il controllo di compatibilita' confrontava `git_commit`,
  `git_dirty` e `git_status_short`. Con modifiche non committate sugli stessi
  file, l'elenco dei file dirty resta uguale anche se il contenuto cambia.

Correzione implementata:

- Aggiunto `git_worktree_hash` allo snapshot del batch runner e del metrics
  exporter.
- L'hash include:
  - `git status --short`;
  - `git diff --binary HEAD --` per i file tracciati modificati;
  - lista e contenuto dei file non tracciati non ignorati.
- `--resume` ora richiede anche la corrispondenza di `git_worktree_hash`.

Motivazione:

- Durante fasi sperimentali con codice dirty, le valutazioni devono distinguere
  cambiamenti reali alle euristiche anche prima del commit.
- Questo evita false valutazioni basate su run vecchie considerate compatibili
  solo perche' i nomi dei file modificati non sono cambiati.

Verifica:

```bash
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*L25P09*' --output-root /private/tmp/exerplaza_batch_resume_check_l25
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*Stanford*' --output-root /private/tmp/exerplaza_batch_resume_check_stanford
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- I dry-run non saltano piu' le vecchie run `quality_local`; mostrano il comando
  reale da eseguire.
- Suite completa: `147` test OK.

## 2026-06-23 - Quarto micro-ciclo QA: question intent e precisione locale

Contesto:

- Dopo le nuove run `quality_local` valutate da AI esterna, il pattern e'
  chiaro: il profilo locale evita molte distant-answer catastrofiche con costo
  minimo, ma alza il recall di falsi positivi.
- Le run erano warm/reuse, quindi i tempi non vanno premiati come misura del
  costo cold.

Risultati osservati:

- L25P09 `2026-06-23_200833_quality_local_structural`: score `2`, candidati
  `16`, reject `8`, revise `6`, keep `2`.
- Stanford `2026-06-23_200834_quality_local_structural`: score `3`, candidati
  `37`, reject `9`, revise `14`, keep `14`.

Failure mode principali:

- Domande dichiarative o embedded, per esempio frasi con una sotto-domanda ma
  senza vera richiesta autonoma.
- Tag retorici e forme deboli.
- Same-sentence answer che non contiene un cue di risposta.
- Placeholder answer che rimanda a materiale futuro.
- Deferred answer ancora troppo permissiva per `quality_local`.
- Duplicati/near-duplicate adiacenti.

Modifiche implementate:

- Aggiunta diagnostica strutturale `question_intent`:
  - `information_seeking`;
  - `embedded_statement_question`;
  - `rhetorical_tag`;
  - `poll_or_check`;
  - `fragment`;
  - `weak_question_form`.
- `question_intent` usa segnali generali:
  - presenza di testa interrogativa autonoma;
  - posizione della prima cue interrogativa;
  - token count;
  - pattern poll numerici;
  - tag discorsivi;
  - metadata di frammentarieta'.
- Penalita' aggiuntive:
  - `poll_or_check`: `-0.18`;
  - `rhetorical_tag`: `-0.18`;
  - `fragment`: `-0.16`;
  - `embedded_statement_question`: `-0.12`;
  - `weak_question_form`: `-0.10`;
  - short question senza head: ulteriore `-0.08`.
- Le stesse categorie penalizzano anche il didactic usefulness score, per
  evitare che il canale didattico compensi troppo una forma debole.
- In `quality_local`, `embedded_statement_question` e `weak_question_form`
  bloccano la deferred answer search.
- Same-sentence answer:
  - se non contiene cue di risposta, penalita' ulteriore `-0.18`;
  - review flag `same_sentence_without_answer_cue`.
- Placeholder/future answer:
  - aggiunti pattern generali per risposte tipo answer/response in next... o
    ritorno futuro al tema;
  - trattati come boilerplate answer.
- Deferred `quality_local`:
  - penalita' di ranking `-0.10` di base;
  - `-0.16` oltre `30s` o oltre `8` unita';
  - `-0.24` oltre `60s` o oltre `12` unita';
  - gate preventivo piu' severo oltre `30s`, `75s` e oltre `10` unita'.
- Deduplica conservativa:
  - confronta solo candidati temporalmente vicini;
  - richiede alta sovrapposizione token domanda/risposta;
  - conserva il candidato con confidenza migliore e meno flag.

Motivazione:

- Spostare il lavoro dalla lista di casi sentinella alla classificazione
  strutturale dell'intento della domanda.
- Mantenere `quality_local` come profilo economico, ma spingerlo verso maggiore
  precisione e minori reject.

Test aggiunti:

- `question_intent` per embedded statement question.
- Same-sentence answer senza cue di risposta.
- Placeholder answer penalizzato.
- `quality_local` blocca deferred per embedded/weak question intent.
- Deduplica near-identical adjacent QA pairs.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_pipeline_config tests.test_main_cli tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*L25P09*' --output-root /private/tmp/exerplaza_batch_v4_l25
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*Stanford*' --output-root /private/tmp/exerplaza_batch_v4_stanford
```

Risultato:

- `40` test QA OK.
- Set mirato esteso: `58` test OK.
- Suite completa: `152` test OK.
- I dry-run sentinella non saltano run esistenti e mostrano i comandi reali da
  eseguire.

## 2026-06-23 - Micro-ciclo quality_local v5: precision gate senza semantica

Valutazione esterna ricevuta dopo la run v4:

- `L25P09` (`2026-06-23_202547_quality_local_structural`):
  - quality score `3`;
  - runtime value `3`;
  - `11` candidati valutati;
  - decisioni AI: `1 keep`, `5 revise`, `5 reject`;
  - migliora rispetto alla run `20:08` che era a quality score `2`;
  - restano falsi positivi da cue interrogative usate come subordinate o
    frammenti, poll/check-in e same-sentence answer non informative.
- `Stanford` (`2026-06-23_202548_quality_local_structural`):
  - quality score `3`;
  - runtime value `4`;
  - `28` candidati valutati;
  - decisioni AI: `9 keep`, `10 revise`, `9 reject`;
  - mantiene quality score `3`, ma con meno candidati rispetto ai `37` della
    run `20:08`;
  - resta rumore da declarative/truncated fragments, risposte metatestuali,
    same-sentence/echo answer e un deferred ancora troppo ampio.

Interpretazione:

- Il profilo `quality_local` conferma il vantaggio principale: evita molti
  errori catastrofici da risposta distante del profilo semantico.
- Il problema residuo non e' il costo QA, ma la precisione: il profilo locale
  produce ancora falsi positivi didatticamente deboli.
- I tempi delle run v4 sono warm/reuse, quindi validi per costo marginale QA e
  non per benchmark end-to-end.

Scelte implementate nel micro-ciclo v5:

- Aggiunto `question_intent_subordinate_fragment`:
  - riconosce domande implicite che sembrano subordinate/frammenti, ad esempio
    cue temporali brevi senza punto interrogativo o spiegazioni introdotte da
    `because/perche` seguite da `if/se/when/quando`;
  - la regola e' strutturale e non basata su frasi sentinella esatte.
- Aggiunto gate finale solo per `quality_local`:
  - scarta poll/check-in, tag retorici, frammenti e subordinate fragment;
  - scarta coppie con risposta debole se la confidence e' sotto `0.72`;
  - scarta embedded/weak question form quando la risposta e' anche poco
    rilevante, boilerplate o same-sentence senza cue;
  - scarta deferred `quality_local` se oltre `45s`, oltre `10` unita', con
    risposta sopra `45` token o quality gate negativo.
- Rafforzata penalita' risposte non sostanziali:
  - boilerplate/metatesto da `-0.14` a `-0.26`;
  - same-sentence senza answer cue da ulteriore `-0.18` a `-0.26`;
  - aggiunta penalita' `low_information_answer_penalty` per risposte molto
    corte senza cue e senza numeri;
  - aggiunta penalita' `deferred_answer_too_broad_penalty` per deferred locali
    troppo ampi.
- Estesi i review flag:
  - `answer_boilerplate`;
  - `low_information_answer`;
  - `deferred_answer_too_broad`;
  - `subordinate_fragment_question`.

Test aggiunti:

- `quality_local` rifiuta placeholder/metarisposte.
- `quality_local` rifiuta subordinate clause fragments.
- `quality_local` rifiuta same-sentence answer senza cue.
- `quality_local` rifiuta deferred answer troppo ampia.
- `quality_local` rifiuta embedded statement question deboli invece di
  esportarle.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_pipeline_config tests.test_main_cli tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*L25P09*' --output-root /private/tmp/exerplaza_batch_v5_l25
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*Stanford*' --output-root /private/tmp/exerplaza_batch_v5_stanford
```

Risultato:

- `44` test QA OK.
- Set mirato esteso: `62` test OK.
- Suite completa: `156` test OK.
- I dry-run sentinella non saltano run esistenti e mostrano i comandi reali da
  eseguire.

## 2026-06-23 - Valutazione v5 e correttivo v6: answer span completeness

Valutazione esterna ricevuta dopo v5:

- `L25P09` (`2026-06-23_203947_quality_local_structural`):
  - quality score `2`;
  - runtime value `4`;
  - `6` candidati valutati;
  - decisioni AI: `1 keep`, `3 revise`, `2 reject`;
  - question quality media salita a `3.17`, ma answer quality resta bassa
    (`2.33`) e context quality scende (`2.17`);
  - conclusione: v5 ha filtrato rumore ma ha tagliato troppo yield e non ha
    risolto il problema principale, cioe' risposte troncate o premise-only.
- `Stanford` (`2026-06-23_203948_quality_local_structural`):
  - quality score `3`;
  - runtime value `4`;
  - `23` candidati valutati;
  - decisioni AI: `8 keep`, `9 revise`, `6 reject`;
  - answer quality media migliora da `2.68` a `3.13`;
  - reject scendono da `9` a `6`;
  - conclusione: v5 migliora precisione su Stanford, ma resta bloccata a score
    `3` per frammentazione, metarisposte e qualche deferred discutibile.

Decisione:

- Non rafforzare ulteriormente i filtri.
- Il limite attuale non e' solo trovare meno candidati, ma scegliere una
  risposta piu' completa quando la prima frase dopo la domanda e' soltanto
  premessa/setup.

Modifiche implementate nel correttivo v6:

- Aggiunto scoring `answer_span_completeness` nel ranking rule-based.
- Se uno span multi-frase aggiunge segnale reale rispetto alla prima frase
  (`shared_keywords`, numeri o cue di risposta), riceve
  `answer_span_extension_support` con bonus `+0.12`.
- Se in `quality_local` la risposta locale breve e immediata ha pochi token e
  nessun segnale utile, riceve `premise_only_answer_penalty` con penalita'
  `-0.10`.
- Se uno span esteso supera `80` token riceve una penalita' leggera
  `answer_span_too_broad_penalty`, per evitare che il bonus favorisca blocchi
  troppo larghi.
- Debug esportato in `answer_span_completeness_debug`, cosi' le valutazioni
  future possono spiegare perche' e' stata scelta una risposta piu' lunga.

Motivazione:

- Recuperare candidati come L25P09 dove la risposta sostanziale arriva nella
  frase successiva alla premessa.
- Migliorare answer quality senza reintrodurre semantic retrieval/reranker e
  senza aumentare i costi.
- Restare su segnali generali, non su frasi specifiche dei test sentinella.

Test aggiunti:

- `test_prefers_extended_answer_when_first_sentence_is_only_setup`.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_pipeline_config tests.test_main_cli tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*L25P09*' --output-root /private/tmp/exerplaza_batch_v6_l25
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --pattern '*Stanford*' --output-root /private/tmp/exerplaza_batch_v6_stanford
```

Risultato:

- `45` test QA OK.
- Set mirato esteso: `63` test OK.
- Suite completa: `157` test OK.
- I dry-run sentinella non saltano run esistenti e mostrano i comandi reali da
  eseguire.

## 2026-06-23 - Valutazione larga quality_local: dipendenza dal formato contenuto

Run eseguite su piu' sorgenti con `quality_local` + `structural` e valutazione
AI esterna su tutto il set, in via eccezionale.

Risultati principali:

- `Stanford`:
  - quality score `4`;
  - `24` candidati;
  - decisioni: `9 keep`, `10 revise`, `5 reject`;
  - answer quality media `3.17`;
  - contenuto: seminario/dialogo accademico, molte domande autentiche o
    retoriche didattiche seguite da risposta locale utile.
- `Eugenia Cheng`:
  - quality score `4`;
  - `47` candidati;
  - decisioni: `26 keep`, `11 revise`, `10 reject`;
  - contenuto: intervista + Q&A, alternanza naturale domanda/risposta;
  - `quality_local` e' molto adatto.
- `Deep Time`:
  - quality score `2`;
  - `11` candidati;
  - decisioni: `5 keep`, `1 revise`, `5 reject`;
  - contenuto: panel/introduzione con lunghi monologhi del moderatore e
    domande retoriche;
  - assenza di diarizzazione/speaker-change rende difficile distinguere una
    risposta reale dalla continuazione dello stesso speaker.
- `L25P08`:
  - quality score `2`;
  - `6` candidati;
  - decisioni: `1 keep`, `4 revise`, `1 reject`;
  - contenuto: lezione tecnica italiana con ASR/sentence split rumorosi;
  - problemi: risposte troncate, filler/self-talk, domande vaghe.
- `L25P09`:
  - quality score `2`;
  - `6` candidati;
  - decisioni: `1 keep`, `3 revise`, `2 reject`;
  - problemi: ASR garbling, premise-only answers, poll noise,
    near-duplicate/echo.
- `SSL1P1`:
  - quality score `2`;
  - `5` candidati;
  - decisioni: `0 keep`, `2 revise`, `3 reject`;
  - contenuto: lezione introduttiva/logistica, poco adatta a Q/A didattiche;
  - il profilo dovrebbe produrre poco o segnalare bassa suitability.

Conclusione strategica:

- `quality_local` e' forte sui contenuti dialogici/intervista/seminario con
  alternanza naturale di domanda e risposta.
- `quality_local` e' debole su:
  - panel monologici con domande retoriche;
  - lezioni amministrative/logistiche;
  - lezioni tecniche con sentence splitting fallback e ASR rumoroso.
- Il prossimo miglioramento non deve essere un altro micro-tuning sui singoli
  candidati, ma una logica di routing/suitability del contenuto.

Decisione operativa:

- Introdurre una valutazione locale automatica del tipo di sorgente/run:
  - `dialogic_suitability`;
  - `monologue_risk`;
  - `same_speaker_continuation_risk`;
  - `logistics_or_admin_risk`;
  - `sentence_split_fallback_risk`;
  - `diarization_recommended`;
  - `recommended_profile`.
- Usare `quality_local` come candidato default solo quando la suitability
  dialogica e' alta.
- Per panel monologici o contenuti retorici, valutare:
  - profilo con diarizzazione/speaker-change;
  - profilo semantico `quality`;
  - oppure QA extraction piu' selettiva/skipping quando il contenuto non e'
    adatto.
- Ridurre valutazioni AI esterne:
  - run locali su tutto il set quando sono veloci;
  - valutazione AI solo su 3-4 run informative per ciclo, scelte dalla
    differenza delle metriche automatiche e dalla copertura dei formati.

## 2026-06-24 - Unified QA Quality Features v1 diagnostico

Decisione successiva alla proposta di routing:

- Non introdurre per ora rami di pipeline basati sul tipo di contenuto.
- Non stimare il numero di speaker e non usare AI/modelli aggiuntivi per
  scegliere profili.
- Mantenere una pipeline unica e pulita.
- Usare le informazioni gia' accumulate per produrre diagnostica locale del
  candidato Q/A/C, senza modificare ancora ranking o export.

Regola architetturale:

- `session.json` resta la fonte granulare:
  - ogni candidato Q/A/C contiene `metadata.quality_features`;
  - `quality_features` contiene solo punteggi e risk reason sintetici;
  - non copia `question_debug`, `answer_debug`, `context_debug` o
    `confidence_debug`.
- `metrics.json` resta aggregato:
  - contiene `qa_quality_metrics`;
  - riassume distribuzioni e conteggi;
  - non contiene lista candidati, testi Q/A, o dump dei debug.
- `review_packet.md` resta la vista leggibile per revisione esterna.

Feature candidate-level introdotte:

- `question_quality_score`;
- `answer_quality_score`;
- `context_quality_score`;
- `grounding_quality_score`;
- `risk_score`;
- `final_quality_score`;
- `quality_band`: `high`, `medium`, `low`;
- `risk_band`: `low`, `medium`, `high`;
- `risk_reasons`.

Formula v1:

- `question_quality_score` combina question score e didactic score.
- `answer_quality_score` usa lo score di ranking risposta gia' calcolato.
- `context_quality_score` deriva da presenza contesto e confidence contesto.
- `grounding_quality_score` deriva da sentence/utterance/timing/segment
  grounding.
- `risk_score` somma pesi compatti da review flag e reason code gia' esistenti.
- `final_quality_score` e' la media pesata di question/answer/context/grounding
  meno una quota del rischio.

Motivazione:

- Dare al progetto una misura locale confrontabile tra run senza richiedere AI
  esterna ogni volta.
- Preparare un futuro quality model attivo, ma solo dopo validazione larga.
- Evitare duplicazioni: dati dettagliati nel candidato, aggregati nella run.

Test aggiunti:

- Candidato Q/A/C contiene `metadata.quality_features`.
- `quality_features` non contiene copie complete dei blocchi debug.
- Un candidato buono ottiene `final_quality_score` maggiore di un candidato
  echo/boilerplate.
- `metrics.json` contiene `qa_quality_metrics` aggregato, senza lista
  candidati.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --dry-run --resume --profiles quality_local --segmentation-mode structural --limit 1 --output-root /private/tmp/exerplaza_quality_features_dryrun
```

Risultato:

- Test mirati: `49` OK.
- Suite completa: `158` OK.
- Dry-run batch OK, senza eseguire nuova pipeline reale.

## 2026-06-24 - Unified QA Quality Features v2 calibrazione diagnostica

Validazione v1 su tutto il set:

- `Stanford` e' ordinato correttamente come caso forte.
- `Deep Time` e `L25P09` risultano piu' rischiosi.
- `SSL1P1` e `L25P08` risultavano pero' troppo alti rispetto alle valutazioni
  AI esterne precedenti.

Problema identificato:

- La formula v1 premiava troppo:
  - grounding buono;
  - gap breve;
  - cue superficiali di risposta (`allora`, `quindi`, `perche`);
  - confidence media.
- Penalizzava troppo poco:
  - domande implicite senza vero punto interrogativo o head didattica;
  - risposte logistiche/filler;
  - low relevance;
  - cue di risposta presenti ma senza reale responsivita';
  - contesto assente o debole.

Modifiche v2:

- `final_quality_score` usa una penalita' rischio piu' alta:
  - da `0.30 * risk_score` a `0.45 * risk_score`.
- Ridotto leggermente il peso del grounding nella base quality.
- Aumentati i pesi rischio per:
  - `low_relevance`;
  - `weak_question_form`;
  - `embedded_statement_question`;
  - `premise_only_answer`.
- Aggiunti risk reason sintetici, sempre derivati da dati gia' presenti:
  - `implicit_question_risk`;
  - `weak_context_risk`;
  - `surface_answer_cue_risk`.

Decisione:

- La v2 resta diagnostica.
- Non cambia ranking/export.
- Non introduce nuovi modelli, routing, speaker-count o duplicazioni.

Test aggiunti:

- Caso logistico/superficiale con cue di risposta ma bassa rilevanza:
  - deve produrre `weak_context_risk`;
  - deve produrre `surface_answer_cue_risk`;
  - deve avere risk score alto e non essere `high`.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- QA extractor: `47` test OK.
- Test mirati QA + exporter: `50` OK.
- Suite completa: `159` OK.

Validazione batch v2:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Il comando ha richiesto permessi elevati per scrivere in:

- `/Users/matteopogetta/Documents/ExerPlazaSample/output`;
- `/Users/matteopogetta/Documents/ExerPlazaProject/evaluations`.

Risultati `qa_quality_metrics` sulle nuove run:

| input | AI score precedente | avg quality | median quality | avg risk | high/medium/low |
| --- | ---: | ---: | ---: | ---: | --- |
| Stanford | 4 | 0.6810 | 0.6934 | 0.1658 | 10/9/5 |
| Eugenia Cheng | 4 | 0.6160 | 0.6268 | 0.2204 | 10/28/9 |
| SSL1P1 | 2 | 0.6050 | 0.6138 | 0.2680 | 1/3/1 |
| L25P08 | 2 | 0.5994 | 0.5711 | 0.2567 | 2/2/2 |
| L25P09 | 2 | 0.5850 | 0.6385 | 0.2400 | 0/5/1 |
| Deep Time | 2 | 0.5583 | 0.6674 | 0.2745 | 1/6/4 |

Interpretazione:

- La calibrazione v2 corregge il problema piu' evidente della v1:
  - `SSL1P1` scende da circa `0.714` a `0.605`;
  - `L25P08` scende da circa `0.682` a `0.599`;
  - `Deep Time` scende da circa `0.646` a `0.558`.
- `Stanford` resta il caso migliore per score medio e numero di candidati high.
- `Eugenia Cheng` resta qualitativamente buono, ma il solo avg lo avvicina
  troppo a `SSL1P1`; la differenza si vede meglio guardando anche:
  - numero candidati (`47` vs `5`);
  - high-quality count (`10` vs `1`);
  - distribuzione risk.

Conclusione:

- `final_quality_score` candidato-level e' utile.
- Per una run intera non basta una media: serve una metrica aggregata composta,
  che consideri anche yield utile e distribuzione quality/risk.

## 2026-06-24 - Run Quality Signal diagnostico

Problema:

- Dopo la calibrazione v2, la media dei candidate score non basta per leggere
  una run.
- Esempio: una run con pochi candidati medi puo' avere media simile a una run
  con molti candidati utili.

Modifica:

- Aggiunto `qa_quality_metrics.run_quality_signal` in `metrics.json`.
- Il segnale resta diagnostico e non modifica ranking/export.
- Non duplica candidati o testi: usa solo aggregati gia' presenti in
  `qa_quality_metrics`.

Formula:

- `quality_distribution_score`: mediana di `final_quality_score`.
- `useful_yield_score`: saturazione su candidati high + quota dei medium:
  `(high + 0.4 * medium) / 10`, limitata a `1.0`.
- `risk_adjustment_score`: `1 - 0.8 * avg_risk - 0.2 * high_risk_ratio`.
- `score`: `0.45 * quality_distribution + 0.35 * useful_yield + 0.20 * risk`.
- Bande:
  - `high >= 0.72`;
  - `medium >= 0.50`;
  - altrimenti `low`.

Motivazione:

- Rendere confrontabili le run senza interpretare manualmente 5 campi.
- Premiare non solo la qualita' media dei candidati, ma anche il numero di
  candidati effettivamente utili.
- Penalizzare run con rischio elevato senza introdurre routing o filtri.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- Test mirati QA + exporter: `50` OK.
- Suite completa: `159` OK.

Validazione batch:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Il comando e' stato eseguito con permessi elevati per scrivere output ed
evaluations fuori dalle root sandbox.

Risultati `run_quality_signal`:

| input | AI score precedente | run signal | band | quality distribution | useful yield | risk adjustment |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Stanford | 4 | 0.8338 | high | 0.6934 | 1.0000 | 0.8590 |
| Eugenia Cheng | 4 | 0.7959 | high | 0.6268 | 1.0000 | 0.8194 |
| Deep Time | 2 | 0.5681 | medium | 0.6674 | 0.3400 | 0.7440 |
| L25P09 | 2 | 0.5123 | medium | 0.6385 | 0.2000 | 0.7747 |
| L25P08 | 2 | 0.5073 | medium | 0.5711 | 0.2800 | 0.7613 |
| SSL1P1 | 2 | 0.5023 | medium | 0.6138 | 0.2200 | 0.7456 |

Interpretazione:

- Il segnale aggregato separa correttamente le run con AI score `4` dalle run
  con AI score `2`.
- Le run deboli restano `medium`, non `low`, perche' contengono comunque alcuni
  candidati potenzialmente utili; questo e' appropriato finche' il segnale resta
  diagnostico e non viene usato come filtro.
- `useful_yield_score` e' il componente che distingue meglio `Eugenia` da
  `SSL1P1`, evitando che la sola media dei candidati renda simili due run molto
  diverse.

## 2026-06-24 - QA extractor guardrail cycle da segnali esistenti

Contesto:

- Dopo `quality_features` e `run_quality_signal`, l'utente ha chiesto di
  procedere al miglioramento del Q/A extractor usando prima tutte le
  informazioni gia' disponibili, senza aggiungere strumenti.
- Le valutazioni AI esterne sulle run `quality_local` indicavano failure mode
  ricorrenti:
  - statement/frammenti trattati come domande;
  - polling/backchannel di aula mescolato a domande reali;
  - risposte meta o boilerplate come `thanks/good question` al posto della
    risposta sostanziale;
  - span di risposta troncati o premise-only;
  - falsi positivi con rischio alto gia' visibile in `quality_features`.

Decisione architetturale:

- Non aggiungere nuovi modelli, nuovi profili o nuovi file.
- Usare `QAPairCandidate.metadata["quality_features"]` come sintesi compatta
  per un gate finale solo in `quality_local`.
- Lasciare invariati schema pubblico e struttura evaluation:
  - candidato = dati granulari;
  - `quality_features` = sintesi diagnostica candidata;
  - `metrics.json` = aggregati;
  - review packet = vista leggibile.

Modifiche implementate:

- `quality_local` ora rifiuta candidati con combinazioni di rischio/qualita'
  chiaramente sfavorevoli:
  - `risk_band=high` e `final_quality_score < 0.58`;
  - `quality_band=low` con rischio almeno medium;
  - `implicit_question_risk` + `surface_answer_cue_risk` sotto confidence
    `0.78`;
  - `poll_or_backchannel_noise` con rischio almeno `0.30`.
- Aggiunto rilevamento generale di rumore polling/backchannel:
  - opzioni numeriche o parole-numero ripetute;
  - check-in di aula/visibilita/ascolto;
  - penalizzazione `poll_or_backchannel_noise`.
- Aggiunto trimming di aperture meta nelle risposte quando segue subito testo
  sostanziale:
  - reason code `answer_meta_opening_trimmed`;
  - se resta solo boilerplate, continua a valere il penalty esistente.
- Aggiunto segnale di risposta incompleta:
  - `incomplete_answer_span_penalty` per span che terminano su connettivi o
    preposizioni, o iniziano come clausole aperte brevi senza chiusura;
  - `answer_span_completion_support` quando l'estensione multi-sentence chiude
    uno span inizialmente incompleto.

Motivazione:

- Migliorare precisione e leggibilita' senza overfitting sui nomi dei casi di
  test.
- Trasformare i segnali gia' raccolti in azione nel profilo sperimentale locale.
- Ridurre falsi positivi ricorrenti prima di introdurre strumenti piu' costosi
  o rami dipendenti dal contenuto.

Test aggiunti:

- trimming di meta-answer opening con contenuto sostanziale successivo;
- reject `quality_local` di polling/backchannel embedded;
- preferenza per completion di answer span incompleto.

Verifica parziale:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
```

Risultato:

- `50` test QA OK.

## 2026-06-24 - Verifica locale post guardrail Q/A extractor

Run locali eseguite dall'utente dopo il ciclo guardrail Q/A extractor:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Nuovo snapshot codice nelle run:

- `git_worktree_hash`: `5c5481b6213311d981ea6932bed238b40b5a8e3faae9db2d554e977b0e7147f3`

Confronto contro il batch precedente `2026-06-24_1345xx`:

| input | candidati prima | candidati dopo | run signal prima | run signal dopo | avg risk prima | avg risk dopo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Deep Time | 11 | 7 | 0.5681 | 0.6041 | 0.2745 | 0.1429 |
| Eugenia Cheng | 47 | 38 | 0.7959 | 0.8236 | 0.2204 | 0.1626 |
| L25P08 | 6 | 3 | 0.5073 | 0.6131 | 0.2567 | 0.0733 |
| L25P09 | 6 | 5 | 0.5123 | 0.5427 | 0.2400 | 0.1600 |
| SSL1P1 | 5 | 4 | 0.5023 | 0.5428 | 0.2680 | 0.1550 |
| Stanford | 24 | 19 | 0.8338 | 0.8698 | 0.1658 | 0.0884 |

Osservazioni:

- Il segnale aggregato migliora su tutti e 6 gli input.
- Tutti i candidati `low` sono stati rimossi nelle nuove run.
- Tutti i candidati `high risk` sono stati rimossi nelle nuove run.
- Le run gia' forti restano `high`:
  - Stanford: `0.8698`;
  - Eugenia Cheng: `0.8236`.
- Le run deboli migliorano ma restano `medium`, come atteso:
  - Deep Time: `0.6041`;
  - L25P08: `0.6131`;
  - L25P09: `0.5427`;
  - SSL1P1: `0.5428`.

Analisi dei candidati rimossi:

- La maggior parte dei candidati rimossi era coerente con i failure mode gia'
  noti: low relevance, question-as-statement, answer surface cue, weak context,
  deferred long gap, poll/backchannel noise.
- Possibile falso negativo da monitorare:
  - Eugenia Cheng `qa_0046`: domanda anaforica con contesto utile
    (`gatekeeping in mathematics`) e risposta su cultura/gatekeeping;
  - e' stata rimossa per `low_relevance` pur avendo contesto medium.

Decisione:

- Non ritoccare ancora il gate prima della prossima AI review esterna.
- Il possibile falso negativo e' utile come punto di controllo per il reviewer:
  se la AI esterna conferma perdita di recall su domande anaforiche con buon
  contesto, introdurre un rescue rule generale per Q/A/C context-grounded:
  `low_relevance` non deve bastare da solo a scartare una domanda autonoma se
  il contesto medium/high risolve l'anafora e gli altri rischi sono bassi.
- Prossimo passo consigliato: AI review mirata su poche run nuove, non su tutto
  il batch.

## 2026-06-24 - AI review esterna mirata su 4 run post guardrail

Run valutate con AI esterna:

- Stanford `2026-06-24_135643_quality_local_structural`;
- Eugenia Cheng `2026-06-24_135640_quality_local_structural`;
- L25P08 `2026-06-24_135641_quality_local_structural`;
- Deep Time `2026-06-24_135637_quality_local_structural`.

Risultati AI esterna:

| input | quality score | runtime value | sintesi |
| --- | ---: | ---: | --- |
| Stanford | 4 | 3 | profilo locale forte, molti keep reali, pochi errori residui da frammenti/declarative |
| Eugenia Cheng | 3 | 4 | buon yield ma misto; molti keep, ancora statement/fragment e answer span errati |
| L25P08 | 3 | 3 | pochi candidati, 2/3 usabili; errore principale: filler/meta-aside scelto come answer |
| Deep Time | 3 | 3 | migliorato rispetto al caso monologico; 2 keep chiari, errori da monologue continuation e span tronchi |

Conclusioni:

- Il profilo `quality_local` sta andando nella direzione corretta:
  - evita quasi del tutto errori catastrofici da deferred/distant answer;
  - e' adatto quando il costo e la manutenzione devono restare bassi;
  - il limite principale ora e' la distinzione tra risposta locale vera e
    continuazione/filler locale.
- La AI esterna conferma che i problemi residui non richiedono subito nuovi
  strumenti:
  - statement/declarative estratti come domande;
  - cue superficiali `because/so` che alzano risposte non sostanziali;
  - overlap lessicale su parole deboli (`they`, `that`, connettivi);
  - domande procedurali/meta che consumano slot QA;
  - span di risposta incompleti.

Modifiche implementate dopo la review:

- Ampliato l'elenco di stopword usate da `_content_tokens` per evitare che
  pronomi e connettivi creino falso overlap domanda/risposta.
- Aggiunto `procedural_question_request` per domande di gestione turno/Q&A,
  descritte come richieste operative di gestione del turno, non come contenuto
  didattico.
- Aggiunto `surface_answer_cue_penalty` quando una risposta ha cue come
  `because/so` ma non ha reale segnale topic condiviso.
- Rafforzato il gate `quality_local`:
  - rifiuta `surface_answer_cue_risk` con `final_quality_score < 0.62`;
  - rifiuta `incomplete_answer_span` con `final_quality_score < 0.65`;
  - rifiuta procedural/meta question requests.

Motivazione:

- Intervenire sui segnali generali gia' rilevati, non sui singoli esempi.
- Evitare di aggiungere routing, diarizzazione o modelli mentre i problemi
  residui sono ancora correggibili con segnali locali deterministici.
- Mantenere la pipeline pulita: reason code nel candidato, aggregati in metrics,
  nessun nuovo file o schema pubblico.

Test aggiunti:

- reject di procedural question request in `quality_local`;
- reject di surface answer cue senza contenuto;
- reject di clausola declarativa con solo overlap debole.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- Test QA: `53` OK.
- Suite completa: `165` OK.

Prossima verifica consigliata:

- Run locali `quality_local` su tutti gli input, senza AI esterna iniziale.
- Controllare se:
  - Stanford resta score locale alto e non perde troppi keep;
  - Eugenia riduce i reject chiari senza crollare di yield;
  - Deep Time scarta qa procedurali/frammenti e resta almeno 2 keep chiari;
  - L25P08 non mantiene il filler/meta-aside come answer principale.

## 2026-06-24 - Verifica locale post AI-review micro-fix

Run locali eseguite dall'utente dopo il micro-fix su overlap debole,
surface answer cue e procedural question request:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Nuovo snapshot codice nelle run:

- `git_worktree_hash`: `c8ab0d7971f3...`

Confronto contro le run `2026-06-24_1356xx` valutate dalla AI esterna:

| input | candidati prima | candidati dopo | run signal prima | run signal dopo | nota |
| --- | ---: | ---: | ---: | ---: | --- |
| Stanford | 19 | 18 | 0.8698 | 0.8677 | stabile high; rimosso un reject AI |
| Eugenia Cheng | 38 | 31 | 0.8236 | 0.8209 | stabile high; rimosso rumore ma anche alcuni revise |
| L25P08 | 3 | 3 | 0.6131 | 0.6131 | invariato; resta il problema meta-aside answer |
| Deep Time | 7 | 4 | 0.6041 | 0.5433 | pulisce reject/revise ma cala yield |
| SSL1P1 | 4 | 3 | 0.5428 | 0.5540 | migliora leggermente; rimosso answer cue superficiale |
| L25P09 | 5 | 5 | 0.5427 | 0.5427 | invariato |

Interpretazione:

- Il micro-fix ha funzionato sui casi esplicitamente segnalati dalla AI:
  - Stanford: rimosso `qa_0005`, reject AI per declarative statement + answer
    troncata.
  - Deep Time: rimossi `qa_0009` reject, `qa_0015` revise per surface cue
    senza risposta reale, `qa_0016` reject procedurale/meta.
  - SSL1P1: rimosso candidato con `surface_answer_cue_risk` e contesto debole.
  - Eugenia: rimossi reject chiari (`qa_0012`, `qa_0039`, `qa_0063`) ma anche
    alcuni candidati AI `revise`, incluso il caso embedded `math with no
    numbers` con risposta forte.
- Non e' emerso un crollo su Stanford/Eugenia: entrambe restano `high` nel
  segnale locale.
- L25P08 e' rimasto invariato: il failure mode specifico e' una risposta
  meta/logistica del docente scelta come answer per una domanda concettuale.

Decisione successiva:

- Il possibile micro-fix sulle risposte di supporto/logistica docente e' stato
  scartato e rimosso perche' troppo dipendente da formulazioni linguistiche
  specifiche.
- Questo failure mode resta annotato come problema aperto da affrontare con un
  segnale piu' strutturale, ad esempio answer-responsiveness, completamento
  dello span o turn-boundary, non con frasi campione.
- Rimosso anche il test dedicato, per evitare che la suite codifichi esempi
  narrativi invece di invarianti generali.

Verifica:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato dopo rimozione del micro-fix specifico:

- Test QA: `54` OK.
- Suite completa: `166` OK.

Prossima verifica:

- Rilanciare run locali `quality_local` per controllare se L25P08 perde il
  candidato meta-aside senza penalizzare Stanford/Eugenia.

## 2026-06-25 - Pulizia anti-overfitting testuale

Problema segnalato dall'utente:

- Alcuni test e annotazioni erano troppo vicini a frasi viste nelle run di
  valutazione.
- Questo viola la regola di progetto: il codice e i test non devono codificare
  esempi reali o formulazioni specifiche, altrimenti il sistema migliora sui
  casi osservati e perde generalita'.

Modifiche:

- Rimosso il micro-fix sulle risposte di supporto/logistica docente perche'
  troppo linguistico e non abbastanza strutturale.
- Rimossi dai test contenuti riconoscibili delle run valutate.
- Sostituiti i casi necessari con placeholder astratti o rimossi quando il test
  avrebbe misurato una frase invece di una proprieta'.
- Ridotto il pattern `procedural_question_request` alla categoria generale di
  richiesta operativa di porre/gestire una domanda, togliendo trigger lessicali
  troppo vicini alle review.
- Aggiornato il diario per non conservare esempi letterali.

Verifica anti-overfitting:

```bash
rg -n "watermarks|Watermarks|piggyback|Holly|pendolo|Furiere|protractor|trigonometry|patient have|doctors understand|scroll one more|source identity|image provenance|Come siamo messi|Ragazzi|I need to understand|where students|support the class|io sono qua|dove siete|aiutarvi" tests src docs PROJECT_DIARY.md README.md README.it.md scripts
```

Risultato:

- Nessun match su esempi/casi reali.
- Restano solo occorrenze generiche di `high risk` come nome di metrica/banda.

Test:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
```

Risultato:

- Test QA: `53` OK.
- Suite completa: `165` OK.

Run locale eseguita da Codex dopo la pulizia:

```bash
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Output batch:

- `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_2026-06-25_142844`

Nuovo snapshot codice:

- `git_worktree_hash`: `d79833fb81bc...`

Confronto `2026-06-24_1411xx` vs `2026-06-25_1428xx`:

- Le metriche sono identiche su tutti e 6 gli input.
- Cambia solo l'hash del codice.
- Questo conferma che la rimozione dei pattern/test troppo specifici non ha
  cambiato il comportamento delle run locali, ma ha ripulito la base di test e
  le regole da overfitting testuale.

Decisione:

- Non fare altra AI review esterna per questa pulizia: non cambia output.
- Chiudere il micro-ciclo QA extractor e procedere a commit, oppure passare al
  prossimo miglioramento strutturale: answer-responsiveness indipendente da
  frasi specifiche.

## 2026-06-25 - Answer responsiveness diagnostico v1

Obiettivo:

- Aggiungere un segnale locale e deterministico che misuri se la risposta
  selezionata risponde davvero alla domanda, senza introdurre regole su frasi o
  casi specifici osservati nelle run.
- Rendere il segnale utile per ranking, risk e confronto run, ma non usarlo
  subito come filtro rigido finche' non e' tarato su tutto il set.

Implementazione:

- Aggiunto helper interno `_score_answer_responsiveness` nel QA extractor.
- Input usati: domanda, risposta, allineamento lessicale/numerico gia'
  calcolato, cue risposta gia' raccolti.
- Output: `answer_responsiveness` come partial score, debug compatto in
  `answer_responsiveness_debug`, reason code sintetici.
- Aggiunto `answer_responsiveness_score` dentro
  `QAPairCandidate.metadata["quality_features"]`.
- Aggiunta distribuzione aggregata `answer_responsiveness_score` in
  `metrics.json` sotto `qa_quality_metrics`.

Regole e parametri iniziali:

- Supporto topicale o numerico: piccolo bonus (`+0.07` / `+0.08`).
- Sostanza aggiunta rispetto alla domanda: piccolo bonus (`+0.04`) se la
  risposta aggiunge almeno tre token informativi.
- Mancanza di aggancio domanda-risposta: penalita' moderata (`-0.14`) se non ci
  sono overlap/cifre/cue utili.
- Domande contestuali o espanse da contesto: penalita' piu' leggera (`-0.04`),
  per non eliminare risposte naturali tipo conferma/smentita con precisazione.
- Domande quantitative senza numero in risposta: penalita' (`-0.12`).
- Risposte che sono ancora domande: penalita' (`-0.12`).
- Il delta e' limitato a `[-0.24, +0.06]`: dopo una prima run locale, il bonus
  positivo `+0.16` risultava troppo generoso e faceva emergere un falso
  positivo su una domanda frammentaria. La versione finale del micro-ciclo
  mantiene quindi la penalita' informativa ma rende il bonus solo conservativo.

Decisione architetturale:

- `answer_responsiveness` non e' un nuovo ramo della pipeline.
- Non aggiunge file nuovi.
- Non copia debug estesi in `metrics.json`.
- Non diventa per ora un gate duro in `quality_local`, perche' una bassa
  sovrapposizione lessicale puo' essere corretta in follow-up contestuali e in
  risposte esplicative naturali.

Test aggiunti:

- Verifica che `quality_features` contenga il nuovo score compatto.
- Verifica che una risposta astratta con aggancio topicale e sostanza abbia
  responsiveness maggiore di una risposta astratta non ancorata.
- Verifica aggregazione dello score in `metrics.json`.

Verifiche locali:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor tests.test_evaluation_run_exporter
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Risultato test:

- Test mirati QA/export: `57` OK.
- Suite completa: `166` OK.

Run locale finale:

- Output batch:
  `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_2026-06-25_202344`

Confronto interno contro baseline pulita `2026-06-25_1428xx`:

- Deep Time: candidati `4 -> 4`, run quality signal `0.5433 -> 0.5762`.
- Eugenia: candidati `31 -> 30`, run quality signal `0.8209 -> 0.8359`.
- L25P08: candidati `3 -> 3`, invariato.
- L25P09: candidati `5 -> 5`, invariato.
- SSL1P1: candidati `3 -> 3`, run quality signal `0.5540 -> 0.5643`.
- Stanford: candidati `18 -> 18`, run quality signal `0.8677 -> 0.8710`.

Nota sulla run intermedia:

- Una versione con bonus massimo `+0.16` aveva prodotto un candidato aggiuntivo
  su SSL1P1; e' stata scartata perche' aumentava il recall di falsi positivi.

Decisione:

- Il micro-ciclo e' localmente stabile.
- Serve valutazione esterna AI mirata sulla run finale per confermare che la
  rimozione di un candidato su Eugenia sia positiva e che i miglioramenti
  interni non nascondano regressioni qualitative.

## 2026-06-25 - Valutazione esterna AI su answer responsiveness v1

Input valutato:

- Run finale `quality_local structural` generata da Codex:
  `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_2026-06-25_202344`
- Cartelle evaluation finali `2026-06-25_2023xx_quality_local_structural`.

Risultati esterni sintetici:

- Deep Time: quality score `2`, runtime value `2`. Il profilo senza
  diarizzazione continua a fallire sui panel/monologhi: le Q/A sono spesso
  auto-continuazioni dello stesso speaker, non risposte cross-speaker.
- Eugenia: quality score `3`, runtime value `3`. Buono su contenuto dialogico,
  ma restano continuation e same-speaker/self-continuation quando manca una
  separazione affidabile dei turni.
- L25P08: quality score `3`, runtime value `4`. Buona velocita' e almeno una
  Q/A forte; failure mode principale: risposta meta/classroom-management o
  non sostanziale selezionata per una domanda valida.
- L25P09: quality score `2`, runtime value `3`. Failure mode dominante:
  risposte circolari/echo e candidati sovrapposti che dovrebbero consolidarsi.
- SSL1P1: quality score `2`, runtime value `3`. Failure mode dominante:
  falsi positivi di question intent su dichiarative o subordinate causali, con
  risposte che sono continuazioni sintattiche.
- Stanford: quality score `4`, runtime value `4`. Il profilo locale e'
  competitivo su seminari dialogici ben strutturati; restano embedded-statement,
  declarative cue e run-on/echo.

Conclusione:

- `answer_responsiveness_score` e' utile come diagnostica/aggregato, ma non
  basta come hard gate.
- La prossima modifica deve agire prima o in parallelo al ranking risposta:
  precisione della domanda candidata e rilevamento di continuazioni sintattiche
  domanda-risposta.
- Non implementare regole su frasi viste nelle review. I guardrail devono usare
  proprieta' strutturali: autonomia della domanda, punteggi intent esistenti,
  fragment/subordinate status, overlap/added information, stesso sentence/run,
  contesto debole.

Prossimo micro-ciclo scelto:

- `question_autonomy_and_continuation_v1`.
- Obiettivo: ridurre falsi positivi da declarative/subordinate/embedded
  question e risposte che sono solo continuazioni/echo, senza abbassare il
  buon comportamento su Stanford/Eugenia.

## 2026-06-25 - Question autonomy and circular echo v1

Obiettivo:

- Agire sui failure mode emersi dalla review esterna senza usare frasi o casi
  specifici: falsi positivi da cue impliciti dentro frasi deboli e risposte
  circolari che riciclano quasi tutta la domanda.

Implementazione:

- Aggiunto reason code `low_autonomy_implicit_question` quando una domanda:
  - non ha `?`;
  - nasce da `implicit_question_cue` e `cue_sentence_extracted`;
  - ha segnali strutturali deboli come sentence quality borderline/penalty,
    merge safety penalty o `intra_sentence_qa`.
- In `quality_local`, `low_autonomy_implicit_question` viene scartato come
  weak question reason.
- Aggiunta penalita' `answer_circular_echo_penalty` quando la risposta copre
  quasi tutta la domanda ma aggiunge pochissimi token informativi. Questo copre
  echo/circularita' anche tra frasi adiacenti, non solo same-sentence echo.
- Aggiunti review flag/risk reason sintetici:
  `low_autonomy_implicit_question` e `circular_answer_echo`.

Parametri iniziali:

- Circular echo: `question_coverage_ratio >= 0.68` e
  `added_answer_token_count <= 2`, penalita' `-0.22`.
- Il gate low-autonomy e' limitato a domande implicite senza `?`; le domande
  esplicite con punto interrogativo restano valutate dai segnali esistenti.

Test aggiunti:

- Un caso astratto di implicit cue senza autonomia viene marcato e rifiutato da
  `quality_local`.
- Un caso astratto di risposta circolare viene penalizzato e marcato come
  `circular_answer_echo`.

Verifiche locali:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_qa_extractor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests
python scripts/run_evaluation_batch.py --resume --profiles quality_local --segmentation-mode structural
```

Risultato test:

- Test QA: `56` OK.
- Suite completa: `168` OK.

Run locale:

- Output batch:
  `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_2026-06-25_204454`

Confronto contro `answer_responsiveness_v1` (`2026-06-25_2023xx`):

- Deep Time: `4 -> 4`, invariato.
- Eugenia: `30 -> 26`; rimossi quattro candidati impliciti/frammentari o
  narrativi. Signal interno `0.8359 -> 0.8069`, ancora `high`.
- L25P08: `3 -> 3`, invariato.
- L25P09: `5 -> 4`; rimosso il candidato circolare/echo su stabilita'. Signal
  interno `0.5427 -> 0.5213`, ancora `medium`.
- SSL1P1: `3 -> 0`; tutti i candidati rimossi erano implicit cue senza `?` con
  sentence/merge debole, gia' valutati dalla review come falsi positivi o solo
  parziali. Questo e' un tradeoff esplicito precisione > yield.
- Stanford: `18 -> 15`; rimossi tre candidati implicit/declarative cue indicati
  dalla review come deboli. Signal interno `0.8710 -> 0.8540`, ancora `high`.

Decisione:

- Localmente il micro-ciclo sembra coerente con la review esterna: sacrifica
  yield dove la qualita' era bassa o ambigua e conserva i casi forti.
- Serve nuova AI review esterna mirata per confermare che il taglio di SSL1P1 a
  zero candidati sia accettabile e che Stanford/Eugenia non perdano valore
  didattico significativo.

## 2026-06-25 - Valutazione esterna AI su question autonomy v1

Input valutato:

- Run `quality_local structural`:
  `/Users/matteopogetta/Documents/ExerPlazaSample/output/evaluation_batch_2026-06-25_204454`
- Cartelle evaluation `2026-06-25_2044xx_quality_local_structural`.

Risultati esterni:

- Deep Time: quality score `2`, runtime value `2`. Nessun miglioramento
  sostanziale sul caso panel/monologo: senza diarizzazione o speaker-turn il
  profilo locale continua a prendere auto-continuazioni dello stesso speaker.
- Eugenia: quality score `3`, runtime value `3`. Il profilo resta utile su
  contenuto dialogico, ma alcuni errori residui dipendono ancora da assenza di
  speaker-turn e da risposte poco responsive.
- L25P08: quality score `3`, runtime value `4`. Restano falsi positivi da
  frammenti dichiarativi e risposte meta/eco; buona velocita' warm, ma da non
  conteggiare come cold runtime.
- L25P09: quality score `3`, runtime value `4`. Il taglio dell'echo circolare e
  dei deferred/distant e' stato valutato positivamente; il matching locale e'
  utile per lezioni single-speaker con frasi abbastanza pulite. Problemi residui
  vengono da ASR, sentence split e risposte troncate.
- SSL1P1: quality score `1`, runtime value `2`. Il taglio a zero candidati e'
  una regressione di recall: il transcript contiene materiale didattico utile,
  ma la nuova guardia e/o il matcher locale non riesce a estrarlo.
- Stanford: quality score `3`, runtime value `4`. Il profilo resta solido per
  no-model/local matching, ma ha perso un po' di valore rispetto alla run
  precedente; restano errori su domande retoriche/embedded, risposte off-target
  e context debole.

Decisione:

- `question_autonomy_and_continuation_v1` non e' da irrigidire ulteriormente.
- Il guardrail e' positivo su L25P09 e su alcuni falsi positivi Stanford, ma
  troppo aggressivo per SSL1P1.
- Non aggiungere altre regole nel QA extractor prima di migliorare l'input
  fraseologico.
- Prossimo ciclo consigliato: modulo conservativo di sentence/semantic cleanup
  prima di segmentation e QA extraction, con output tracciabile e metadata come
  autonomia frase, run-on/fragment, boundary confidence e question likelihood.
