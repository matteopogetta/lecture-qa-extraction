# Decision Plan - 2026-07-03

Ruolo: Claude assume la parte decisionale (priorità, criteri di accettazione, review esterna).
Codex esegue solo implementazione a scope chiuso. Obiettivo: massimo avanzamento con minimo consumo crediti Codex.

## Soglie operative adottate

- `keep_ratio >= 0.70` sui candidati emessi (precisione).
- `quality_score >= 4` dove il contenuto lo consente (un monologo può legittimamente produrre 0 candidati: 0 candidati corretti > N candidati falsi).
- Nuovo vincolo: il recall va misurato, non solo la precisione. Una run con 2 candidati perfetti su un'intervista densa NON supera la soglia.
- Runtime: nessuna nuova feature può aumentare il tempo QA-stage oltre +15% cold; trascrizione/alignment restano il costo dominante e cacheable.

## Stato sintetico (da evaluations/ + diary)

- `quality_local_structural` è il profilo di riferimento: avgQ 2.94, avgRV 3.26, keep 43% (65 run valutate). Full non giustifica 3.3-5.6x il tempo.
- Precisione: in netto miglioramento dopo i micro-cicli v1-v6 + guardrails follow-up/check-in/echo.
- Recall: problema dominante. Dialoghi (intervista): 2 candidati su decine di scambi reali. L25P08 (socratico): risolto (1 -> 7).
- Pattern mancanti già identificati (diary 2026-07-02): domanda trascritta senza `?`, domanda spezzata su più frasi, domanda run-on con risposta nella frase successiva.

## Decisioni

1. NO diarizzazione ora: costo runtime alto e non misurato, contro il criterio tempo. Riconsiderare solo se R1+R3 falliscono sul cluster intervista.
2. NO semantic reranker ora: il margine rule-based non è esaurito. Si apre solo con trigger esplicito (vedi stop-condition).
3. La prossima capacità da costruire è la MISURA del recall (R2): senza proxy di recall ogni review esterna giudica solo la precisione e il progetto ottimizza la metrica sbagliata.
4. Congelare il benchmark: 6 input (deep_time, eugenia_cheng, l25p08, l25p09, ssl1p1, stanford) + dialoghi come 7° input interview-heavy. Una run cold per ciclo, non ripetizioni.
5. Igiene: unificare le cartelle input duplicate (stanford x2, dialoghi x2) prima del prossimo batch.

## Sequenza cicli

- R1: boundary/focus recovery (recall domande) — prompt 1.
- R2: recall proxy nel packet/metrics — prompt 2 (può andare in parallelo a R1, file diversi).
- R3: batch di generalizzazione sul benchmark + review esterna Claude — prompt 3, solo dopo merge R1+R2.
- Stop-condition per la linea semantica locale: se dopo R1+R3 il recall proxy su dialoghi resta < 0.5 o keep < 60%, aprire micro-ciclo "local semantic responsiveness check" (modello locale piccolo, solo sui candidati, budget tempo misurato prima dell'adozione).

## Regole per i prompt Codex (risparmio crediti)

1. Un prompt = un micro-ciclo chiuso. Niente esplorazione: file da toccare elencati, comportamento atteso specificato, criteri di accettazione binari.
2. Codex non deve rileggere il diary né le evaluations: il contesto necessario è nel prompt.
3. Vietato: refactoring opportunistico, nuove dipendenze, modifiche fuori dai file elencati, frasi reali nei test (solo fixture `marker alpha`-style).
4. Output richiesto a Codex: diff + esito test + entry PROJECT_DIARY scritta. Le review esterne le fa Claude (costo zero crediti).
5. Run lunghe (trascrizione cold) solo quando servono al criterio di accettazione; preferire run warm su cache esistente quando lo stadio modificato è solo QA.
6. Ogni prompt inizia con: "Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare file fuori da questa cartella."
7. Responsabilità diary: Codex appende la propria entry di implementazione a PROJECT_DIARY.md alla fine di ogni ciclo (fa parte dei criteri di accettazione); Claude appende le entry di review esterna e decisione dopo ogni valutazione.

---

## PROMPT 1 - R1 Boundary/Focus Recovery (recall domande)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella.

Contesto: pipeline lecture QA, profilo quality_local, estrazione rule-based in
src/lecture_analyzer/analysis/_qa_extractor_impl.py e _qa_rules_impl.py.
La precisione è buona; il recall su contenuti intervista è basso. Tre pattern di
domanda reale oggi NON vengono riconosciuti:

A) Domanda trascritta come dichiarativa senza '?': frase con struttura
   interrogativa chiara (inversione, wh-cue iniziale, richiesta diretta di
   definizione/spiegazione) ma senza punto interrogativo per errore di
   trascrizione/punteggiatura.
B) Domanda spezzata su più frasi consecutive dello stesso speaker-turn logico:
   il focus interrogativo sta nella prima o ultima frase, le altre sono setup.
   Va ricomposto un question span unico con focus breve.
C) Domanda run-on lunga: frase interrogativa lunga/divagante la cui risposta
   sostanziale sta nella frase immediatamente successiva. Oggi viene persa o
   il focus resta sepolto.

Requisiti:
1. Implementare il riconoscimento dei tre pattern SOLO tramite struttura
   (cue interrogativi, posizione, punteggiatura, lunghezza, adiacenza),
   mai tramite parole-contenuto specifiche. Bilingue it/en come i cue esistenti.
2. Pattern A: emettere solo con confidenza alta (almeno due segnali strutturali
   indipendenti) e risposta locale (stessa frase o adiacente). Reason code
   nuovo: question_without_terminal_mark_recovered.
3. Pattern B: ricomposizione con limite massimo 3 frasi, salvare il focus in
   question.metadata["normalized_question_text"] come già avviene per le
   domande espanse. Reason code: split_question_recomposed.
