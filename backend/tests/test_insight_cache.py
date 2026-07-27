"""La cache degli insight: il 7B non va richiamato per idee già analizzate."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db, upsert_item
from app.llm import IdeaInsight
from app.models import Item, Score
from app.pipeline import run_pipeline
from fakes import EmbedManyMixin


class CountingOllama:
    """Conta quante volte viene generato un insight (= chiamate al 7B)."""

    def __init__(self) -> None:
        self.insight_calls = 0

    def insight(self, item: Item) -> IdeaInsight:
        self.insight_calls += 1
        return IdeaInsight(
            summary=f"riassunto {item.title}", why_text="perché", difficulty=None
        )

    def topic_label(self, labels: list[str]) -> str:
        return "topic"


class FakeEmbedder(EmbedManyMixin):
    unavailable = False

    def embed(self, text: str) -> list[float]:
        # Un asse per parola chiave: uno/due/tre restano distinti (niente fusioni).
        t = text.lower()
        if "uno" in t:
            return [1.0, 0.0, 0.0]
        if "due" in t:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    return eng


def _config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.4),
        clustering=ClusteringConfig(idea_threshold=0.99, topic_threshold=0.9),
    )


def _items() -> list[Item]:
    # "ai" nel titolo => fit > 0 => passano il fit-gate e arrivano davvero all'LLM.
    return [
        Item(source="hn", external_id="1", title="ai progetto uno"),
        Item(source="hn", external_id="2", title="ai progetto due"),
    ]


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


def test_second_run_does_not_recall_the_llm(engine) -> None:
    ollama = CountingOllama()
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items())], ollama=ollama, embedder=FakeEmbedder(),
        )
    assert ollama.insight_calls == 2  # primo run: due idee nuove, due generazioni

    # Secondo run con gli stessi contenuti: nessuna nuova generazione.
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items())], ollama=ollama, embedder=FakeEmbedder(),
        )
    assert ollama.insight_calls == 2  # invariato: cache colpita


def test_new_item_in_second_run_is_the_only_llm_call(engine) -> None:
    ollama = CountingOllama()
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items())], ollama=ollama, embedder=FakeEmbedder(),
        )
    assert ollama.insight_calls == 2

    # Terzo item nuovo al secondo run: solo lui paga il 7B.
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items() + [Item(source="hn", external_id="3", title="ai tre nuovo")])],
            ollama=ollama, embedder=FakeEmbedder(),
        )
    assert ollama.insight_calls == 3  # +1 solo per il nuovo


def test_off_topic_item_skips_the_llm(engine) -> None:
    """Fit-gate: un item senza match di keyword non deve spendere il 7B."""
    ollama = CountingOllama()
    items = [
        Item(source="hn", external_id="1", title="ai uno"),         # in tema ("ai")
        Item(source="hn", external_id="2", title="carbonara due"),  # fuori tema
    ]
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(items)], ollama=ollama, embedder=FakeEmbedder(),
        )
    assert ollama.insight_calls == 1  # solo l'item in tema chiama il modello


def test_score_is_still_recomputed_each_run(engine) -> None:
    """La cache tocca solo l'insight testuale: gli score si rifanno ogni run."""
    ollama = CountingOllama()
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items())], ollama=ollama, embedder=FakeEmbedder(),
        )
    with Session(engine) as s:
        run_pipeline(
            s, _config(), Settings(),
            sources=[FakeSource(_items())], ollama=ollama, embedder=FakeEmbedder(),
        )
        # due run x due idee = quattro righe di score (una per idea per run)
        assert len(s.exec(select(Score)).all()) == 4
