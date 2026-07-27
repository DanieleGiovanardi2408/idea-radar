"""API FastAPI di Idea Radar."""

import logging
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.appconfig import get_config
from app.db import get_session, init_db
from app.models import Idea, IdeaStatus, Run, utcnow
from app.pipeline import execute_run
from app.queries import (
    idea_history,
    ideas_per_profile,
    latest_score_for,
    monitor_stats,
    top_ideas,
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
    # Profilo (macro-tema) su cui il fit è stato misurato.
    profile: str | None = None
    why_text: str | None = None
    difficulty: str | None = None
    n_items: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # Stato utente (azioni manuali: pin, dismiss, visto, nota).
    pinned: bool = False
    dismissed_at: datetime | None = None
    seen_at: datetime | None = None
    note: str | None = None
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
    # Macro-tema: il profilo della maggioranza delle idee del topic.
    profile: str | None = None
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


class IdeaUpdate(BaseModel):
    """Azioni utente su un'idea. I campi assenti non vengono toccati."""

    pinned: bool | None = None
    dismissed: bool | None = None  # True scarta, False ripristina
    seen: bool | None = None  # True marca come vista adesso
    note: str | None = None  # una stringa imposta la nota, null la cancella


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
        profile=score.profile if score else None,
        why_text=score.why_text if score else None,
        difficulty=(score.difficulty.value if score and score.difficulty else None),
        n_items=len(idea.items),
        first_seen=idea.first_seen,
        last_seen=idea.last_seen,
        pinned=idea.pinned,
        dismissed_at=idea.dismissed_at,
        seen_at=idea.seen_at,
        note=idea.note,
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
    offset: int = Query(default=0, ge=0),
    include_dismissed: bool = False,
    profile: str | None = None,
) -> list[IdeaOut]:
    """Idee ordinate (pinnate prima, poi composite): filtri e paginazione in SQL.

    Default: solo il vivo. Le archiviate si chiedono con ``?status=archived``,
    le scartate a mano con ``?include_dismissed=true``, e ``?profile=<nome>``
    mostra il radar dal punto di vista di un tema solo.
    """
    rows = top_ideas(
        session,
        limit=limit,
        status=status,
        topic_id=topic_id,
        offset=offset,
        include_dismissed=include_dismissed,
        profile=profile,
    )
    return [_idea_out(idea, score) for idea, score in rows]


@app.get("/ideas/{idea_id}", response_model=IdeaDetailOut)
def get_idea(idea_id: int, session: Session = Depends(get_db)) -> IdeaDetailOut:
    idea = session.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea non trovata")
    detail = _idea_out(idea, latest_score_for(session, idea_id), model=IdeaDetailOut)
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


@app.patch("/ideas/{idea_id}", response_model=IdeaOut)
def update_idea(
    idea_id: int, payload: IdeaUpdate, session: Session = Depends(get_db)
) -> IdeaOut:
    """Azioni utente su un'idea: pin, dismiss, visto, nota.

    Stato UTENTE, ortogonale allo ``status`` della pipeline: i run non lo
    toccano mai. ``dismissed: true`` mette il timestamp (e l'idea esce dalle
    viste), ``false`` lo azzera; ``seen: true`` marca la visita adesso;
    ``note`` con stringa imposta l'appunto, con ``null`` esplicito lo cancella.
    """
    idea = session.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea non trovata")

    provided = payload.model_fields_set
    if payload.pinned is not None:
        idea.pinned = payload.pinned
    if payload.dismissed is not None:
        idea.dismissed_at = utcnow() if payload.dismissed else None
    if payload.seen:
        idea.seen_at = utcnow()
    if "note" in provided:  # distingue "assente" (non toccare) da null (cancella)
        idea.note = payload.note

    session.add(idea)
    session.commit()
    session.refresh(idea)
    return _idea_out(idea, latest_score_for(session, idea_id))


class ProfileOut(BaseModel):
    name: str
    label: str
    keywords: list[str]
    n_ideas: int = 0


@app.get("/profiles", response_model=list[ProfileOut])
def list_profiles(session: Session = Depends(get_db)) -> list[ProfileOut]:
    """I temi configurati, col numero di idee vive che ciascuno rappresenta.

    Serve al frontend per il selettore: i profili vivono in config.yaml, quindi
    l'unica fonte di verità è il backend.
    """
    counts = ideas_per_profile(session)
    return [
        ProfileOut(
            name=p.name,
            label=p.title,
            keywords=p.keywords,
            n_ideas=counts.get(p.name, 0),
        )
        for p in get_config().effective_profiles()
    ]


@app.get("/topics", response_model=list[TopicOut])
def list_topics(
    session: Session = Depends(get_db),
    min_ideas: int = Query(default=1, ge=1, le=100),
    order_by: str = Query(default="top_composite", pattern="^[a-z_]+$"),
) -> list[TopicOut]:
    """Topic vivi. ``min_ideas=2`` scarta i temi da una sola idea."""
    return [
        TopicOut(**t)
        for t in topics_overview(session, min_ideas=min_ideas, order_by=order_by)
    ]


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