4. Pattern C: consentire il candidato solo se la risposta successiva supera i
   gate di responsiveness esistenti. Reason code: runon_question_local_answer.
5. Tutti e tre passano dai gate quality_local esistenti (answer-question,
   echo, poll/backchannel, followup prompt): NON creare bypass nuovi.
6. Vietato modificare: sentence_reconstruction, segmenter, exporter, profili
   diversi da quality_local. Vietate nuove dipendenze. Nessun refactoring.
7. Test: aggiungere unit test per A, B, C in tests/test_qa_extractor.py con
   fixture astratte (marker alpha / response beta), inclusi 3 test negativi
   (dichiarativa vera non promossa; setup senza focus non ricomposto; run-on
   con risposta non-responsive rifiutato).

Accettazione (binaria):
- PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest
  tests.test_qa_extractor tests.test_evaluation_run_exporter
  tests.test_ai_review_packet_exporter -> tutti OK (>=98 preesistenti + nuovi).
- Run warm su dialoghi_di_scienza_ep2 (riuso cache trascrizione):
  candidati >= 4 (era 2 in 2026-07-02_164424), senza reintrodurre i reject
  noti (misattribuzione continuazione-domanda, frammento retorico embedded).
- Run warm di regressione su l25p08: candidati restano 7, recuperi socratici
  presenti.
Consegna: diff, esito test, conteggio candidati delle due run, e appendi tu
l'entry di questo ciclo a PROJECT_DIARY.md (stesso formato delle entry
esistenti). Non eseguire altre run. Non valutare tu la qualità semantica:
la review esterna è a carico di Claude.
```

## PROMPT 2 - R2 Recall Proxy (misura, zero costo runtime)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella.

Contesto: le review esterne del progetto oggi misurano solo la precisione dei
candidati emessi. Serve un proxy di recall a costo runtime ~zero, calcolato dai
dati già in memoria a fine QA-stage.

Requisiti:
1. Aggiungere in metrics.json una sezione "qa_coverage" con soli aggregati:
   - interrogative_sentence_count: frasi con terminale '?' o cue interrogativo
     forte (riusare i rilevatori esistenti in _qa_rules_impl, non duplicarli);
   - emitted_candidate_count;
   - coverage_ratio = emitted/interrogative (0 se denominatore 0);
   - suppressed_by_gate_count: candidati formati ma scartati dai gate, con
     breakdown per reason code (dizionario reason -> count).
2. Aggiungere la stessa sezione in forma leggibile nel review_packet.md
   (sezione "Coverage Summary" dopo Timing Summary), così la review esterna
   può giudicare il recall senza leggere tutto il transcript.
3. Vincoli: nessun dump candidato-per-candidato in metrics.json (solo
   aggregati); nessun nuovo stadio pipeline; costo O(n) sulle frasi già
   disponibili; nessuna modifica all'estrazione stessa.
4. File attesi: exporter del review packet, writer di metrics, eventuale
   raccolta contatori nel qa_extractor. Nessun altro file.
5. Test: unit test su conteggi e breakdown con fixture astratte; un test che
   verifica coverage_ratio=0 senza divisione per zero.

Accettazione: suite unittest completa OK; una run warm qualsiasi mostra la
sezione Coverage Summary nel packet e qa_coverage in metrics.json.
Consegna: diff, esito test, esempio della sezione generata, e appendi tu
l'entry di questo ciclo a PROJECT_DIARY.md (stesso formato delle entry esistenti).
```

## PROMPT R1.1 - Tightening recovery patterns (OBBLIGATORIO prima di R3)

Esito review esterna R1 (2026-07-03): L25P08 ok (keep 6/7), Dialoghi regredito in
precisione (keep 2/5); i 3 reject vengono tutti dai nuovi pattern A/B.

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella.

Contesto: il ciclo R1 (recupero domande senza '?', split, run-on) ha aumentato
il recall ma introdotto 3 falsi positivi su 5 candidati nella run Dialoghi
2026-07-03_102855. Cause osservate:
- il recupero missing-terminal promuove dichiarative/retoriche di monologo il
  cui cue interrogativo NON e' in posizione head del focus (elenchi tipo
  "impari cos'e' X, come funziona Y" e periodi retorici "sarebbe bellissimo
  se...");
- la risposta selezionata puo' essere la continuazione interrogativa della
  domanda stessa: frase senza '?' che contiene cue interrogativo ed eco del
  focus della domanda;
- risposte same-speaker che iniziano con marker additivi di continuazione
  ("anche", "mettendoci", gerundi additivi) vengono accettate come risposte.

Requisiti (solo tightening, nessuna nuova feature):
1. Pattern missing-terminal: emettere solo se il cue interrogativo e' in
   posizione head della frase focus E c'e' almeno un segnale di
   non-continuazione (cambio strutturale, pausa temporale, o risposta che non
   prosegue lo stesso periodo sintattico). Le dichiarative con cue in mezzo
   alla frase non diventano domande.
2. Answer gate: una candidata risposta che contiene cue interrogativo E
   overlap/eco col focus della domanda va rifiutata come risposta anche senza
   '?', con reason code answer_question_continuation_rejected, e la ricerca
   passa alla frase successiva (entro i limiti locali esistenti).
3. Penalita' nuova per risposte same-speaker adiacenti che iniziano con marker
   additivo di continuazione: reason code additive_continuation_answer_penalty.
   Penalita', non reject secco.
4. Implementazione solo strutturale (posizione cue, punteggiatura, adiacenza,
   overlap), niente parole-contenuto specifiche, bilingue it/en.
5. File: src/lecture_analyzer/analysis/_qa_extractor_impl.py e
   _qa_rules_impl.py se serve. Niente altro. Nessuna nuova dipendenza.
