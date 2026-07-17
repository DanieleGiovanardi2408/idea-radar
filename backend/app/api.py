"""API FastAPI di Idea Radar."""

import logging
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session, init_db
from app.models import Idea, IdeaStatus, Run
from app.pipeline import execute_run
from app.queries import (
    idea_history,
    latest_scores,
    monitor_stats,
    topic_trends,
    topics_overview,
)
from app.runlock import RunLockBusy, run_lock_busy

logger = logging.getLogger(__name__)

# Un run alla volta: la pipeline scrive su SQLite e satura Ollama.
_run_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Idea Radar API", version="0.2.0", lifespan=lifespan)


def get_db() -> Iterator[Session]:
    with get_session() as session:
        yield session


# ---- Response models -------------------------------------------------------


class ItemOut(BaseModel):
    source: str
    title: str
    url: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    engagement: dict | None = None


class IdeaOut(BaseModel):
    id: int
    label: str
    summary: str | None = None
    status: str
    topic_id: int | None = None
    topic_label: str | None = None
    composite: float
    heat: float | None = None
    credibility: float | None = None
    feasibility: float | None = None
    opportunity: float | None = None
    fit: float | None = None
    why_text: str | None = None
    difficulty: str | None = None
    n_items: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    items: list[ItemOut] = []


class ScorePoint(BaseModel):
    run_id: int
    composite: float
    heat: float
    credibility: float
    feasibility: float
    opportunity: float
    fit: float


class IdeaDetailOut(IdeaOut):
    history: list[ScorePoint] = []


class TopicOut(BaseModel):
    id: int
    label: str
    n_ideas: int
    n_items: int
    n_proposed: int
    avg_composite: float
    top_composite: float
    first_seen: datetime
    last_seen: datetime


class TrendPoint(BaseModel):
    run_id: int
    started_at: datetime
    n_ideas: int
    n_items: int
    avg_composite: float


class TrendOut(BaseModel):
    topic_id: int
    label: str
    points: list[TrendPoint]
    n_ideas: int
    avg_composite: float
    delta_ideas: int
    delta_composite: float


class RunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    phase: str
    n_items: int
    n_items_fetched: int
    n_items_new: int
    n_ideas_processed: int
    n_ideas_proposed: int
    n_ideas_total: int
    n_topics: int
    error: str | None = None
    sources: dict | None = None


class StatsOut(BaseModel):
    n_items: int
    n_ideas: int
    n_topics: int
    n_proposed: int
    n_archived: int = 0
    n_runs: int
    items_by_source: dict[str, int]
    last_run: RunOut | None = None
    recent_runs: list[RunOut] = []


class RunStarted(BaseModel):
    run_id: int | None = None
    started: bool
    detail: str


# ---- Serializzazione -------------------------------------------------------


def _run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status.value,
        phase=run.phase,
        n_items=run.n_items,
        n_items_fetched=run.n_items_fetched,
        n_items_new=run.n_items_new,
        n_ideas_processed=run.n_ideas_processed,
        n_ideas_proposed=run.n_ideas_proposed,
        n_ideas_total=run.n_ideas_total,
        n_topics=run.n_topics,
        error=run.error,
        sources=run.sources_json,
    )


def _idea_out(idea: Idea, score, model=IdeaOut):
    return model(
        id=idea.id,
        label=idea.label,
        summary=idea.summary,
        status=idea.status.value,
        topic_id=idea.topic_id,
        topic_label=idea.topic.label if idea.topic else None,
        composite=score.composite if score else 0.0,
        heat=score.heat if score else None,
        credibility=score.credibility if score else None,
        feasibility=score.feasibility if score else None,
        opportunity=score.opportunity if score else None,
        fit=score.fit if score else None,
        why_text=score.why_text if score else None,
        difficulty=(score.difficulty.value if score and score.difficulty else None),
        n_items=len(idea.items),
        first_seen=idea.first_seen,
        last_seen=idea.last_seen,
        items=[
            ItemOut(
                source=it.source,
                title=it.title,
                url=it.url,
                author=it.author,
                created_at=it.created_at,
                engagement=it.engagement_json,
            )
            for it in idea.items
        ],
    )


