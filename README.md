# Idea Radar

Monorepo per la raccolta e lo scoring di idee. Backend Python (FastAPI) + frontend React (Vite + TypeScript + Tailwind).

## Requisiti

- [uv](https://docs.astral.sh/uv/) (Python 3.11+ gestito automaticamente)
- Node.js 20+

## Avvio

### Backend

```bash
cd backend
cp .env.example .env   # solo la prima volta
uv run uvicorn app.api:app --reload
```

API su http://localhost:8000 — health check: `curl http://localhost:8000/health`

Se la porta 8000 è occupata, usa `--port 8001` e avvia il frontend con `BACKEND_URL=http://localhost:8001 npm run dev`.

### Frontend

```bash
cd frontend
npm install            # solo la prima volta
npm run dev
```

App su http://localhost:5173 — la pagina mostra lo stato di `/health` del backend (proxy Vite, niente CORS in dev).

### Test e CLI

```bash
cd backend
uv run pytest          # test
uv run idea-radar      # CLI (placeholder)
```

## Privacy e dati

Questo repository contiene **solo codice**. Il database con i dati raccolti (idee, segnali dalle fonti configurate) resta **in locale** e non viene mai committato: `.env`, `*.db` e `data/` sono esclusi via `.gitignore`. Nel repo è versionato solo `.env.example` come template di configurazione, senza segreti.

## Struttura

```
backend/    FastAPI + Typer CLI (gestito con uv)
  app/      codice applicativo (api, cli, config)
  tests/    pytest
frontend/   Vite + React + TS + Tailwind CSS v4
```
