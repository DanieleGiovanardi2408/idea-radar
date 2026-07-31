"""La storia dell'engagement si accumula per (item, run).

``upsert_item`` sovrascrive ``engagement_json`` sull'item a ogni re-fetch:
``ItemStat`` è ciò che ne conserva la serie nel tempo — la materia prima
della futura heat "a delta" (velocità tra osservazioni consecutive).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import EmbedManyMixin, FakeOllama
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.models import Item, ItemStat
from app.pipeline import run_pipeline


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


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


def _config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.4),
        clustering=ClusteringConfig(idea_threshold=0.99, topic_threshold=0.8),
    )


def _run(session: Session, items: list[Item]):
    return run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )


def test_each_run_records_engagement_observation(session: Session) -> None:
    _run(
        session,
        [
            Item(
                source="hn",
                external_id="1",
                title="ai tool",
                engagement_json={"score": 100, "comments": 20},
            )
        ],
    )
    stat = session.exec(select(ItemStat)).one()
    assert stat.engagement == 120.0  # riduzione hn: score + comments
    assert stat.engagement_json == {"score": 100, "comments": 20}
    assert stat.observed_at is not None


def test_refetch_appends_history_instead_of_overwriting(session: Session) -> None:
    _run(
        session,
        [Item(source="hn", external_id="1", title="ai tool",
              engagement_json={"score": 100, "comments": 0})],
    )
    _run(
        session,
        [Item(source="hn", external_id="1", title="ai tool",
              engagement_json={"score": 250, "comments": 10})],
    )

    item = session.exec(select(Item)).one()
    stats = sorted(session.exec(select(ItemStat)).all(), key=lambda s: s.run_id)
    assert [s.item_id for s in stats] == [item.id, item.id]
    assert [s.engagement for s in stats] == [100.0, 260.0]  # la storia esiste
    # …mentre l'item, da solo, conserva soltanto l'ultimo valore.
    assert item.engagement_json == {"score": 250, "comments": 10}


def test_duplicate_item_in_same_fetch_records_one_observation(session: Session) -> None:
    duplicate = dict(
        source="hn",
        external_id="1",
        title="ai tool",
        engagement_json={"score": 10, "comments": 0},
    )
    run = _run(session, [Item(**duplicate), Item(**duplicate)])
    stats = session.exec(select(ItemStat)).all()
    assert len(stats) == 1  # una sola osservazione per (item, run)
    assert stats[0].run_id == run.id
