# Piano di implementazione — Idea Radar

*24 luglio 2026 — basato su analisi del codice (backend + frontend) e verifica delle API gratuite disponibili oggi.*

Il progetto è in ottimo stato: pipeline completa e testata (~135 test), nessun TODO irrisolto, design system frontend maturo. Il piano quindi non "ripara": estende. Quattro assi — fonti, architettura, feature, frontend — con una roadmap in fondo.

---

## 1. Fonti

### 1.0 Prerequisito: fonti config-driven (da fare per primo)

Oggi aggiungere una fonte tocca **4 punti sparsi**: il registry hardcoded in `sources/base.py:34`, i campi piatti di `SourceConfig` (`appconfig.py:16`), e soprattutto **5 costanti per-fonte in `scoring.py:33-53`** (`_VELOCITY_CAP`, `_SATURATION_CAP`, `_SOURCE_CREDIBILITY`, `_LIVE_COUNTER_SOURCES`, più la logica per-fonte di `absolute_engagement`). Una fonte non registrata lì cade su default generici e perde la heat misurata.

**Intervento:** ogni collector espone un *descriptor* (dataclass) con `velocity_cap`, `saturation_cap`, `credibility_base`, `is_live_counter`, `engagement_fields`; `scoring.py` legge dal descriptor invece che dai dict; il registry diventa un dict popolato dai moduli stessi; `SourceConfig` diventa discriminated union su `type`. Dopo questo, una fonte nuova = 1 file + 1 voce in config.yaml.

### 1.1 Quick win a costo zero (solo config.yaml)

Fonti RSS/Atom già supportate da `rss.py` senza codice: dev.to (`/feed/tag/X`), blog engineering (Cloudflare, Vercel, Fly.io…), newsletter tech con feed. Lobsters è già presente.

### 1.2 Nuovi collector, in ordine di rapporto valore/sforzo

| Fonte | API | Sforzo | Note |
|---|---|---|---|
| **arXiv** | Atom, gratis, senza chiave | Basso | Riusa quasi tutto `rss.py`; query per categoria (`cs.AI`, `cs.SE`). Segnale *early* per eccellenza. Non live-counter: heat euristica. |
| **Product Hunt** | GraphQL v2, token gratuito | Medio | Quota 6.250 punti complessità + 450 req / 15 min; approvazione richiesta per uso commerciale. `votesCount` è un live counter → heat delta reale come GitHub/HN. Collector sul pattern di `github.py`. |
| **Stack Exchange** | REST, gratis con key | Medio | Domande in crescita per tag = domanda insoddisfatta, segnale complementare all'offerta (repo). |
| **Reddit** | JSON/OAuth | Basso valore ora | Free solo non-commerciale, 100 QPM, **registrazione OAuth chiusa: approvazione manuale 2–4 settimane**. Tenere l'attuale copertura RSS (già in config, con le note su Cloudflare in `config.yaml:31-34`) e rimandare il collector dedicato. |

### 1.3 Arricchimento engagement: download stats

