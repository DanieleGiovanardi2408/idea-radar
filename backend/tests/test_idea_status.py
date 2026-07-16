"""Regressione: con la dedup attiva, la faccia dell'idea (status/summary/score)
deve venire dall'item MIGLIORE del run, non dall'ultimo processato."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Idea, IdeaStatus, Item
from app.pipeline import run_pipeline


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(summary=f"riassunto di {item.title}", why_text="w", difficulty=None)

    def topic_label(self, labels: list[str]) -> str:
        return "topic"


class ConstEmbedder:
    """Stesso vettore per tutti: due item finiscono nella STESSA idea."""

    unavailable = False

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_idea_status_reflects_best_item_not_last(session: Session) -> None:
    cfg = AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(idea_threshold=0.5, topic_threshold=0.9),
    )
    # "strong" ha molto engagement (composite alto → proposed), "weak" no
    # (composite basso → processed). Vengono processati in quest'ordine e
    # fusi nella stessa idea. Prima del fix, "weak" (ultimo) sovrascriveva lo
    # status a "processed" pur avendo l'idea un punteggio 'proposed'.
    strong = Item(
        source="hn", external_id="1", title="ai alpha",
        author="x", engagement_json={"score": 1000, "comments": 1000},
    )
    weak = Item(
        source="hn", external_id="2", title="ai beta",
        engagement_json={"score": 0, "comments": 0},
    )

    run = run_pipeline(
        session, cfg, Settings(),
        sources=[FakeSource([strong, weak])],
        ollama=FakeOllama(), embedder=ConstEmbedder(),
    )

    ideas = session.exec(select(Idea)).all()
    assert len(ideas) == 1                         # i due item si fondono
    assert ideas[0].status == IdeaStatus.PROPOSED  # dal migliore, non dal debole
    assert run.n_ideas_proposed == 1               # contatore coerente col composite
    assert run.n_ideas_processed == 0