# ---- Endpoints -------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ideas", response_model=list[IdeaOut])
def list_ideas(
    session: Session = Depends(get_db),
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[IdeaOut]:
    latest = latest_scores(session)
    rows = [
        _idea_out(idea, latest.get(idea.id))
        for idea in session.exec(select(Idea)).all()
        # Default: solo il vivo. Le archiviate si chiedono con ?status=archived.
        if (
            idea.status != IdeaStatus.ARCHIVED
            if status is None
            else idea.status == status
        )
        and (topic_id is None or idea.topic_id == topic_id)
    ]
    rows.sort(key=lambda r: r.composite, reverse=True)
    return rows[:limit]


@app.get("/ideas/{idea_id}", response_model=IdeaDetailOut)
def get_idea(idea_id: int, session: Session = Depends(get_db)) -> IdeaDetailOut:
    idea = session.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea non trovata")
    latest = latest_scores(session)
    detail = _idea_out(idea, latest.get(idea.id), model=IdeaDetailOut)
    detail.history = [
        ScorePoint(
            run_id=s.run_id,
            composite=s.composite,
            heat=s.heat,
            credibility=s.credibility,
            feasibility=s.feasibility,
            opportunity=s.opportunity,
            fit=s.fit,
        )
        for s in idea_history(session, idea_id)
    ]
    return detail


@app.get("/topics", response_model=list[TopicOut])
def list_topics(session: Session = Depends(get_db)) -> list[TopicOut]:
    return [TopicOut(**t) for t in topics_overview(session)]


@app.get("/trends", response_model=list[TrendOut])
def list_trends(
    session: Session = Depends(get_db),
    max_runs: int = Query(default=12, ge=2, le=50),
) -> list[TrendOut]:
    return [TrendOut(**t) for t in topic_trends(session, max_runs=max_runs)]


@app.get("/stats", response_model=StatsOut)
def get_stats(session: Session = Depends(get_db)) -> StatsOut:
    data = monitor_stats(session)
    return StatsOut(
        n_items=data["n_items"],
        n_ideas=data["n_ideas"],
        n_topics=data["n_topics"],
        n_proposed=data["n_proposed"],
        n_archived=data["n_archived"],
        n_runs=data["n_runs"],
        items_by_source=data["items_by_source"],
        last_run=_run_out(data["last_run"]) if data["last_run"] else None,
        recent_runs=[_run_out(r) for r in data["recent_runs"]],
    )


@app.get("/runs", response_model=list[RunOut])
def list_runs(
    session: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[RunOut]:
    runs = session.exec(select(Run)).all()
    runs.sort(key=lambda r: r.id or 0, reverse=True)
    return [_run_out(r) for r in runs[:limit]]


@app.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: Session = Depends(get_db)) -> RunOut:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run non trovato")
    return _run_out(run)


def _background_run() -> None:
    """Esegue la pipeline fuori dal ciclo di richiesta, con lock anti-doppioni."""
    if not _run_lock.acquire(blocking=False):
        logger.info("Run già in corso: ignoro la richiesta.")
        return
    try:
        execute_run()
    except RunLockBusy:
        # Il lock su file è cross-process: qui l'ha preso la CLI o lo
        # scheduler. Non è un errore, solo un doppione evitato.
        logger.info("Run già in corso in un altro processo: ignoro la richiesta.")
    except Exception:
        logger.exception("Run in background fallito")
    finally:
        _run_lock.release()


@app.post("/runs", response_model=RunStarted, status_code=202)
def trigger_run(
    background_tasks: BackgroundTasks, session: Session = Depends(get_db)
) -> RunStarted:
    """Avvia un run in background e torna subito: il progresso si legge da /runs."""
    if _run_lock.locked() or run_lock_busy():
        return RunStarted(started=False, detail="Un run è già in corso.")
    background_tasks.add_task(_background_run)
    return RunStarted(started=True, detail="Run avviato.")
