"""`heal` recupera i sedimenti che il clustering incrementale non rivaluta:
item entrati senza embedding (Ollama giù) e singleton che oggi avrebbero un posto.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select
from typer.testing import CliRunner

from app import cli
from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.clustering import attach_item_to_idea
from app.config import Settings
from app.db import init_db, upsert_item
from app.healing import (
    heal_ideas,
    ideas_to_reinsight,
    items_without_embedding,
    regenerate_insights,
    singleton_ideas,
)
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, utcnow


class FakeEmbedder:
    """Restituisce il vettore che l'item avrebbe avuto se Ollama fosse stato su."""

    unavailable = False

    def __init__(self, by_title: dict[str, list[float]]) -> None:
        self._by_title = by_title
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        for title, vector in self._by_title.items():
            if title in text:
                return vector
        raise AssertionError(f"testo inatteso: {text!r}")


class DeadEmbedder:
    unavailable = False
    settings = Settings()  # il logger di embed_item legge il nome del modello

    def embed(self, text: str) -> list[float]:
        from app.embeddings import EmbeddingError

        raise EmbeddingError("Ollama giù")


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
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(
            idea_threshold=0.85, cohesion_floor=0.8, topic_threshold=0.5
        ),
    )


def _item(session: Session, external_id: str, title: str, embedding=None) -> Item:
    return upsert_item(
        session,
        Item(
            source="hn",
            external_id=external_id,
            title=title,
            embedding_json=embedding,
        ),
    )


def _degraded(session: Session, external_id: str, title: str) -> tuple[Item, Idea]:
    """Un item entrato con Ollama giù: nessun vettore, idea a sé senza centroide."""
    item = _item(session, external_id, title)
    idea = attach_item_to_idea(session, item, None, 0.85)
    assert idea.centroid_json is None
    return item, idea


def test_a_degraded_item_is_re_embedded_and_re_aggregated(session: Session) -> None:
    """Il caso che ha motivato il comando: 9 item così nell'archivio reale."""
    healthy = _item(session, "1", "la stessa notizia su hn", [1.0, 0.0, 0.0])
    home = attach_item_to_idea(session, healthy, healthy.embedding_json, 0.85)
    orphan, orphan_idea = _degraded(session, "2", "la stessa notizia su rss")

    assert len(items_without_embedding(session)) == 1
    embedder = FakeEmbedder({"la stessa notizia su rss": [0.99, 0.1, 0.0]})

    summary = heal_ideas(session, _config(), Settings(), embedder=embedder)

    assert summary["n_embedded"] == 1
    assert summary["n_merged"] == 1
    assert summary["n_without_embedding_left"] == 0
    assert session.get(Idea, orphan_idea.id) is None  # dissolta
    assert {i.id for i in session.get(Idea, home.id).items} == {healthy.id, orphan.id}


def test_a_singleton_with_no_home_is_left_alone(session: Session) -> None:
    for i, vec in enumerate([[1.0, 0.0], [0.0, 1.0]]):
        item = _item(session, str(i), f"item {i}", vec)
        attach_item_to_idea(session, item, vec, 0.85)

    summary = heal_ideas(session, _config(), Settings())

    assert summary["n_singleton_checked"] == 2
    assert summary["n_merged"] == 0
    assert len(session.exec(select(Idea)).all()) == 2


def test_healing_never_touches_ideas_with_more_than_one_item(session: Session) -> None:
    """Un'idea con più item un posto l'ha già trovato: non si rimette in gioco."""
    for i, vec in enumerate([[1.0, 0.0, 0.0], [0.99, 0.1, 0.0]]):
        item = _item(session, str(i), f"doppione {i}", vec)
        attach_item_to_idea(session, item, vec, 0.85, cohesion_floor=0.8)
    pair = session.exec(select(Idea)).one()
    assert len(pair.items) == 2

    assert singleton_ideas(session) == []
    summary = heal_ideas(session, _config(), Settings())

    assert summary["n_singleton_checked"] == 0
    assert len(session.exec(select(Idea)).one().items) == 2