6. Test con fixture astratte: 3 positivi (dichiarativa con cue non-head NON
   promossa; risposta-continuazione-interrogativa rifiutata con passaggio alla
   frase dopo; penalita' additiva applicata) + 2 di non-regressione (pattern A
   legittimo con cue head resta emesso; split question legittima resta emessa).

Accettazione (binaria):
- suite unittest completa OK (>=106 preesistenti + nuovi);
- run warm Dialoghi: keep attesi preservati (apertura intervista e cosmologia
  numerica presenti), le 2 dichiarative retoriche note non piu' emesse, e se
  la domanda 'quali lavori' viene emessa deve avere una risposta che non e'
  continuazione interrogativa; candidati attesi 3-4;
- run warm L25P08: restano 7 candidati.
Consegna: diff, esito test, conteggio candidati delle due run, e appendi tu
l'entry di questo ciclo a PROJECT_DIARY.md (stesso formato delle entry
esistenti). Non valutare tu la qualita' semantica: review esterna a carico di
Claude.
```

## PROMPT 3 - R3 Batch di generalizzazione + igiene label (dopo merge R1.1, previa review Claude)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella.

Contesto: R1 (recall recovery), R1.1 (tightening precisione) e R2 (coverage)
sono in main e validati da review esterna. Serve il batch di verifica sul
benchmark congelato prima di decidere il prossimo ciclo.

Requisiti:
1. Igiene input: unificare le cartelle evaluation duplicate
   (stanford_seminar_* x2, dialoghi_di_scienza_* x2): tenere il label canonico,
   spostare le run esistenti sotto di esso, nessuna run cancellata.
   La normalizzazione del label va corretta alla fonte nello script/exporter
   così i duplicati non si riformano.
2. Lanciare con scripts/run_evaluation_batch.py una run
   quality_local + structural per ciascuno dei 7 input benchmark:
   deep_time, eugenia_cheng, l25p08, l25p09, ssl1p1, stanford, dialoghi.
   Riuso cache consentito per gli stadi NON modificati (trascrizione,
   alignment); lo stadio QA deve ricomputare (invalidare/forzare solo quello).
3. Ogni run deve produrre session.json, review_packet.md (con Coverage
   Summary), metrics.json, ai_review.json placeholder pending_manual_review.
4. Nessuna modifica a logica di estrazione in questo ciclo.

Accettazione: 7 run nuove complete dei 4 file, coverage presente, nessun
duplicato di label residuo in evaluations/.
Consegna: lista path delle 7 run + tempi per stadio (osservato vs cold-equivalent
vs warm), e appendi tu l'entry di questo ciclo a PROJECT_DIARY.md (stesso
formato delle entry esistenti). La valutazione delle run la farà Claude
("Valuta"), non farla tu.
```

## Esito R3 (2026-07-03, review Claude)

Benchmark completo valutato: keep 53%, avgQ 2.86 -> soglie non raggiunte, stop-condition scattata.
Dettaglio in evaluations/benchmark_overview_2026-07-03.md. Failure dominante: risposta =
continuazione same-speaker. Secondari: span troncati, risposte non responsive, rumore poll/tag.

AGGIORNAMENTO post ricompute wtpsplit (R3.1): baseline = run 2026-07-03_1413xx-1415xx,
keep 52%, avgQ 2.57. Frasi ora integre; i difetti R4 persistono (l25p09), il failure
same-speaker si e' rafforzato (deep_time 0 keep, ssl1p1 0 keep). I criteri di accettazione
di R4 vanno verificati contro le run 1413xx-1415xx, non 1214xx.

## PROMPT 4 - R4 Span completeness + noise (rule-based, costo ~0)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella.

Contesto: review esterna del benchmark 2026-07-03 (quality_local structural).
Tre difetti rule-based ricorrenti nella classe revise/reject:
A) Domande troncate a meta' frase: lo span domanda inizia o finisce dentro un
   periodo sintattico (3 casi su eugenia_cheng), pur con risposta valida.
B) Risposte tagliate a fine finestra/segmento: lo span risposta termina a
   meta' periodo o sull'ultima parola della finestra (2 casi su l25p09).
C) Rumore d'aula: tag-question isolate senza premessa nel medesimo span
   ('vero?', 'right?' come domanda intera) e risposte fatte di poll/backchannel
   multi-parlante (voti, monosillabi ripetuti) - 2 casi su l25p09.

Requisiti (solo strutturale, niente parole-contenuto, bilingue it/en):
1. Question span integrity: se lo span domanda non inizia a un confine di
   frase integro, estendere/retrarre al confine piu' vicino entro 1 frase;
   se impossibile, penalita' question_span_integrity_penalty.
2. Answer span completion: se lo span risposta termina senza punteggiatura
   terminale e la frase successiva e' continuazione dello stesso periodo,
   completare lo span (max +1 frase, riusare answer_span_completion_support);
   se il taglio e' a fine segmento senza continuazione disponibile, penalita'
   answer_truncated_at_boundary_penalty.
3. Tag-question isolata: una frase composta solo da tag di conferma non puo'
   essere domanda candidata autonoma (estendere il reject poll/backchannel
   esistente).
4. Poll/backchannel come risposta: sequenze di unita' brevissime ripetute o
   non lessicali non possono essere span risposta.
5. File: src/lecture_analyzer/analysis/_qa_extractor_impl.py (+ _qa_rules_impl
   se serve). Nessuna nuova dipendenza, nessun refactoring.
6. Test con fixture astratte: 4 positivi (uno per requisito) + 2 non-regressione
   (socratico breve valido resta emesso; risposta breve legittima tipo
   completamento non viene scartata come backchannel).

Accettazione (binaria):
- suite unittest mirata OK (>=109 preesistenti + nuovi);
- run warm l25p09: le 2 risposte troncate note risultano completate o
  penalizzate, il candidato poll/voti non emesso, la tag-question isolata non
  emessa; i 2 keep noti restano;
