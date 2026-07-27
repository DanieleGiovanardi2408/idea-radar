"""`recluster_topics` ri-raggruppa le idee in topic dagli embedding già salvati,
con la nuova `topic_threshold`, senza rifare fetch/embedding/insight."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Idea, Item, Topic
from app.pipeline import recluster_topics, run_pipeline
from fakes import EmbedManyMixin


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


class FakeEmbedder(EmbedManyMixin):
    """Due vettori a coseno 0.8: stanno insieme sotto soglia 0.5, separati sotto 0.95."""

    unavailable = False

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "uno" in text.lower() else [0.8, 0.6]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _config(topic_threshold: float) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(
            idea_threshold=0.99, topic_threshold=topic_threshold, llm_topic_labels=False
        ),
    )


def test_recluster_retunes_topics_without_a_full_run(session: Session) -> None:
    items = [
        Item(source="hn", external_id="1", title="ai uno"),
        Item(source="hn", external_id="2", title="ai due"),
    ]
    # Run iniziale con topic_threshold permissiva: le due idee finiscono in UN topic.
    run_pipeline(
        session, _config(topic_threshold=0.5), Settings(),
        sources=[FakeSource(items)], ollama=FakeOllama(), embedder=FakeEmbedder(),
    )
    assert len(session.exec(select(Idea)).all()) == 2
    assert len(session.exec(select(Topic)).all()) == 1

    # Recluster con soglia più severa: stessi embedding, ma ora la coppia non si
    # tiene più — e due idee sole non fanno due temi, restano NON RAGGRUPPATE.
    # Prima qui nascevano due topic da un membro ciascuno: è il meccanismo che
    # sull'archivio vero ha prodotto 784 topic finti su 1002.
    result = recluster_topics(
        session, _config(topic_threshold=0.95), Settings(), ollama=FakeOllama()
    )
    assert result["n_ideas"] == 2          # le idee non vengono toccate
    assert result["n_topics"] == 0
    assert session.exec(select(Topic)).all() == []
    assert all(i.topic_id is None for i in session.exec(select(Idea)).all())


def test_recluster_is_idempotent_on_same_threshold(session: Session) -> None:
    items = [
        Item(source="hn", external_id="1", title="ai uno"),
        Item(source="hn", external_id="2", title="ai due"),
    ]
    run_pipeline(
        session, _config(topic_threshold=0.95), Settings(),
        sources=[FakeSource(items)], ollama=FakeOllama(), embedder=FakeEmbedder(),
    )
    before = len(session.exec(select(Topic)).all())
    result = recluster_topics(
        session, _config(topic_threshold=0.95), Settings(), ollama=FakeOllama()
    )
    assert result["n_topics"] == before    # stessa soglia => stesso numero di topic


def test_threshold_override_beats_config(session: Session) -> None:
    """`recluster --threshold X` deve vincere sulla soglia di config.yaml."""
    items = [
        Item(source="hn", external_id="1", title="ai uno"),
        Item(source="hn", external_id="2", title="ai due"),
    ]
    run_pipeline(
        session, _config(topic_threshold=0.5), Settings(),
        sources=[FakeSource(items)], ollama=FakeOllama(), embedder=FakeEmbedder(),
    )
    assert len(session.exec(select(Topic)).all()) == 1

    result = recluster_topics(
        session,
        _config(topic_threshold=0.5),  # config direbbe "insieme"…
        Settings(),
        ollama=FakeOllama(),
        topic_threshold=0.95,          # …ma l'override li separa
    )
    # Separati significa senza tema: nessuno dei due ha più compagni.
    assert result["n_topics"] == 0
    assert all(i.topic_id is None for i in session.exec(select(Idea)).all())


def test_cli_recluster_sweep_prints_preview(monkeypatch) -> None:
    """`recluster --sweep` stampa l'anteprima e non tocca il recluster vero."""
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from app import cli

    @contextmanager
    def _fake_session():
        yield None

    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "get_session", _fake_session)
    monkeypatch.setattr(
        cli,
        "sweep_topic_thresholds",
        lambda session, values: [
            {
                "threshold": v,
                "n_topics": 5,
                "max_size": 4,
                "n_singleton": 2,
                "biggest_sample": ["alpha", "beta"],
            }
            for v in values
        ],
    )

    def _no_recluster(threshold_override=None):
        raise AssertionError("la sweep non deve eseguire il recluster vero")

    monkeypatch.setattr(cli, "execute_recluster", _no_recluster)

    result = CliRunner().invoke(cli.app, ["recluster", "--sweep", "0.62,0.7"])
    assert result.exit_code == 0
    assert "0.62" in result.output and "0.70" in result.output
    assert "alpha" in result.output
    assert "senza scritture" in result.output


def test_cli_recluster_sweep_rejects_garbage(monkeypatch) -> None:
    from typer.testing import CliRunner

    from app import cli

    result = CliRunner().invoke(cli.app, ["recluster", "--sweep", "abc"])
    assert result.exit_code == 2
    assert "non valido" in result.output
