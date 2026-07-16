from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Idea, Item, RunStatus, Score, Topic, TopicStat
from app.pipeline import run_pipeline


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    """Sostituisce OllamaClient nei test: nessuna rete."""

    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(
            summary=f"riassunto di {item.title}", why_text="perché sì", difficulty=None
        )

    def topic_label(self, labels: list[str]) -> str:
        return "topic di prova"


class FakeEmbedder:
    """Embedding deterministici: item con lo stesso prefisso finiscono vicini."""

    unavailable = False

    def embed(self, text: str) -> list[float]:
        # "ai" ovunque nel testo (dopo il prefisso "clustering:" degli embedding).
        return [1.0, 0.0] if "ai" in text.lower() else [0.0, 1.0]


class ExplodingSource:
    def fetch(self) -> list[Item]:
        raise RuntimeError("fonte down")


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _config(idea_threshold: float = 0.99) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 0.5, "opportunity": 0.5}, threshold=0.4),
        clustering=ClusteringConfig(
            idea_threshold=idea_threshold, topic_threshold=0.8, llm_topic_labels=True
        ),
    )


def _run(session: Session, items: list[Item], **kwargs):
    return run_pipeline(
        session,
        kwargs.pop("config", _config()),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
        **kwargs,
    )


def test_pipeline_creates_items_ideas_scores_and_topics(session: Session) -> None:
    items = [
        Item(source="hn", external_id="1", title="ai tool", engagement_json={"score": 100}),
        Item(source="github", external_id="2", title="repo", engagement_json={"stars": 500}),
    ]
    run = _run(session, items)

    assert run.status == RunStatus.DONE
    assert run.n_items == 2
    assert run.finished_at is not None
    assert len(session.exec(select(Idea)).all()) == 2
    assert len(session.exec(select(Score)).all()) == 2
    assert run.n_ideas_proposed + run.n_ideas_processed == 2
    assert session.exec(select(Topic)).all()  # topic creati
    assert session.exec(select(TopicStat)).all()  # fotografia per i trend


def test_pipeline_records_progress_and_source_stats(session: Session) -> None:
    run = _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert run.phase == "completato"
    assert run.n_items_fetched == 1
    assert run.n_items_new == 1
    assert run.sources_json == {"FakeSource": {"fetched": 1, "new": 1}}


def test_second_run_does_not_recount_existing_items(session: Session) -> None:
    items = [Item(source="hn", external_id="1", title="ai tool")]
    _run(session, items)
    second = _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert second.n_items_fetched == 1
    assert second.n_items_new == 0  # già visto
    assert len(session.exec(select(Idea)).all()) == 1


def test_similar_items_collapse_into_one_idea(session: Session) -> None:
    """Con soglia permissiva due segnali sullo stesso tema fanno UNA idea."""
    items = [
        Item(source="hn", external_id="1", title="ai agent per il codice"),
        Item(source="github", external_id="2", title="ai agent che scrive codice"),
    ]
    run = _run(session, items, config=_config(idea_threshold=0.8))

    ideas = session.exec(select(Idea)).all()
    assert len(ideas) == 1  # deduplicate
    assert len(ideas[0].items) == 2
    assert run.n_items == 2
    assert len(session.exec(select(Score)).all()) == 1  # uno score per idea per run


def test_score_per_run_is_kept_across_runs(session: Session) -> None:
    items = [Item(source="hn", external_id="1", title="ai tool")]
    _run(session, items)
    _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert len(session.exec(select(Score)).all()) == 2  # uno per run


def test_pipeline_survives_failing_source(session: Session) -> None:
    run = run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[ExplodingSource(), FakeSource([Item(source="hn", external_id="9", title="ok")])],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )
    assert run.status == RunStatus.DONE
    assert run.n_items == 1
    assert run.sources_json["ExplodingSource"]["error"]