- run warm eugenia_cheng: le 3 domande monche note risultano integre o
  penalizzate; i 9 keep noti restano;
- run warm l25p08: restano 7 candidati.
Consegna: diff, esito test, conteggi candidati delle tre run, e appendi tu
l'entry a PROJECT_DIARY.md. Review esterna a carico di Claude.
```

## PROMPT 5a - R5a Esperimento misurato: speaker-change check via embedding sugli span

Variante economica decisa il 2026-07-04: per il gate serve solo sapere se Q e A
hanno la stessa voce, non una diarizzazione completa. Costo atteso: secondi/run.

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella. Le run vanno nel layout standard
evaluations/<input>/runs/<run_label>/ (NON in sottocartelle dedicate).

Contesto: failure mode dominante del benchmark = risposta-continuazione dello
stesso parlante spacciata per replica. Esperimento misurato (default OFF, no
adozione in questo ciclo): verifica speaker-change sui soli span dei candidati
gia' estratti, senza diarizzazione completa.

Requisiti:
1. Nuovo check opzionale post-estrazione: per ogni candidato, estrarre l'audio
   dello span domanda e dello span risposta (timing gia' nei candidati),
   calcolare speaker embedding locali (modello open source scaricato una volta,
   es. ECAPA/speechbrain o pyannote embedding, CPU) e la similarita' coseno
   Q vs A. Output nel candidato: speaker_similarity_score + review flag
   same_speaker_suspected / different_speaker_likely.
2. Robustezza: se uno span e' troppo corto (< ~1.5s) o l'estrazione fallisce,
   NESSUN gate: flag speaker_check_unavailable, candidato non penalizzato.
3. Gate configurabile (per questo esperimento: solo penalita'/flag, nessun
   reject automatico): same_speaker_suspected + assenza di pattern socratico
   riconosciuto -> penalita' forte. Il socratico/self-answered locale resta
   esente.
4. Nessun download a runtime se il modello manca: fallback trasparente a
   check disattivo con nota nei metrics.
5. Misurare e riportare nei metrics: tempo di load del modello, tempo del
   check per candidato e per run.
6. File: modulo nuovo dedicato in analysis/ + hook in _qa_extractor_impl (o
   post-processing), config per l'attivazione. Test con fixture astratte
   incluso fallback senza modello e span corto.
7. Run warm con check ON su: deep_time, dialoghi, stanford, l25p08 (controllo:
   i socratici non devono essere penalizzati).

Accettazione (binaria):
- suite unittest mirata OK;
- 4 run nel layout standard con i 4 file, ai_review pending;
- metrics riportano i tempi del check; breakdown flag per candidato visibile
  nel review packet.
Consegna: diff, tempi, path run, entry PROJECT_DIARY. Valutazione qualitativa
a carico di Claude; adozione di default NON in questo ciclo.
```

## PROMPT 5b - R5b Diarizzazione completa misurata (solo se R5a insufficiente o per la linea recall)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella. Le run vanno nel layout standard
evaluations/<input>/runs/<run_label>/.

Contesto: la diarizzazione completa (pyannote, stage gia' predisposto ma
disabled) e' una-tantum per input e cacheable; oltre al gate speaker-change
abiliterebbe la struttura a turni per il recall su interviste/panel. Va
misurata prima di qualsiasi adozione.

Requisiti:
1. Abilitare la diarizzazione SOLO per deep_time e dialoghi, senza cambiare i
   default dei profili.
2. Gate speaker-change nel QA extractor attivo solo con speaker assegnati:
   risposta same-speaker -> penalita' forte, salvo socratico/self-answered
   locale (esente).
3. Misurare: costo cold per input (secondi), costo warm alla seconda run
   (atteso ~0 via artifact), delta candidati emessi/soppressi con breakdown.
4. Run per input: una cold-diarization + una warm di conferma riuso.
5. Test con fixture astratte per il gate.

Accettazione (binaria): suite OK; 4 run standard con ai_review pending;
report tempi cold/warm e delta candidati.
Consegna: diff, tempi, path run, entry PROJECT_DIARY. Valutazione e confronto
con R5a/R6 a carico di Claude.
```

## PROMPT 6 - R6 Esperimento misurato: responsiveness semantico locale sui candidati

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella. Le run vanno nel layout standard
evaluations/<input>/runs/<run_label>/.

Contesto: secondo esperimento misurato contro risposte-continuazione e
risposte topicamente adiacenti ma non responsive (stanford). Vincoli progetto:
locale, open source, nessuna API esterna, nessun costo per-run rilevante.

Requisiti:
1. Aggiungere un re-scorer semantico OPZIONALE (default off) che opera SOLO
   sui candidati gia' estratti (mai sull'intero transcript): modello sentence
   embedding locale piccolo e multilingue (it/en) gia' scaricabile offline una
   volta; nessun download a runtime se il modello e' assente -> fallback
   trasparente a off con nota nei metrics.
2. Segnali: similarita' domanda-risposta ed evidenza di risposta vs
   continuazione (es. similarita' risposta-domanda troppo alta = eco;
   risposta piu' simile alla continuazione della domanda che a una replica).
   Output: campo semantic_responsiveness_score nei quality_features del
   candidato + eventuale penalita' gate configurabile.
3. Misurare e riportare: tempo aggiuntivo per candidato e per run (atteso
   < qualche secondo su <=15 candidati), footprint del modello, tempo di load.
4. Eseguire run warm con re-scorer ON su: stanford, dialoghi, l25p08
   (controllo non-regressione socratico).
5. Test con fixture astratte, incluso fallback senza modello.

Accettazione (binaria):
- suite unittest mirata OK;
- 3 run warm prodotte con re-scorer attivo e tempi riportati nei metrics;
- overhead re-scorer per run documentato.
Consegna: diff, tempi, path run, entry PROJECT_DIARY. Valutazione qualitativa
e confronto R5 vs R6 (qualita'-per-secondo) a carico di Claude; nessuna
adozione di default in questo ciclo.
```