Non una fonte ma un *enricher*: per idee che citano un pacchetto, interrogare [pypistats.org](https://pypistats.org/api/) (ultimi 180 giorni) o `api.npmjs.org` per la velocity dei download. Alimenta `ItemStat` con un secondo segnale di trazione, indipendente dalle stelle.

---

## 2. Architettura

In ordine di urgenza:

1. **Scalabilità clustering** — `attach_item_to_idea` è O(n²) in Python puro (`clustering.py:57`), e ogni item ricalcola similarità contro tutte le idee. Primo passo economico: vettorizzare con numpy (matrice centroidi in RAM, un `argmax` per item). Se il DB cresce oltre ~10⁴ idee: `sqlite-vec` come indice ANN, restando su SQLite.
2. **Query in SQL** — `latest_scores()` carica tutti gli Score in memoria (`queries.py:10`) e `/ideas` ordina e taglia in Python (`api.py:228-240`). Spostare ordinamento, filtri e paginazione (offset o cursor) in SQL. Serve anche al frontend (§4).
3. **Throughput dei run** — LLM ed embedding sono seriali, una chiamata HTTP per item, più commit annidati per item (`pipeline.py:257`, `clustering.py:27`). Interventi: batch embedding (endpoint `/api/embed` di Ollama accetta liste), commit per fase invece che per item, piccolo pool (2–3 worker) per gli insight LLM.

   **Aggiornamento 27 luglio — fatto in parte, e il pool va escluso.** Batch
   embedding: fatto (`throughput.embed_batch_size`, 280 richieste diventate 9).
   Commit: la fase di embedding ne fa uno invece di uno per item e la scrittura
   dell'avanzamento ha un throttle temporale; i commit degli score restano per
   item **di proposito**, perché accorparli terrebbe la transazione di scrittura
   decine di secondi e con `busy_timeout=30000` un `PATCH /ideas/{id}` scadrebbe
   durante un run. Il pool di worker è stato *misurato* prima di scriverlo: due
   richieste identiche a Ollama costano 15,7s in serie e 16,7s in parallelo —
   le accoda, e due slot su un 7B si dividerebbero comunque la stessa banda di
   memoria. Non c'è niente da parallelizzare lato client. Quel che resta del
   tempo è latenza per chiamata (~7s): l'unica leva vera è §3.6, un modello di
   insight più piccolo, che è una scelta di qualità e non di codice.
4. **Healing dei singleton** — un item assegnato a un'idea non viene mai ri-aggregato (`clustering.py:49`): i run degradati lasciano singleton permanenti. Aggiungere `idea-radar heal`: individua idee singleton con embedding, prova a fonderle con idee vicine sopra `idea_threshold`. Complementare al preflight già esistente.
5. **Igiene** — allineare i default di `appconfig.py:31` (0.82/0.62) ai valori tarati di config.yaml (0.75/0.70); pianificare la migrazione a timestamp tz-aware (`models.py:10`) prima che nuovo codice erediti la trappola.

---

## 3. Feature di prodotto

1. **Azioni utente sulle idee** — il gap più grande: oggi il radar è read-only. Aggiungere su `Idea` uno stato utente (`pinned`, `dismissed`, `seen_at`, `note`) + `PATCH /ideas/{id}` con test. Il dismiss manuale deve sopravvivere ai run (non farsi "rivivere" dal lifecycle automatico). Sblocca metà del piano frontend.
2. **Digest** — comando `idea-radar digest`: report markdown delle idee promosse a `proposed` dall'ultimo digest (nuove entry, mover di trend). Con lo scheduler launchd già in piedi, diventa un briefing automatico.
3. **Storico run consultabile** — l'API c'è già (`GET /runs`), manca solo la UI (§4).
4. **Profili keyword** — set di keyword nominati in config.yaml (es. "AI tooling", "dev-infra") con fit calcolato per profilo; il radar diventa multi-tema senza duplicare il DB.
5. **Export** — `GET /ideas?format=csv` o comando CLI: banale, utile.
6. **Modello insight configurabile più piccolo** — già in roadmap README; con il fit-gate esistente è il knob giusto per hardware modesto.

---

## 4. Frontend

1. **Routing** — tutto vive in `useState<View>` (`App.tsx:52`): niente URL, deep-link, back. React Router con rotte `/radar`, `/topics/:id`, `/ideas/:id`, `/trends`, `/monitor`. Il drawer idea diventa linkabile.
2. **Data layer** — sostituire il blocco monolitico `loadAll` + `Promise.all` (un solo loading/error globale, zero cache, `App.tsx:84-103`) con TanStack Query: caching, errori per-risorsa, refetch, e il polling a 2s diventa `refetchInterval` condizionale.
3. **Azioni idea in UI** — pin/dismiss/nota su `IdeaCard` e `IdeaDetail` (dipende da §3.1); esporre il filtro `archived` già supportato dall'API; filtri status/topic server-side (l'infrastruttura in `api.ts:18-24` esiste ed è inutilizzata).
4. **Drill-down Trend → Topic** — i Panel in `TrendsView.tsx:102` sono `interactive` senza handler: click sul trend → topic aperto in TopicsView (con routing diventa un link).
5. **Storico run in Monitor** — `api.runs()` è implementato e mai usato: lista run completa con dettaglio.
6. **A11y** — focus trap + restore nel drawer, `role="tablist"` sulla nav, blip del radar raggiungibili da tastiera, tooltip su focus oltre che hover.

   **Aggiornamento 27 luglio — fatto, tranne il tablist che non va fatto.**
   Trappola del focus: `useFocusTrap`, con ripristino su chi aveva il focus.
   Blip: roving tabindex — una sola fermata di Tab, frecce per scorrere, Invio
   per aprire, anello di focus visibile e tooltip anche al focus. Sessanta
   fermate di Tab per un grafico i cui dati sono nella lista sotto sarebbero
   state un peggioramento, non un'accessibilità. `role="tablist"` **no**: quella
   riga è stata scritta quando la nav era `useState<View>`; ora i tab sono link
   con un URL e una pagina propria, e marcarli come tab farebbe perdere
   l'informazione che sono link imponendo la navigazione a frecce di un widget
   composito. Il pattern giusto è `<nav aria-label>` + `aria-current`, che
   NavLink già emette. Le sparkline sono diventate `role="img"` con un nome, per
   lo stesso motivo dei blip: i loro numeri sono già scritti accanto.
