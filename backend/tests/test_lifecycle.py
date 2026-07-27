"""Ciclo di vita: idee senza segnali → archivio, e ritorno gratis.

L'archiviazione gira in coda a ogni run; la de-archiviazione non è un caso
speciale ma il normale flusso della pipeline: un item nuovo aggiorna
``last_seen`` e il ri-scoring riporta lo status a processed/proposed.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, LifecycleConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.lifecycle import archive_stale_ideas
from app.llm import IdeaInsight
from app.models import Idea, IdeaStatus, Item, utcnow
from app.pipeline import run_pipeline
from app.queries import top_ideas
from fakes import EmbedManyMixin


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(summary="s", why_text="w", difficulty=None)

    def topic_label(self, labels: list[str]) -> str:
        return "topic"


class FakeEmbedder(EmbedManyMixin):
    unavailable = False

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _idea(
    session: Session,
    label: str,
    days_ago: float,
    status: IdeaStatus = IdeaStatus.PROCESSED,
) -> Idea:
    idea = Idea(
        label=label, status=status, last_seen=utcnow() - timedelta(days=days_ago)
    )
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


def _config(archive_after_days: float = 14.0) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.4),
        clustering=ClusteringConfig(idea_threshold=0.99, topic_threshold=0.8),
        lifecycle=LifecycleConfig(archive_after_days=archive_after_days),
    )


def test_archives_only_stale_ideas(session: Session) -> None:
    old = _idea(session, "spenta", days_ago=20)
    fresh = _idea(session, "viva", days_ago=2)

    count = archive_stale_ideas(session, older_than_days=14)

    assert count == 1
    assert session.get(Idea, old.id).status == IdeaStatus.ARCHIVED
    assert session.get(Idea, fresh.id).status == IdeaStatus.PROCESSED


def test_zero_days_disables_lifecycle(session: Session) -> None:
    _idea(session, "vecchissima", days_ago=400)
    assert archive_stale_ideas(session, older_than_days=0) == 0


def test_archived_ideas_leave_default_views(session: Session) -> None:
    _idea(session, "spenta", days_ago=20, status=IdeaStatus.ARCHIVED)
    _idea(session, "viva", days_ago=1)

    rows = top_ideas(session)
    assert [idea.label for idea, _ in rows] == ["viva"]
    archived = top_ideas(session, status=IdeaStatus.ARCHIVED)
    assert [idea.label for idea, _ in archived] == ["spenta"]


def test_run_pipeline_archives_at_the_end(session: Session) -> None:
    _idea(session, "spenta", days_ago=30)
    run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource([Item(source="hn", external_id="n", title="ai fresh")])],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )
    stale = session.exec(select(Idea).where(Idea.label == "spenta")).one()
    assert stale.status == IdeaStatus.ARCHIVED


def test_new_item_revives_archived_idea(session: Session) -> None:
    items = [Item(source="hn", external_id="1", title="ai tool")]
    run_pipeline(
        session, _config(), Settings(),
        sources=[FakeSource(items)], ollama=FakeOllama(), embedder=FakeEmbedder(),
    )
    idea = session.exec(select(Idea)).one()
    idea.status = IdeaStatus.ARCHIVED
    idea.last_seen = utcnow() - timedelta(days=30)
    session.add(idea)
    session.commit()

    run_pipeline(
        session, _config(), Settings(),
        sources=[FakeSource([Item(source="hn", external_id="1", title="ai tool")])],
        ollama=FakeOllama(), embedder=FakeEmbedder(),
    )

    revived = session.exec(select(Idea)).one()
    assert revived.status != IdeaStatus.ARCHIVED  # il segnale nuovo la rianima
    assert (utcnow() - revived.last_seen).total_seconds() < 3600
