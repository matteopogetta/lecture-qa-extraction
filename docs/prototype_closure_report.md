# Report di Chiusura Prototipo - Lecture QA Extraction

Data: 2026-07-05. Ruoli: Codex (implementazione), Claude (decisioni, review esterna), Matteo (direzione, esecuzione run).

## Obiettivo e verdetto

Obiettivo: estrarre coppie Q/A/C di qualità da lezioni universitarie (e contenuti didattici affini) nel minor tempo possibile, con stack locale, open source e senza API esterne.

Verdetto: prototipo funzionante e congelato. Il profilo `quality_local` + segmentazione `structural` è la configurazione di riferimento: su lezioni universitarie con domande didattiche produce candidati affidabili (input di riferimento: 86% keep, zero reject), sui monologhi puri emette correttamente zero candidati, e sui contenuti dialogici recupera scambi genuini tramite il segnale vocale. I target ideali (keep ≥ 70% globale) non sono raggiunti sul benchmark misto, ma i falsi sistematici sono stati eliminati e ogni candidato emesso porta flag di rischio utilizzabili a valle.

## Numeri finali (benchmark 7 input, review esterna per candidato)

46 candidati emessi: keep 24 (52%), revise 14 (30%), reject 8 (17%). Qualità media run 3.14/5 (da 2.86 a inizio ciclo di valutazione). Dettaglio per input in `evaluations/benchmark_overview_2026-07-03.md` (locale) e in `docs/quality_evaluation.md`.

Runtime: su run warm il costo QA + speaker check è 2–5s per input; il costo cold resta dominato dalla trascrizione (faster-whisper), una-tantum per input e riusata via cache. Il profilo `quality_local` pareggia la qualità del profilo `full` a ~0.28x del tempo cold.

## Cosa è stato costruito (cicli R1–R10.1, 2026-07-03 → 2026-07-05)

Recall strutturale: recupero di domande senza `?` terminale, domande spezzate su più frasi, run-on con risposta locale (R1/R1.1). Misura del recall: `qa_coverage` in metrics e Coverage Summary nel review packet, con breakdown dei gate di soppressione (R2). Precisione rule-based: integrità degli span, completamento risposte, reject di tag-question isolate e poll/backchannel, dedupe delle coppie eco, trimming dei check-in interni agli span, penalità per risposte-deflessione (R4, R9, R10). Speaker check: confronto ECAPA delle impronte vocali domanda/risposta con penalità graduata, waiver per self-answered didattici forti, esclusione span sovrapposti, estensione audio degli span brevi (R5a, R7–R7.2). Rescue speaker-assistito: ri-ammissione di candidati soppressi da gate morbidi quando la voce della risposta è confidentemente diversa, con floor testuale, filtro conversazionale e rifinitura dei confini (R8–R8.2). Consolidamento: percorso confidence canonico, censimento flag con default coerenti, il contesto non guida mai l'emissione (R10/R10.1).

Esperimenti valutati e non adottati: scorer semantico di responsività (R6: load per-run dominante, penalizza risposte brevi corrette — resta opt-in diagnostico); diarizzazione completa (R5b: mai eseguita, in coda come lavoro futuro).

## Metodo di lavoro validato

Micro-cicli a scope chiuso con criteri di accettazione binari; review esterna indipendente di ogni run (ai_review.json per candidato) come ground truth; benchmark congelato di 7 input; separazione osservato/cold-equivalent/warm nei tempi; regola dei marker astratti nei test. La review esterna ha intercettato 4 regressioni non visibili dai test (winner sbagliato nel dedupe, confidence base alterata, thin_context promosso a gate, keep soppresso da span integrity) — è il controllo di qualità decisivo del progetto.

## Limiti noti (fuori scope prototipo, in ordine di impatto)

1. Panel/tavole rotonde multi-voce: senza diarizzazione completa i candidati sono continuazioni same-speaker; oggi vengono correttamente penalizzati/rifiutati, ma il contenuto reale resta inaccessibile (deep_time: 0 keep). Rimedio noto: R5b nel decision plan.
2. Recall interviste basso in assoluto (coverage ~0.03–0.05) pur con rescue attivo: molti scambi genuini restano sotto i gate. Rimedi noti: estendere il rescue, diarizzazione a turni.
3. Reject semantici residui: risposte adiacenti, topicamente affini ma non responsive (stanford/eugenia). Rimedio abbozzato: reject-flagger semantico mirato alle deflessioni, non scorer generale.
4. Classe revise su lezioni lavagna-dipendenti: risposte deittiche ("qua, qua") e code troncate dalla trascrizione — limite in parte della sorgente audio, non dell'estrazione.
5. Soglie speaker (same .73 / full .85) deboli per voci simili nella stessa registrazione: un vero scambio a due voci può risultare same-speaker (penalità parziale accettata su un keep noto).

## Come riprendere lo sviluppo

Leggere `docs/decision_plan_2026-07-03.md` (storico delle decisioni e prompt pronti: R5b diarizzazione misurata; linee future annotate). Il benchmark congelato e le review candidate-per-candidato in `evaluations/` sono la ground truth per misurare qualsiasi modifica: rieseguire il batch warm da terminale costa minuti, la trascrizione è in cache.