7. **Refactor mirati** — estrarre `useRadarData()` e `Nav` da App.tsx; geometria dei blip (`RadarScope.tsx:28-53`) in funzione pura testabile; formatter data unico (oggi triplicato in MonitorView/TrendsView/IdeaDetail); verificare il contratto di `startRun` (`App.tsx:131` legge `res.detail`, il backend risponde 202 con altro body).

   **Aggiornamento 27 luglio — fatto.** `Nav` in `components/Nav.tsx` (con sé
   porta via quattro pezzi di stato che non servivano a nessun altro: App.tsx da
   304 a 241 righe); geometria in `components/radarGeometry.ts` con 9 test —
   quelle erano le affermazioni matematiche del radar e non ne era verificata
   nessuna; date in `src/dates.ts`, dove `formatTime` e `shortDate` si sono
   rivelate la stessa funzione con due nomi, e ora un valore non valido dà "—"
   invece di stampare "Invalid Date" nell'interfaccia. Il contratto di
   `startRun` **era già a posto**: quella riga descrive il codice di prima che
   l'endpoint diventasse `POST /runs` con il modello `RunStarted`, e il
   frontend legge esattamente i campi che il backend manda (test in
   `test_api.py`).

---

## Roadmap proposta

| Fase | Contenuto | Perché in quest'ordine |
|---|---|---|
| **1. Fondamenta** | §1.0 fonti config-driven · §2.2 query SQL + paginazione · §3.1 azioni utente (modello + PATCH) | Tutto il resto ci si appoggia |
| **2. Prodotto** | §4.1-4.3 routing + data layer + azioni in UI · §1.2 arXiv + Product Hunt | Valore visibile all'utente |
| **3. Robustezza** | §2.1 clustering numpy · §2.3 batch/parallelismo · §2.4 heal | Prima che il DB cresca |
| **4. Rifiniture** | §3.2 digest · §4.4-4.7 drill-down, storico run, a11y, refactor · §1.3 enricher download | Incrementali, senza dipendenze |

Ogni voce rispetta le convenzioni esistenti: config in `config.yaml`/`.env`, comandi via `uv run`, test per ogni endpoint nuovo, policy di scheduling in `scheduling.py`.

### Riferimenti fonti esterne

- [Reddit API pricing e limiti 2026](https://www.socialcrawl.dev/blog/reddit-data-api-2026) · [approvazione OAuth](https://www.techloy.com/reddit-api-pricing-in-2026-complete-guide-for-developers-and-businesses/)
- [Product Hunt API rate limits](https://api.producthunt.com/v2/docs/rate_limits/headers) · [docs](https://api.producthunt.com/v2/docs)
- [arXiv API](https://info.arxiv.org/help/api/index.html)
- [PyPI Stats API](https://pypistats.org/api/)
