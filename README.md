# Idea Radar

**Surface rising tech opportunities before they saturate — not *what's popular*, but *what's climbing and still open*.**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-Ollama%20(local)-000000?logo=ollama&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

Idea Radar collects signals from Hacker News, GitHub, and tech RSS feeds, groups
them by meaning, and ranks them by **opportunity**. The guiding idea: a project
with 100k stars accumulated over six years is a *closed market*, not an opening.
A repo with 2k stars in three months might be one. The radar is built to tell
those two apart.

Everything runs on **free APIs and a local LLM** (via [Ollama](https://ollama.com/)).
No paid services, no cloud model calls, no data leaving your machine.

---

## How it works

```mermaid
flowchart LR
    HN[Hacker News] --> Items[(Items)]
    GH[GitHub] --> Items
    RSS[RSS feeds] --> Items
    Items --> Emb[Embeddings<br/>nomic-embed-text]
    Emb --> Ideas[Ideas<br/>semantic dedup]
    Ideas --> Topics[Topics<br/>persist across runs]
    Items --> LLM[LLM insight<br/>qwen2.5]
    Ideas --> Score[Scoring]
    LLM --> Score
    Score --> UI[Radar · Topics · Trends · Monitor]
    Topics --> UI
```

A single **run** walks the whole pipeline: fetch raw items from the sources,
embed them locally, collapse items that describe the same thing into one **idea**,
group related ideas into **topics** that persist across runs (so trends become
measurable), and score everything.

### Scoring

Each idea gets four quality metrics, combined into a weighted average (`quality`)
and multiplied by relevance:

```
composite = quality × (relevance_floor + (1 − relevance_floor) × fit)
```

- **heat** — *speed* of growth (stars/day on GitHub, engagement on HN/RSS), not
  absolute popularity.
- **credibility** — trustworthiness of the source and author.
- **feasibility** — how buildable it is for a team of 1–3 people, estimated by
  the LLM against an explicit rubric.
- **opportunity** — recent **and** not yet saturated. This is the brake that
  keeps established, finished projects off the top.
- **fit** — adherence to your keywords. Not an addend but a **multiplier**: an
  off-topic idea is pulled down even if it's wildly popular.

Above `scoring.threshold`, an idea is promoted to `proposed`. Every parameter
lives in [`backend/config.yaml`](backend/config.yaml).

### Aggregation & topics

Local embeddings do two jobs with one mechanism: merge different signals that
tell the same story into a single idea (deduplication), and group related ideas
into topics. Because topics persist between runs, a theme that grows from one run
to the next becomes a **trend** — the core of the Trend view. (With a single run
the Trend view is empty by construction: it needs at least two.)

---

## Features

- **Opportunity-first ranking** that rewards momentum over accumulated popularity.
- **Semantic deduplication** — the same launch on HN, GitHub, and a blog collapses
  into one idea.
- **Local-only LLM** for summaries, "why it matters" notes, and difficulty
  estimates — nothing is sent to a paid API.
- **Trends across runs** — topics are tracked over time so you can see what's rising.
- **Resilient collection** — a rate-limited or broken feed is skipped, never
  crashes a run; RSS fetching is polite (honest User-Agent, throttling, `Retry-After`).
- **Cost-aware LLM use** — insights are cached per idea (repeat runs only pay for
  new content) and clearly off-topic items skip the model entirely (fit-gate).
- **Hands-free trend accumulation** — a launchd agent runs the pipeline every few
  hours while the Mac is awake, with catch-up after sleep/reboot, a cross-process
  lock, and an Ollama preflight so unattended runs never degrade the data.
- **Four views** — Radar (ranked ideas), Topic (grouped by theme), Trend (what's
  moving between runs), Monitor (live pipeline progress).

---

## Tech stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.11+, [uv](https://docs.astral.sh/uv/), FastAPI + Uvicorn, SQLModel (SQLite), Typer CLI, pydantic-settings, pytest |
| Frontend | Vite + React + TypeScript, Tailwind CSS v4 |
| Intelligence | Ollama — `qwen2.5:7b` (insights), `nomic-embed-text` (embeddings) |
| Sources | Hacker News (Firebase API), GitHub (Search API), RSS |

---

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python 3.11+ automatically)
- Node.js 20+
- [Ollama](https://ollama.com/) running, with two models pulled:

```bash
ollama pull qwen2.5:7b        # insights: summary, why_text, difficulty
ollama pull nomic-embed-text  # embeddings: clustering and topics
```

> Without Ollama the radar still runs in degraded mode: heuristic descriptions
> and no clustering (each signal stays its own idea).

### Backend

```bash
cd backend
cp .env.example .env          # first time only — add your free GITHUB_TOKEN
uv run uvicorn app.api:app --reload
```

API on `http://localhost:8000` — health check: `curl http://localhost:8000/health`.
If port 8000 is taken, use `--port 8001` and start the frontend with
`BACKEND_URL=http://localhost:8001 npm run dev`.

### Frontend

```bash
cd frontend
npm install                   # first time only
npm run dev
```

App on `http://localhost:5173` (Vite proxies to the backend, no CORS in dev).

### CLI

```bash
cd backend
uv run idea-radar run         # collect + embed + cluster + score
uv run idea-radar ideas       # top ideas (--proposed for above-threshold only)
uv run idea-radar topics      # ideas grouped by theme
uv run idea-radar trends      # what's rising and falling between runs
uv run idea-radar stats       # ingestion funnel
uv run pytest                 # tests
```

### Scheduled runs (macOS)

```bash
cd backend
uv run idea-radar schedule install    # register the launchd agent
uv run idea-radar schedule status     # loaded? last exit code? recent runs
uv run idea-radar schedule uninstall  # remove it
```

The agent is deliberately dumb: it fires `idea-radar run --scheduled` at login
and every 30 minutes, and **all the policy lives in the CLI**, where it is
tested. A real run only starts when the last completed run is older than
`scheduling.min_interval_hours` (config.yaml, default 4); every other tick is a
~1s skip, logged with its reason to `backend/data/logs/scheduled.log`. On a
laptop this behaves like anacron: ticks missed while asleep are coalesced on
wake, `RunAtLoad` covers reboots, and changing the cadence in config.yaml
requires no reinstall (re-run `schedule install` only if you move the repo or
uv). Exit codes are meaningful — 0 ok/skip, 1 failed run, 3 Ollama not ready —
and `schedule status` translates them.

Unattended runs are stricter than manual ones, on purpose:

- If Ollama is down or a model is missing, the run is **skipped** (and retried
  at the next tick) instead of running degraded: items ingested without
  embeddings become permanent singleton ideas (`scheduling.require_ollama`).
- A cross-process file lock (`data/.run.lock`) guarantees a scheduled run, a
  manual run and the API never write to SQLite at the same time.
- LaunchAgents only run while you are logged in; with the Mac off, nothing
  runs. RSS and GitHub mostly self-heal after a gap — what is lost is the HN
  front page of those hours.

---

## Configuration

Runtime behaviour lives in [`backend/config.yaml`](backend/config.yaml) — sources,
keywords, scoring weights and thresholds, clustering thresholds. Secrets live in
`backend/.env` (never committed):

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | Free GitHub token for the Search API |
| `OLLAMA_HOST` | Ollama endpoint (default `http://localhost:11434`) |
| `OLLAMA_MODEL` | Insight model (default `qwen2.5:7b`) |
| `EMBEDDING_MODEL` | Embedding model (default `nomic-embed-text`) |

Two knobs worth knowing: `scoring.threshold` controls how selective the radar is,
and `clustering.idea_threshold` controls how aggressively duplicate signals merge
(higher = only near-identical items collapse).

---

## Project structure

```
backend/
  app/
    api.py         # FastAPI endpoints
    cli.py         # Typer CLI (entry point: `uv run idea-radar`)
    pipeline.py    # run orchestration
    sources/       # collectors: hackernews, github, rss
    embeddings.py  # local embeddings + similarity
    clustering.py  # items → ideas, ideas → topics
    scoring.py     # metrics and composite
    llm.py         # insights via Ollama
    scheduling.py  # unattended-run policy: staleness gate + Ollama preflight
    schedule_launchd.py  # launchd agent: install / uninstall / status
    runlock.py     # cross-process run lock (CLI, API, scheduler)
    queries.py     # shared reads for API/CLI
    models.py      # SQLModel
  config.yaml      # sources, keywords, scoring, clustering, scheduling
  tests/
frontend/
  src/
    views/         # Radar, Topic, Trend, Monitor
    components/     # cards, detail, UI primitives
```

---

## Privacy & data

This repository contains **code only**. The database with collected data stays
**local** and is never committed: `.env`, `*.db`, and `data/` are excluded via
`.gitignore`. Only `.env.example` (no secrets) is versioned.

---

## Roadmap

Recently shipped:

- [x] Semantic deduplication working end-to-end — embeddings use the `clustering:`
      task prefix and `idea_threshold` is tuned (114 raw items collapse to ~36 ideas).
- [x] Per-idea insight cache — repeat runs only pay the LLM for genuinely new content.
- [x] Fit-gate — clearly off-topic items skip the LLM entirely.
- [x] Consistent idea status — an idea's status/summary come from its best-scoring item.
- [x] `recluster` command — re-groups ideas into topics from cached embeddings in
      seconds, for fast `topic_threshold` tuning without a full run.
- [x] Scheduled runs — dumb launchd agent + smart CLI gate (`run --scheduled`):
      staleness check, Ollama preflight, cross-process lock, SQLite in WAL.
      Trends now accumulate on their own (`idea-radar schedule install`).
- [x] Engagement history — every run snapshots per-item engagement into
      `item_stats`: the raw material for delta-based heat.
- [x] Sweep-based topic tuning — `recluster --sweep 0.62,0.68,0.74` previews
      thresholds with zero writes; `--threshold` applies one on the fly.
- [x] Idea lifecycle — ideas with no fresh signals for 14 days are archived at
      the end of each run, and revived automatically when a new item lands.
- [x] HN backfill source (Algolia) — fixed 48h time-window queries heal the
      gaps left while the Mac was off, and repeated passes over the same
      stories feed `item_stats` with the observations delta-heat will need.

Next:

- [ ] Delta-based heat — replace engagement/age with velocity measured between
      consecutive `item_stats` observations.
- [ ] Configurable, smaller insight model for faster runs on modest hardware.
- [ ] More source connectors behind the same interface.

---

## License

Released under the MIT License — see [LICENSE](LICENSE).