def test_the_absorbed_idea_leaves_no_orphan_scores(session: Session) -> None:
    healthy = _item(session, "1", "notizia hn", [1.0, 0.0, 0.0])
    attach_item_to_idea(session, healthy, healthy.embedding_json, 0.85)
    _, orphan_idea = _degraded(session, "2", "notizia rss")
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(
        Score(
            idea_id=orphan_idea.id,
            run_id=run.id,
            heat=0.1,
            credibility=0.1,
            feasibility=0.1,
            opportunity=0.1,
            fit=0.1,
            composite=0.1,
        )
    )
    session.commit()

    heal_ideas(
        session,
        _config(),
        Settings(),
        embedder=FakeEmbedder({"notizia rss": [0.99, 0.1, 0.0]}),
    )

    remaining = session.exec(select(Score)).all()
    assert all(s.idea_id != orphan_idea.id for s in remaining)


def test_user_actions_and_dates_survive_the_absorption(session: Session) -> None:
    healthy = _item(session, "1", "notizia hn", [1.0, 0.0, 0.0])
    attach_item_to_idea(session, healthy, healthy.embedding_json, 0.85)
    _, orphan_idea = _degraded(session, "2", "notizia rss")
    orphan_idea.pinned = True
    orphan_idea.note = "questa mi interessa"
    session.add(orphan_idea)
    session.commit()

    heal_ideas(
        session,
        _config(),
        Settings(),
        embedder=FakeEmbedder({"notizia rss": [0.99, 0.1, 0.0]}),
    )

    survivor = session.exec(select(Idea)).one()  # ne resta una: quale non conta
    assert len(survivor.items) == 2
    assert survivor.pinned is True
    assert survivor.note == "questa mi interessa"


def test_between_two_singletons_the_older_one_survives(session: Session) -> None:
    """L'esito non deve dipendere dall'ordine del ciclo.

    Regressione: senza una regola esplicita, un item riparato poteva far
    sparire l'idea sana che lo stava aspettando, portandosi via etichetta e
    riassunto già pagati al modello.
    """
    first = _item(session, "1", "la notizia, per prima", [1.0, 0.0, 0.0])
    older = attach_item_to_idea(session, first, first.embedding_json, 0.85)
    older.first_seen = utcnow() - timedelta(days=10)
    older.summary = "riassunto pagato al 7B"
    session.add(older)
    second = _item(session, "2", "la stessa notizia, dopo", [0.99, 0.1, 0.0])
    younger = attach_item_to_idea(session, second, second.embedding_json, 0.999)
    younger.first_seen = utcnow()
    session.add(younger)
    session.commit()

    heal_ideas(session, _config(), Settings(), embed_missing=False)

    survivor = session.exec(select(Idea)).one()
    assert survivor.id == older.id
    assert survivor.summary == "riassunto pagato al 7B"
    assert survivor.first_seen < utcnow() - timedelta(days=9)


def test_with_ollama_down_nothing_is_lost(session: Session) -> None:
    """L'embedder si arrende: gli item restano lì, pronti per il prossimo heal."""
    _degraded(session, "1", "primo")
    _degraded(session, "2", "secondo")

    summary = heal_ideas(session, _config(), Settings(), embedder=DeadEmbedder())

    assert summary["n_embedded"] == 0
    assert summary["n_without_embedding_left"] == 2
    assert len(session.exec(select(Item)).all()) == 2


def test_skip_embeddings_does_not_call_ollama(session: Session) -> None:
    _degraded(session, "1", "senza vettore")
    embedder = FakeEmbedder({"senza vettore": [1.0, 0.0]})

    summary = heal_ideas(
        session, _config(), Settings(), embedder=embedder, embed_missing=False
    )

    assert embedder.calls == 0
    assert summary["n_embedded"] == 0
    assert summary["n_without_embedding_left"] == 1


def _scored(session: Session, idea: Idea, run: Run, composite: float) -> None:
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.1,
            credibility=0.1,
            feasibility=0.1,
            opportunity=0.1,
            fit=0.1,
            composite=composite,
        )
    )
    session.commit()


