"""API FastAPI di Idea Radar."""

from fastapi import FastAPI

app = FastAPI(title="Idea Radar API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