## STATO CICLI (aggiornato 2026-07-04 sera)

R1-R4: chiusi. R5a->R7.2: chiuso, speaker check ADOTTATO in quality_local
(waiver responsive, penalita' graduata, overlap exclusion, span extension).
R6: non adottato, diagnostico opzionale. R5b: in coda, solo per la linea recall.
Prossimo: refresh benchmark 7 input con gate ON, poi ciclo recall interviste.

## Esito confronto R5a vs R6 (2026-07-04, review Claude)

R5a vince nettamente su qualita'-per-secondo (dettaglio nel diary). R6 non adottato (resta
diagnostico opzionale). Prossimo ciclo Codex: R7 = adozione speaker check + fix regressione R4.

## PROMPT 7 - R7 Adozione speaker check + fix regressione R4

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Non toccare
file fuori da questa cartella. Le run vanno nel layout standard
evaluations/<input>/runs/<run_label>/.

Contesto: l'esperimento R5a (speaker-change check ECAPA sugli span dei candidati)
e' validato: segnale coerente con le review esterne, costo ~2s load + <0.2s/candidato.
Si adotta nel profilo quality_local. Inoltre il ciclo R4 ha introdotto una
regressione su dialoghi: il keep validato 'apertura intervista' (ex qa_0005,
domanda con '?' terminale e risposta responsiva adiacente) e' soppresso da
question_span_integrity.

Requisiti:
1. Fix regressione R4 (PRIMA del resto): il penalty/gate question_span_integrity
   non deve sopprimere una domanda con punteggiatura interrogativa terminale,
   confini di frase integri e risposta locale responsiva. Aggiungere test di
   non-regressione con fixture astratta che riproduce il pattern.
2. Adozione speaker check in quality_local (default ON se il modello locale
   esiste al path configurato, altrimenti fallback trasparente off):
   a. escludere dal check i candidati con span domanda/risposta sovrapposti
      (same-sentence/intra-frase): flag speaker_check_overlapping_spans,
      nessun giudizio;
   b. span domanda < min_span: invece di unavailable, estendere lo span audio
      ai confini dell'utterance/turno contenitore (solo audio, non il testo)
      fino a max 3s; se ancora corto -> unavailable come oggi;
   c. gate a penalita' (non reject): same_speaker_suspected senza pattern
      socratico/self-answered -> penalita' configurabile (default moderata),
      different_speaker_likely -> piccolo bonus di confidenza;
   d. reason code nei candidati e breakdown flag nel Coverage/packet.