def test_reinsight_targets_the_most_visible_ideas_first(session: Session) -> None:
    """Si rigenera per priorità, non per "sospetto".

    Riconoscere un riassunto ereditato sbagliato non funziona: dal vocabolario si
    misura la lingua (insight in italiano, item in inglese), dagli embedding non
    si distingue "stesso dominio, oggetto diverso" — ed è esattamente il caso.
    Quindi l'ordine è quello che conta: prima ciò che l'utente legge davvero.
    """
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.refresh(run)

    ideas = []
    rows = (
        ("alta sopra soglia", 0.9, IdeaStatus.PROPOSED),
        ("media sopra soglia", 0.7, IdeaStatus.PROPOSED),
        ("sotto soglia", 0.3, IdeaStatus.PROCESSED),
        ("archiviata", 0.95, IdeaStatus.ARCHIVED),
    )
    for position, (label, composite, status) in enumerate(rows):
        # Vettori ortogonali: quattro idee distinte, non una da quattro item.
        vector = [1.0 if j == position else 0.0 for j in range(len(rows))]
        item = _item(session, label, label, vector)
        idea = attach_item_to_idea(session, item, item.embedding_json, 0.999)
        idea.status = status
        session.add(idea)
        session.commit()
        _scored(session, idea, run, composite)
        ideas.append(idea)

    default = ideas_to_reinsight(session)
    assert [i.label for i in default] == ["alta sopra soglia", "media sopra soglia"]

    everything = ideas_to_reinsight(session, only_proposed=False)
    assert "sotto soglia" in [i.label for i in everything]
    assert "archiviata" not in [i.label for i in everything]  # fuori dalle viste

    assert [i.label for i in ideas_to_reinsight(session, limit=1)] == [
        "alta sopra soglia"
    ]


def test_regenerating_updates_both_summary_and_last_score(session: Session) -> None:
    from app.llm import IdeaInsight

    class FakeOllama:
        def insight(self, item: Item) -> IdeaInsight:
            return IdeaInsight(
                summary=f"riassunto giusto di {item.title}",
                why_text="motivo nuovo",
                difficulty=None,
            )

    item = _item(session, "1", "il vero argomento", [1.0, 0.0])
    idea = attach_item_to_idea(session, item, item.embedding_json, 0.85)
    idea.summary = "riassunto di un'altra cosa"
    session.add(idea)
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.1,
            credibility=0.1,
            feasibility=0.1,
            opportunity=0.1,
            fit=0.1,
            composite=0.1,
            why_text="motivo vecchio",
        )
    )
    session.commit()

    done = regenerate_insights(session, Settings(), [idea], ollama=FakeOllama())

    assert done == 1
    assert session.get(Idea, idea.id).summary == "riassunto giusto di il vero argomento"
    # Il why_text vive sullo score: lasciarlo indietro darebbe un riassunto
    # nuovo accanto alla vecchia motivazione.
    assert session.get(Score, (idea.id, run.id)).why_text == "motivo nuovo"


def test_cli_falls_back_when_ollama_is_not_ready(monkeypatch, tmp_path) -> None:
    """Con Ollama giù il comando ripassa i singleton invece di fallire."""
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(
        cli, "items_without_embedding", lambda session: [object(), object()]
    )
    monkeypatch.setattr(
        cli, "ollama_preflight", lambda settings: (False, "Ollama non raggiungibile")
    )
    called: dict = {}

    def _fake_heal(embed_missing=True, on_progress=None):
        called["embed_missing"] = embed_missing
        return {
            "n_embedded": 0,
            "n_merged": 0,
            "n_singleton_checked": 7,
            "n_without_embedding_left": 2,
            "n_ideas": 10,
            "n_topics": 3,
        }

    monkeypatch.setattr(cli, "execute_heal", _fake_heal)

    result = CliRunner().invoke(cli.app, ["heal"])

    assert result.exit_code == 0
    assert called["embed_missing"] is False  # non ha nemmeno provato a chiamare
    assert "Ollama non raggiungibile" in result.stdout
    assert "Niente da riparare" in result.stdout
    assert "2 item restano senza embedding" in result.stdout
