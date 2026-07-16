"""Verifica che ``run_pipeline`` invochi il callback di avanzamento."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Item
from app.pipeline import run_pipeline


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(summary=f"s {item.title}", why_text="w", difficulty=None)

    def topic_label(self, labels: list[str]) -> str:
        return "topic"


class FakeEmbedder:
    unavailable = False

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "ai" in text.lower() else [0.0, 1.0]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.4),
        clustering=ClusteringConfig(idea_threshold=0.99, topic_threshold=0.8),
    )


def test_on_progress_is_called_per_item(session: Session) -> None:
    seen: list[str] = []
    items = [
        Item(source="hn", external_id="1", title="ai tool"),
        Item(source="github", external_id="2", title="repo"),
    ]
    run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
        on_progress=seen.append,
    )
    # un messaggio di insight per ciascuno dei due item + la fase finale dei topic
    assert "insight 1/2" in seen
    assert "insight 2/2" in seen
    assert any("topic" in m for m in seen)


def test_on_progress_is_optional(session: Session) -> None:
    # Senza callback la pipeline gira lo stesso (retro-compatibilità API).
    run = run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource([Item(source="hn", external_id="1", title="ai tool")])],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )
    assert run.n_items == 1
