# Piano v2 — Il radar che si valuta, e le idee che si sviluppano

*1 agosto 2026 — dopo la release desktop 0.1.x. Quattro assi, decisi insieme:
il prodotto non deve diventare commerciale ma un progetto completo e onesto —
portfolio oggi, forse open source domani. Niente onboarding/config UI: chi lo
usa sa mettere mano a un YAML.*

---

## Asse A — Track record: il radar impara se aveva ragione

**Il problema.** Il radar sostiene di predire opportunità ma non ha mai
controllato una propria previsione. Heat, opportunity, fit: plausibili, mai
provati. È la mancanza più grave — e la feature più distintiva da costruire,
perché quasi nessun "trend tool" si auto-valuta.

**L'intuizione chiave: i dati ci sono già.** `Score` conserva la storia dei
punteggi di ogni idea run per run; `ItemStat` conserva la storia
dell'engagement (stelle, punti, download) di ogni item a ogni osservazione.
Per ogni idea proposta al tempo T possiamo già ricostruire cos'è successo
DOPO T. Non serve nessuna fonte nuova: serve un giudice.

**Il verdetto.** Un modulo `outcomes.py` che, per ogni idea promossa a
`proposed` da almeno N giorni (default 30), guarda l'engagement dei suoi item
nella finestra successiva alla promozione e la classifica:

- **hit** — l'engagement ha continuato a crescere sopra una soglia
  (es. +50% della velocity pre-promozione);
- **flat** — viva ma ferma;
- **miss** — morta (nessun segnale nuovo, velocity a zero).

Il verdetto si calcola solo su fonti *live counter* (dove il delta è
misurato, non inventato) e si scrive in una tabella `idea_outcomes`
(idea_id, promoted_run_id, verdict, horizon_days, computed_at) — così la
storia dei giudizi è essa stessa ispezionabile e ricalcolabile.

**Il pannello.** In Monitor (o vista propria "Verdetti"): hit-rate
complessivo, per profilo e per fonte; la distribuzione hit/flat/miss nel
tempo; e per ogni idea il grafico con il marker "proposta qui" — il momento
della previsione, visibile sopra la curva di ciò che è successo dopo.

**L'apprendimento (fase 2, solo dopo che i verdetti girano).** Prima di
toccare i pesi: un report. Su tutte le idee giudicate, quale metrica al
momento della proposta separava meglio gli hit dai miss? (Una regressione
logistica in numpy basta, niente ML pipeline.) Il report *suggerisce* pesi —
non li applica: i pesi restano in config.yaml, decisi da un umano che ha
letto il report. Auto-tuning silenzioso = debugging impossibile.

## Asse B — Qualità LLM: meno testo, più vincolato, verificato

I testi generati sono la parte più letta e la più debole (mosse
passe-partout nonostante il prompt le vieti; angoli di business incoerenti).
Tre interventi, in ordine di resa:

1. **Validazione post-generazione.** Le mosse che matchano pattern generici
   ("scrivi una guida di riferimento", "domina il canale", "monitora gli
   sviluppi") vengono rifiutate e rigenerate UNA volta con il motivo del
   rifiuto nel prompt; alla seconda bocciatura l'idea resta senza mosse
   (meglio di mosse finte — stessa filosofia del topic label cinese).
2. **Coerenza dell'angle.** Similarità embedding tra angle generato e
   testo dell'idea: sotto soglia = l'angle parla d'altro → scarta e riprova.
   Il caso "designer freelance/agenzia viaggi per un modello GGUF" non deve
   arrivare in UI.
3. **Few-shot nei prompt.** Un esempio buono e uno cattivo (commentato) per
   mosse e angle: i 7B seguono gli esempi molto meglio delle regole.

Upgrade del modello (14B) resta un knob dell'utente, non un requisito.

**Aggiornamento 14 agosto — fatto, con una correzione al punto 1.** La
validazione delle mosse non rigenera al primo sospetto: le passe-partout
vengono *tolte* e, se ne resta almeno una buona, quella basta — rigenerare
per riscrivere due mosse già valide costerebbe sette secondi. La
rigenerazione (una sola, con l'elenco delle scartate dentro il prompt) scatta
quando non sopravvive niente; alla seconda bocciatura l'idea resta senza. Il
rifiuto è un'eccezione a sé, `GenerationRejected`, e la distinzione conta più
di quanto sembri: se Ollama è giù la fase si ferma (è giù per tutti), se la
risposta è generica si prosegue con le altre idee. Stessa logica sull'API:
503 quando Ollama non c'è, 422 quando ha risposto male — la UI dice due cose
diverse invece di mandare a controllare Ollama in entrambi i casi.

Il budget della fase ora conta le chiamate *vere* e non le idee: da quando una
generazione può costarne due, un tetto che contava le idee non conteneva più i
secondi che doveva contenere.

Coerenza dell'angle e few-shot: come previsti. Le soglie
(`moves.angle_min_similarity`, `moves.generic_patterns`) stanno in
config.yaml, e ogni scarto finisce nel log con la sua similarità — si tarano
guardando i numeri, come la soglia dei video.

## Asse C — Il radar e i video con un senso

**Lo scope deve informare, non solo scenografia.** Oggi la distanza dal
centro è il punteggio ma l'angolo non codifica nulla: i blip si ammassano a
caso. Nuova geometria: **uno spicchio per profilo** (l'angolo = il tema),
distanza = punteggio come oggi. Colpo d'occhio reale: "sta nascendo roba
negli agenti, il mio spicchio IoT è vuoto". Con legenda, e lo spicchio si
accende quando il tema è selezionato — la geometria è già in
`radarGeometry.ts` testata, si estende lì.

**Video pertinenti.** Peppa Pig in dashboard mina tutto. Filtro in
`videos.py`: similarità embedding tra titolo del video e keyword del profilo
(il modello di embedding è già in casa), sotto soglia = fuori; più blocklist
di canali. E un passo in più: **video per idea** — nel dossier di un'idea
salvata, ricerca YouTube on demand sul suo label ("cosa dicono di questa
cosa specifica"), con la stessa cache 15 minuti.

**Novità visibili.** I blip entrati nell'ultimo run pulsano diversi per un
giro di sweep: il radar deve far percepire il *nuovo*, che è il suo mestiere.

**Aggiornamento 14 agosto — fatti anche gli ultimi due pezzi.** *Video per
idea*: endpoint proprio (`/ideas/{id}/videos`), ricerca sul label, stessa cache
di 15 minuti — ma **non parte all'apertura del dossier**. Una ricerca costa 100
unità delle 10.000 quotidiane e un dossier si apre molte più volte di quante i
video interessino: la spesa la autorizza un click. Il filtro di pertinenza è lo
stesso codice del pannello, con un ancoraggio solo (il label invece delle
keyword del tema) — se il titolo non somiglia a ciò che si è cercato, YouTube
ha risposto d'altro. E "nessuno ne parla" viene scritto come informazione, non
lasciato come vuoto: per un'idea in salita è esattamente il punto.

*Novità visibili*: `first_seen` dopo l'inizio dell'ultimo run = contatto nuovo,
e il blip si porta un'eco che si allarga quando la spazzata gli passa sopra —
stesso periodo e stesso delay del lampo, così parte dal passaggio. La freschezza
si calcola in `radarGeometry.ts` con i suoi test, e finisce anche nel nome
accessibile del blip: un'animazione, per chi usa uno screen reader, non esiste.
Legenda solo quando c'è almeno un contatto nuovo.

## Asse D — "Sviluppo": le idee salvate diventano un piano di lavoro

Oggi il pin è un segnalibro. La vista nuova ("Sviluppo") lo trasforma in un
tavolo di lavoro, SENZA toccare la pipeline:

- **Stati utente**: da esplorare → in sviluppo → parcheggiata (kanban a tre
  colonne, drag opzionale — anche solo un selettore basta).
- **Le mosse diventano checklist**: le mosse LLM dell'idea arrivano come
  to-do spuntabili, più to-do liberi dell'utente.
- **Collegamenti**: URL a repo/progetti personali/note esterne.
- **Attività dal radar**: per ogni idea salvata, "cosa è successo da quando
  la segui" — nuovi item agganciati, delta engagement, cambi di punteggio.
  È l'asse A al servizio del singolo progetto dell'utente: il radar continua
  a lavorare per le idee che hai scelto.

**Modello dati**: tabella `workspace` separata (idea_id, stage,
checklist_json, links_json, updated_at) — lo stato di sviluppo è
dell'utente, non della pipeline; i run non la toccano mai (stessa regola di
pin/dismiss). Endpoint CRUD con test, vista frontend con la stessa grammatica
visiva (pannelli glass, hud label).

---

## Ordine proposto

| Fase | Cosa | Perché prima |
|---|---|---|
| 1 | A: verdetti + pannello track record | Dati già pronti; è la tesi del prodotto che si dimostra |
| 2 | C: spicchi per profilo + filtro video | Resa visiva immediata, poco codice, ripaga ogni screenshot |
| 3 | D: vista Sviluppo | Nuova superficie di prodotto, si appoggia ad A per l'attività |
| 4 | B: validazione LLM | Trasversale, si può fare a pezzi in mezzo alle altre |
| 5 | A fase 2: report di calibrazione | Ha senso solo con qualche mese di verdetti accumulati |

Convenzioni invariate: config in config.yaml, test per ogni endpoint nuovo,
niente servizi a pagamento, LLM solo locale.
