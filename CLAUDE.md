# Idea Radar — Convenzioni di progetto

## Stack

- **Backend** (`backend/`): Python 3.11+, gestito con **uv**. FastAPI + Uvicorn per l'API, SQLModel per la persistenza, Typer per la CLI, pydantic-settings per la configurazione (`.env`), pytest per i test.
- **Frontend** (`frontend/`): Vite + React + TypeScript, Tailwind CSS v4 (plugin `@tailwindcss/vite`, niente `tailwind.config.js`).

## Vincoli di costo — IMPORTANTE

- **Solo API gratuite**: nessun servizio a pagamento (GitHub API con token gratuito, Hacker News, ecc.).
- **LLM solo locale via Ollama**: nessuna chiamata a LLM cloud a pagamento. Host e modello configurati via `OLLAMA_HOST` / `OLLAMA_MODEL`.

## Struttura cartelle

```
backend/
  app/
    api.py      # app FastAPI (endpoint)
    cli.py      # CLI Typer (entry point: `uv run idea-radar`)
    config.py   # Settings pydantic-settings, legge .env
  tests/        # pytest
  config.yaml   # sources, keywords, scoring (weights + threshold)
  .env.example  # GITHUB_TOKEN, OLLAMA_HOST, OLLAMA_MODEL
frontend/
  src/          # React + TS; stili solo con classi Tailwind
```

## Convenzioni

- Comandi backend sempre via `uv run …` (mai attivare venv a mano); nuove dipendenze con `uv add`.
- La configurazione runtime va in `app/config.py` (env) o `config.yaml` (comportamento); niente valori hardcoded.
- In dev il frontend raggiunge il backend tramite il proxy Vite (`/health` → `localhost:8000`, override con `BACKEND_URL`); non introdurre CORS finché non serve.
- Ogni endpoint nuovo ha un test in `backend/tests/`.