3. Test con fixture astratte per 1, 2a, 2b, 2c (backend fake come gia' fatto).
4. File: qa_speaker_check.py, _qa_extractor_impl.py (integrity fix + gate),
   config. Nessuna nuova dipendenza.

Accettazione (binaria):
- suite unittest mirata OK;
- run warm sui 4 input (deep_time, dialoghi, stanford, l25p08):
  - dialoghi: l'apertura intervista torna emessa (3 candidati attesi) e
    qa_0015 riceve un giudizio speaker (non piu' unavailable);
  - l25p08: 7 candidati invariati, socratici con flag overlap/esenzione,
    nessuna penalita';
  - deep_time: i 3 same-speaker penalizzati o soppressi dal gate;
  - stanford: i flaggati .73+ penalizzati, i 2 sotto soglia intatti.
Consegna: diff, esito test, conteggi candidati e breakdown flag per run, entry
PROJECT_DIARY. Review esterna a carico di Claude.
```

## PROMPT 7.1 - R7.1 Fix gate speaker check (obbligatorio prima di dichiarare adottato)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Le run vanno nel
layout standard evaluations/<input_label_canonico>/runs/<run_label>/: usa i label
canonici esistenti (deep_time_and_intelligence_panel_..., dialoghi_di_scienza_ep2_-_astrofisica,
stanford_seminar_-_human-centered_..., l25p08), NON crearne di nuovi.

Contesto: review esterna di r5a_speaker_warm_20260704_fix1. Fix R4, overlap
exclusion e span extension OK. Il gate a penalita' pero' ha comportamento
invertito: su deep_time e stanford i candidati flaggati same_speaker hanno
confidence identiche alle run pre-gate (nessuna penalita' applicata), mentre su
dialoghi e' stato penalizzato qa_0015, un self-answered forte validato keep
(confidence .7745 -> .5245).

Requisiti:
1. Debug del wiring: individuare perche' la penalita' non si applica ai flaggati
   di deep_time/stanford e si applica invece al caso dialoghi; documentare la
   causa nell'entry diary.
2. Correzione: penalita' applicata a ogni same_speaker_suspected NON esente.
3. Esenzione estesa (oltre al socratico intra-frase): candidati con
   answer_responsiveness_strong + ancora lessicale/definitoria (es.
   answer_responsiveness_anchor) non vengono penalizzati: sono self-answered
   didattici legittimi. Reason code: speaker_penalty_waived_responsive.
4. Penalita' graduata sulla similarita': nessuna sotto la soglia, parziale
   nella fascia soglia-.85, piena sopra .85. Parametri configurabili.
5. Test con fixture astratte per 2, 3, 4 (backend fake).

Accettazione (binaria):
- suite unittest mirata OK;
- run warm sui 4 input canonici:
  - deep_time: qa_0005 e qa_0020 (sim .75+) con confidence RIDOTTA vs run fix1;
  - stanford: i flaggati sopra soglia con confidence ridotta (graduata), i 2
    sotto soglia intatti;
  - dialoghi: qa_0015 NON penalizzato (waiver responsive), qa_0005 al massimo
    penalita' parziale, qa_0026 penalizzato;
  - l25p08: 7 candidati, confidence tutte invariate.
Consegna: diff, esito test, tabella confidence prima/dopo per i 4 input, entry
PROJECT_DIARY. Ricorda: run SOLO nei label canonici sopra. Review esterna a
carico di Claude.
```

## PROMPT 7.2 - R7.2 Ripristino confidence base + sola penalita' graduata

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Le run vanno nei
label canonici esistenti in evaluations/ (deep_time_and_intelligence_panel_...,
dialoghi_di_scienza_ep2_-_astrofisica, stanford_seminar_-_human-centered_...,
l25p08), run label nuovo r5a_speaker_warm_20260704_fix4.

Contesto: review esterna di fix3. Waiver responsive e penalita' graduata OK.
Bloccante: R7.1 ha alterato il calcolo della confidence BASE. Evidenza:
stanford qa_0014 ha reason codes identici tra la run 2026-07-04_105047 e fix3
(unico delta: il flag same_speaker_suspected) ma confidence .662 -> .8904.
Il 'pre_gate .912' non corrisponde a nessuna run osservata.

Requisito unico e secco:
1. Individuare e revertire la modifica che ha cambiato la confidence base
   (probabile nel giro 'esenzione socratica ristretta / update confidence_label').
2. Comportamento atteso per OGNI candidato:
   confidence_finale = confidence_osservata_nelle_run_2026-07-04_105xxx
                        - penalita_speaker_graduata (0 se esente/waiver/sotto soglia).
   Nessun altro effetto su confidence, reason codes o emissione.
3. Test: fixture che verifica che senza speaker check la confidence coincida
   col valore pre-R7.1, e che col check cambi SOLO della penalita'.

Accettazione (binaria, tabella confidence per tutti i candidati dei 4 input):
- non flaggati/esenti/waiver: confidence IDENTICA alle run 2026-07-04_105xxx
  (l25p08 tutti; stanford qa_0001/qa_0004; dialoghi qa_0015 al valore .7745);
- flaggati non esenti: confidence = valore 105xxx meno penalita' graduata
  (mai superiore al valore 105xxx);
- suite unittest mirata OK.
Consegna: diff, tabella confidence 105xxx vs fix4 per tutti i candidati, entry
PROJECT_DIARY. Review esterna a carico di Claude.
```

## PROMPT 8 - R8 Recall interviste assistito dal segnale speaker

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Le run vanno nei
label canonici esistenti in evaluations/ (deep_time_and_intelligence_panel_...,
dialoghi_di_scienza_ep2_-_astrofisica, stanford_seminar_-_human-centered_...,
l25p08, l25p09, ssl1p1): NON creare cartelle nuove tipo deep_time/ o dialoghi/.

Contesto: il benchmark post-adozione speaker check (run 2026-07-04_1843xx) ha
coverage 0.02-0.06 sugli input dialogici: su dialoghi 153 frasi interrogative
producono 3 candidati, con 34 candidati formati ma soppressi dai gate
(breakdown nel Coverage Summary: low_autonomy_implicit_question=13,
surface_answer_cue_risk=7, below_min_qa_confidence=4, weak_answer_responsiveness=4...).
Ora abbiamo un segnale nuovo: una risposta con voce DIVERSA dalla domanda e' forte
evidenza di scambio genuino. Obiettivo: usarlo per recuperare recall senza
riaprire la precisione.

Requisiti:
1. Speaker-assisted rescue (solo quality_local, solo se modello speaker
   disponibile): i candidati soppressi ESCLUSIVAMENTE da gate morbidi
   (low_autonomy_implicit_question, weak_expanded_contextual_question,
   surface_answer_cue_risk, below_min_qa_confidence entro un margine
   configurabile, weak_answer_responsiveness) passano dal speaker check;
   se sim <= different_threshold (voce confidentemente diversa) E il candidato
   ha almeno un'ancora minima di responsivita', viene ri-ammesso con reason
   code speaker_rescued_candidate e review flag dedicato.
2. NESSUN rescue per: same_speaker_suspected, zona incerta tra le soglie,
   speaker_check_unavailable, o candidati soppressi da gate duri
   (poll/backchannel, answer-question, echo circolare, question_span_integrity).
3. Budget: cap configurabile sul numero di check per run (default 40) e sui
   rescue emessi (default 8); riportare i tempi del check esteso nei metrics.
4. Il Coverage Summary riporta: rescued_candidate_count e breakdown dei gate
   di provenienza dei rescued.
5. Test con fixture astratte (backend fake): rescue con voce diversa; NO rescue
   con voce uguale/incerta/unavailable; NO rescue da gate duro; cap rispettato.

Accettazione (binaria):
- suite unittest mirata OK;
- run warm su dialoghi, deep_time, l25p08, ssl1p1:
  - dialoghi: candidati emessi >= 6 (era 3), i 3 esistenti invariati, i rescued
    tutti con flag speaker_rescued_candidate;
  - l25p08: 7 candidati INVARIATI (monologo stesso speaker: zero rescue);
  - ssl1p1: 2 candidati invariati (zero rescue);
  - deep_time: al massimo rescue marginali, tutti flaggati.
Consegna: diff, esito test, conteggi e coverage prima/dopo per input, tempi del
check, entry PROJECT_DIARY. La qualita' dei rescued la giudica Claude in review
esterna: non ottimizzare sui testi specifici degli input.
```

## PROMPT 8.1 - R8.1 Qualita' dei rescued (floor testuale + trimming + filtro gestione)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Run nei label
canonici esistenti sotto evaluations/, run label nuovo con timestamp standard.

Contesto: review esterna di R8 (2026-07-04_210000). Il meccanismo rescue e'
validato: ha recuperato i primi veri scambi intervista del progetto (2 su
dialoghi). Ma dei 5 rescued totali: 2 sono spazzatura da regioni di
trascrizione garbled, 1 e' gestione conversazionale, e i 2 genuini hanno span
rotti (focus domanda sepolto in run-on, risposta troncata a meta' frase).

Requisiti (solo sul percorso rescue, non toccare l'estrazione normale):
1. Floor di sanita' testuale: un rescued con sentence quality molto bassa su
   domanda O risposta (riusare gli score esistenti, soglia configurabile) non
   viene emesso: reason speaker_rescue_rejected_text_quality.
2. Filtro gestione conversazionale sui rescued: riusare i rilevatori esistenti
   check-in/backchannel/ringraziamenti; se la risposta e' meta-conversazionale
   il rescue non emette: reason speaker_rescue_rejected_conversational.
3. Boundary/focus trimming sui rescued emessi:
   - domanda: se run-on, esportare il focus interrogativo (riusare
     normalized_question_text / focus recovery esistente);
   - risposta: troncare al primo periodo sintatticamente integro esteso
     (riusare answer_span_completion), mai a meta' frase.
4. Test con fixture astratte: garbled non emesso; conversazionale non emesso;
   rescued genuino emesso con focus trimmed e risposta a confine integro.

Accettazione (binaria):
- suite unittest mirata OK;
- run warm su dialoghi, deep_time, l25p08, ssl1p1:
  - dialoghi: i 2 rescued garbled non emessi; i 2 genuini emessi con span
    puliti (domanda = focus interrogativo, risposta senza troncamento a meta'
    frase); i 3 originali invariati;
  - deep_time: il rescued conversazionale non emesso, 3 candidati;
  - l25p08 e ssl1p1: invariati, zero rescue.
Consegna: diff, esito test, testi Q/A dei rescued finali di dialoghi, conteggi
per input, entry PROJECT_DIARY. Review esterna a carico di Claude.
```

## PROMPT 8.2 - R8.2 Chiusura R8.1: due rifiniture sui rescued

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Run nei label
canonici esistenti sotto evaluations/ (dialoghi_di_scienza_ep2_-_astrofisica,
deep_time_and_intelligence_panel_..., l25p08, ssl1p1), run label timestamp
standard. NON creare cartelle nuove.

Contesto: R8.1 e' validato dalla review esterna (filtri testuale/conversazionale
ok, zero regressioni). Restano SOLO due rifiniture sui rescued, gia' individuate
(la prima e' la patch che era stata bloccata dai crediti):

1. Answer completion su confine sospeso: se la risposta di un rescued termina
   senza punteggiatura terminale su un confine sintattico sospeso (es. finisce
   con '...come si'), estendere di UNA ulteriore sentence usando il completion
   gia' esistente; se anche cosi' resta sospesa, troncare all'ultimo periodo
   integro precedente. Mai emettere risposta che finisce a meta' sintagma.
2. Focus trimming domanda: il trimming del focus interrogativo sui rescued non
   deve tagliare dentro un sintagma (oggi produce 'Che cosa vuol dire studiare
   a un'): tagliare solo su confini di frase o clausola; se il focus pulito
   non e' isolabile, tenere la frase interrogativa intera piu' corta che
   contiene il focus.

Vincoli: solo percorso rescue, nessun effetto sui candidati non-rescued,
nessuna nuova dipendenza. Test con fixture astratte per entrambi i casi
(risposta sospesa estesa; risposta ancora sospesa troncata al periodo integro;
focus non tagliato dentro sintagma; fallback frase intera).

Accettazione (binaria):
- suite unittest mirata OK (>=160 preesistenti + nuovi);
- run warm su dialoghi, deep_time, l25p08, ssl1p1:
  - dialoghi: 5 candidati; i rescued qa_0033 e qa_0038 con domanda che non
    termina a meta' sintagma e risposta che chiude su periodo integro;
    i 3 originali invariati;
  - deep_time 3, l25p08 7, ssl1p1 2: tutti invariati.
Consegna: diff, esito test, testi Q/A finali dei 2 rescued di dialoghi,
e appendi tu l'entry di chiusura R8.1/R8.2 a PROJECT_DIARY.md (nota: le entry
di implementazione R8.1 e review esterna del 2026-07-04 sono gia' state
registrate da Claude, non duplicarle). Review esterna a carico di Claude.
```

## ENDGAME (2026-07-05): priorita' lezioni universitarie, chiusura prototipo

Budget: 2 prompt Codex. R9 = precisione lezioni. R10 = pulizia codice.
README/documentazione/report finale: Claude (zero crediti). Run finali: Matteo da terminale.

## PROMPT 9 - R9 Precision pack lezioni universitarie (ultimo ciclo estrazione)

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Run nei label
canonici esistenti sotto evaluations/, run label timestamp standard. NON creare
cartelle nuove.

Contesto: target primario del progetto = lezioni universitarie. l25p08 e' maturo
(keep 86%); l25p09 e' il caso debole (keep 29%) con difetti ricorrenti censiti
dalle review esterne. Ultimo ciclo di estrazione prima della chiusura prototipo:
SOLO regole strutturali, nessuna nuova dipendenza, nessun esempio hardcoded.

Requisiti:
1. Dedupe coppie eco: se la domanda di un candidato e' l'eco/riformulazione
   della risposta di un candidato adiacente e le due risposte si sovrappongono,
   emettere solo il migliore (reason echo_pair_deduplicated). Caso reale:
   l25p09 qa_0008/qa_0009.
2. Trimming di segmenti check-in/gestione INTERNI allo span risposta (oggi il
   filtro agisce solo su candidati interi): rimuovere inserti conversazionali
   brevi tra periodi didattici validi quando il resto dello span regge da solo
   (reason answer_internal_checkin_trimmed). Caso reale: l25p09 qa_0013
   ('ci siamo, mi state vedendo' dentro una spiegazione valida).
3. Penalita' deflessione: risposta breve senza ancora di contenuto condivisa
   con la domanda e composta prevalentemente da meta-riferimenti al corso/
   lezione (rilevati strutturalmente: nessun overlap lessicale col focus della
   domanda + assenza di cue definitorio/esplicativo) -> penalita' forte o gate
   (reason deflection_answer_penalty). Caso reale: l25p09 qa_0018 ('Non e'
   essenzialissimo per questo corso', conf .80).
4. Pulizie rescue accodate (rescue-only): cap configurabile di lunghezza
   risposta (default ~80 parole, tronca all'ultimo periodo integro entro il
   cap); focus trimming a CLAUSOLA COMPLETA che conservi l'oggetto della
   domanda quando presente.
5. Test con fixture astratte per 1, 2, 3, 4 (positivi + non-regressione:
   socratico breve legittimo non deduplicato ne' deflesso).

Accettazione (binaria):
- suite unittest mirata OK (>=164 preesistenti + nuovi);
- run warm su l25p09, l25p08, ssl1p1, dialoghi:
  - l25p09: la coppia eco ridotta a 1 candidato; il candidato deflessione non
    emesso o con confidence bassa; lo span con check-in interno ripulito;
    gli altri candidati validi invariati;
  - l25p08: 7 candidati INVARIATI;
  - ssl1p1: <=2 candidati, nessun nuovo emesso;
  - dialoghi: 5 candidati, rescued con risposta entro il cap e domanda con
    oggetto conservato.
Consegna: diff, esito test, conteggi e testi dei candidati cambiati, entry
PROJECT_DIARY. Review esterna a carico di Claude.
```

## PROMPT 10 - R10 Pulizia codice per chiusura prototipo

```
Lavora solo su /Users/matteopogetta/Documents/ExerPlazaProject. Nessuna run
richiesta (solo verifica test). Nessun cambiamento di comportamento salvo dove
indicato.

Contesto: chiusura prototipo. Obiettivo: consolidare senza riprogettare.
La review esterna di R9 ha trovato 2 correzioni da fare PRIMA della pulizia.

Requisiti:
0. Fix R9 (PRIORITARIO, comportamentale):
   a. Dedupe eco, selezione del vincitore: nella coppia deve PERDERE il membro
      la cui DOMANDA duplica/eco la risposta dell'altro (e' l'eco-question);
      tie-break: meno penalita' di rumore (backchannel/troncamento), poi
      confidence. Caso reale: su l25p09 va tenuto qa_0008 (domanda definitoria,
      risposta pulita) ed eliminato qa_0009 (domanda-eco, risposta con
      backchannel), oggi avviene il contrario.
   b. Cap risposta rescue: se nessun periodo integro esiste entro il cap
      (regioni con punteggiatura rada), troncare all'ultimo confine di
      CLAUSOLA, mai lasciare la coda sospesa (oggi dialoghi qa_0033 termina
      a meta' frase).
   Test astratti per entrambi. Verifica con run warm su l25p09 e dialoghi:
   l25p09 tiene la domanda definitoria della coppia eco; dialoghi qa_0033
   chiude su confine di clausola.
1. Confidence path: eliminare l'emulazione legacy introdotta in R7.2/fix4
   rendendo quel calcolo l'UNICO percorso canonico (stesso output attuale,
   verificato dai test); rimuovere i campi di audit temporanei se non piu'
   necessari o marcarli deprecati.
2. Flag e config: censire le opzioni CLI/config introdotte nei cicli R5-R8
   (speaker check, rescue, semantic responsiveness) e:
   - default coerenti col deciso: speaker check ON se modello presente,
     rescue ON in quality_local, semantic responsiveness OFF;
   - rimuovere flag morti o esperimenti abbandonati;
   - docstring/help CLI accurati per ogni flag rimasto.
3. Igiene: rimuovere codice morto evidente nei moduli toccati dai cicli
   R1-R8 (analysis/, output/, scripts/), NESSUN refactoring architetturale.
4. Fixture: verificare che nessun test contenga frasi da transcript reali
   (regola marker astratti), sistemare eventuali residui.
5. Suite COMPLETA unittest verde (tutti i moduli tests/, escluso
   test_placeholder_cli se l'ambiente pytest resta rotto: in tal caso
   documentarlo nel diary).

Accettazione: suite verde; docstring/help aggiornati; entry PROJECT_DIARY con
elenco di cio' che e' stato rimosso/consolidato e l'inventario finale dei flag
con i loro default.
Consegna: diff, esito test, inventario flag, entry diary. La documentazione
utente (README, docs/) NON e' in scope: la scrive Claude dopo questo ciclo.
```

## Chiusura (dopo R9+R10, a carico di Claude + Matteo)

1. Matteo: benchmark finale 7 input da terminale (gate e rescue ON, default).
2. Claude: valutazione finale, overview conclusiva con confronto inizio/fine,
   aggiornamento README.md e README.it.md, revisione docs/ (architecture,
   quality_evaluation, local_installation con speaker model setup), report di
   chiusura prototipo in docs/.
3. Igiene finale evaluations/: cancellazioni manuali gia' elencate + eventuale
   archiviazione run sperimentali.

## Dopo R3

Claude valuta le 7 run (comando "Valuta" sul benchmark), produce overview aggiornata con coverage, e decide:
- se keep >= 70% e coverage_ratio accettabile sugli input dialogici -> consolidare, dichiarare la fase estrazione matura, passare a robustezza/UI;
- altrimenti -> micro-ciclo semantico locale (stop-condition sopra), oppure ciclo mirato sul failure mode dominante emerso dalla coverage.
