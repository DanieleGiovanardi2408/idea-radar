"""`rebuild_ideas` ri-aggrega gli item già in archivio con le soglie correnti.

Il caso reale che l'ha resa necessaria: mesi di run col vecchio criterio (merge
deciso sul centroide) avevano prodotto un'idea da 740 item. Cambiare criterio
non bastava — lo storico va ricostruito, e senza perdere né le azioni
dell'utente né gli insight già pagati al modello locale.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select
from typer.testing import CliRunner

from app import cli
from app.appconfig import AppConfig, ClusteringConfig, LifecycleConfig, ScoringConfig
from app.clustering import attach_item_to_idea, group_items_by_similarity
from app.config import Settings
from app.db import init_db, upsert_item
from app.models import Idea, IdeaItem, Item, ItemStat, Run, RunStatus, Score, Topic
from app.pipeline import preview_rebuild_ideas, rebuild_ideas


class FakeOllama:
    def topic_label(self, labels: list[str]) -> str:
        return "topic"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _config(idea_threshold: float = 0.85, cohesion_floor: float = 0.8) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(
            idea_threshold=idea_threshold,
            cohesion_floor=cohesion_floor,
            topic_threshold=0.5,
            llm_topic_labels=False,
        ),
        lifecycle=LifecycleConfig(archive_after_days=0),
    )


def _item(session: Session, external_id: str, title: str, embedding, **kwargs) -> Item:
    return upsert_item(
        session,
        Item(
            source="hn",
            external_id=external_id,
            title=title,
            embedding_json=embedding,
            **kwargs,
        ),
    )


def _done_run(session: Session) -> Run:
    run = Run(status=RunStatus.DONE, finished_at=None)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _blob(session: Session, vectors: list[list[float]]) -> tuple[Idea, list[Item]]:
    """Un'idea-calamita come quelle prodotte dal vecchio criterio: membri estranei."""
    items = [
        _item(session, f"b{i}", f"membro {i}", v) for i, v in enumerate(vectors)
    ]
    idea = Idea(label="membro 0", centroid_json=vectors[0])
    idea.items = list(items)
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea, items


def test_rebuild_splits_a_blob_into_coherent_ideas(session: Session) -> None:
    size = 6
    vectors = [[1.0 if j == i else 0.0 for j in range(size)] for i in range(size)]
    idea, items = _blob(session, vectors)
    run = _done_run(session)
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
        )
    )
    session.commit()

    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert summary["n_ideas_before"] == 1
    assert summary["n_ideas"] == size  # membri ortogonali: nessuno si fonde
    assert summary["max_size"] == 1
    assert len(session.exec(select(Item)).all()) == size  # gli item non si toccano


def test_rebuild_keeps_true_duplicates_together(session: Session) -> None:
    """Il rebuild non è solo distruttivo: i doppioni veri restano fusi."""
    _item(session, "1", "la stessa notizia su hn", [1.0, 0.0, 0.0])
    _item(session, "2", "la stessa notizia su rss", [0.99, 0.1, 0.0])
    _item(session, "3", "un'altra cosa", [0.0, 1.0, 0.0])
    _done_run(session)

    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert summary["n_ideas"] == 2
    assert summary["max_size"] == 2


def test_rebuild_preserves_items_engagement_history_and_user_actions(
    session: Session,
) -> None:
    idea, items = _blob(session, [[1.0, 0.0], [0.0, 1.0]])
    run = _done_run(session)
    for item in items:
        session.add(ItemStat(item_id=item.id, run_id=run.id, engagement=42.0))
    idea.pinned = True
    idea.note = "da guardare bene"
    idea.seen_at = run.started_at
    session.add(idea)
    session.commit()
    anchor_id = items[0].id  # "membro 0" dà l'etichetta all'idea

    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert summary["n_user_state_restored"] == 1
    assert len(session.exec(select(ItemStat)).all()) == 2  # storia intatta
    heir = session.get(Item, anchor_id).ideas[0]
    assert heir.pinned is True
    assert heir.note == "da guardare bene"
    assert heir.seen_at is not None
    # Lo stato utente NON si spalma sulle altre idee nate dalla stessa blob.
    others = [i for i in session.exec(select(Idea)).all() if i.id != heir.id]
    assert all(not o.pinned and o.note is None for o in others)


def test_rebuild_carries_over_llm_insights_and_rescores(session: Session) -> None:
    idea, items = _blob(session, [[1.0, 0.0], [0.0, 1.0]])
    idea.summary = "riassunto pagato al 7B"
    session.add(idea)
    run = _done_run(session)
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.2,
            credibility=0.2,
            feasibility=0.2,
            opportunity=0.2,
            fit=0.2,
            composite=0.2,
            why_text="perché conta",
            difficulty=None,
        )
    )
    session.commit()

    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert summary["n_scored"] == 2
    assert summary["scored_on_run"] == run.id
    ideas = session.exec(select(Idea)).all()
    assert all(i.summary == "riassunto pagato al 7B" for i in ideas)
    scores = session.exec(select(Score)).all()
    assert len(scores) == 2  # gli score vecchi (idea cancellata) non restano orfani
    assert all(s.why_text == "perché conta" for s in scores)
    assert all(s.run_id == run.id for s in scores)


def test_rebuild_dates_come_from_the_items_not_from_now(session: Session) -> None:
    """Un'idea ricostruita non deve sembrare nata oggi: il ciclo di vita ci si basa."""
    old = Run(status=RunStatus.DONE)
    session.add(old)
    session.commit()
    a = _item(session, "1", "vecchio", [1.0, 0.0])
    a.fetched_at = a.fetched_at - timedelta(days=30)
    session.add(a)
    session.commit()
    stamp = a.fetched_at

    rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    idea = session.exec(select(Idea)).one()
    assert idea.first_seen == stamp
    assert idea.last_seen == stamp


def test_rebuild_drops_stale_topics_and_stats(session: Session) -> None:
    _blob(session, [[1.0, 0.0], [0.0, 1.0]])
    session.add(Topic(label="topic orfano", centroid_json=[0.5, 0.5]))
    _done_run(session)
    session.commit()

    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    topics = session.exec(select(Topic)).all()
    assert "topic orfano" not in [t.label for t in topics]
    assert summary["n_topics"] == len(topics)
    # Nessun link idea-item orfano rimasto.
    idea_ids = {i.id for i in session.exec(select(Idea)).all()}
    assert all(link.idea_id in idea_ids for link in session.exec(select(IdeaItem)).all())


def test_items_without_embedding_stay_one_idea_each(session: Session) -> None:
    _item(session, "1", "senza vettore", None)
    _item(session, "2", "anche questo", None)
    _done_run(session)

    preview = preview_rebuild_ideas(session, _config())
    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert preview["n_items_without_embedding"] == 2
    assert preview["n_ideas"] == 2
    assert summary["n_ideas"] == 2


def test_preview_predicts_the_real_rebuild(session: Session) -> None:
    """L'anteprima non è una stima: deve dare lo stesso risultato del rebuild."""
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.1, 0.0],  # doppione del primo
        [0.0, 1.0, 0.0],
        [0.1, 0.99, 0.0],  # doppione del terzo
        [0.0, 0.0, 1.0],
    ]
    for i, vec in enumerate(vectors):
        _item(session, str(i), f"item {i}", vec)
    _done_run(session)

    preview = preview_rebuild_ideas(session, _config())
    summary = rebuild_ideas(session, _config(), Settings(), ollama=FakeOllama())

    assert preview["n_ideas"] == summary["n_ideas"] == 3
    assert preview["max_size"] == summary["max_size"] == 2
    assert preview["n_singleton"] == summary["n_singleton"] == 1


def test_preview_writes_nothing(session: Session) -> None:
    _blob(session, [[1.0, 0.0], [0.0, 1.0]])
    before = len(session.exec(select(Idea)).all())

    preview_rebuild_ideas(session, _config())

    assert len(session.exec(select(Idea)).all()) == before


def test_group_items_by_similarity_matches_attach(session: Session) -> None:
    """La funzione dell'anteprima e quella della pipeline devono decidere uguale."""
    vectors = [[1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.5, 0.866, 0.0], [0.0, 1.0, 0.0]]
    predicted = group_items_by_similarity(vectors, 0.85, 0.8)

    actual: dict[int, list[int]] = {}
    for index, vec in enumerate(vectors):
        item = _item(session, str(index), f"item {index}", vec)
        idea = attach_item_to_idea(session, item, vec, 0.85, cohesion_floor=0.8)
        actual.setdefault(idea.id, []).append(index)

    assert sorted(map(sorted, predicted)) == sorted(map(sorted, actual.values()))


def test_rebuild_reports_progress_through_the_slow_parts(session: Session) -> None:
    """Il comando resta muto per minuti: l'avanzamento dice almeno dove sta.

    Le due parti lente sono il naming dei topic (una chiamata al modello per
    topic) e il riscoring; entrambe devono farsi sentire.
    """
    messages: list[str] = []
    # Le due prime idee stanno a 0.75 di similarità: sopra `topic_threshold`
    # (0.5) e sotto `idea_threshold` (0.85), quindi restano idee distinte che
    # formano UN topic — quello che il naming deve nominare. Da quando un'idea
    # sola non apre un tema, senza una coppia qui non ci sarebbe nulla da
    # nominare e il messaggio di avanzamento non arriverebbe mai.
    for i, vec in enumerate([[1.0, 0.0], [0.75, 0.66], [0.0, 1.0]]):
        _item(session, str(i), f"item {i}", vec)
    _done_run(session)

    config = _config()
    config.clustering.llm_topic_labels = True
    config.clustering.topic_label_min_ideas = 1
    rebuild_ideas(
        session,
        config,
        Settings(),
        ollama=FakeOllama(),
        on_progress=messages.append,
    )

    assert any("raggruppo" in m for m in messages)
    assert any(m.startswith("nomi topic") for m in messages)


def test_rescoring_twice_updates_instead_of_duplicating(session: Session) -> None:
    """Regressione: ``rescore`` gira su un archivio INTATTO, dove lo score esiste già.

    ``rebuild-ideas`` cancella tutto prima, quindi un INSERT cieco gli andava
    bene; ``rescore`` no, e violava la chiave primaria (idea, run) — e la stessa
    cosa succedeva a ``topic_stats``. Rifotografare lo stesso run è legittimo:
    la fotografia si sovrascrive.
    """
    from app.pipeline import _record_topic_stats, _rescore_ideas
    from app.models import TopicStat

    _item(session, "1", "un doppione", [1.0, 0.0])
    run = _done_run(session)
    config = _config()
    rebuild_ideas(session, config, Settings(), ollama=FakeOllama())

    prima = _rescore_ideas(session, config, run, {})
    _record_topic_stats(session, run)
    dopo = _rescore_ideas(session, config, run, {})  # non deve esplodere
    _record_topic_stats(session, run)

    assert prima == dopo == 1
    scores = session.exec(select(Score).where(Score.run_id == run.id)).all()
    assert len(scores) == 1  # aggiornato, non duplicato
    stats = session.exec(select(TopicStat).where(TopicStat.run_id == run.id)).all()
    assert len(stats) == len({s.topic_id for s in stats})


def test_cli_dry_run_does_not_rebuild(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "execute_preview_rebuild",
        lambda idea_threshold=None, cohesion_floor=None: {
            "threshold": 0.86,
            "cohesion_floor": 0.82,
            "n_items": 1383,
            "n_items_without_embedding": 0,
            "n_ideas_now": 215,
            "n_ideas": 1267,
            "max_size": 6,
            "n_singleton": 1174,
            "biggest_sample": ["la stessa notizia su sei fonti"],
        },
    )

    def _boom(idea_threshold=None, cohesion_floor=None):
        raise AssertionError("--dry-run non deve ricostruire nulla")

    monkeypatch.setattr(cli, "execute_rebuild_ideas", _boom)

    result = CliRunner().invoke(cli.app, ["rebuild-ideas", "--dry-run"])

    assert result.exit_code == 0
    assert "1267 idee" in result.stdout
    assert "215" in result.stdout  # quante sono ora, per il confronto
    assert "la stessa notizia su sei fonti" in result.stdout


def test_cli_refuses_when_there_is_nothing_to_rebuild(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "execute_preview_rebuild",
        lambda idea_threshold=None, cohesion_floor=None: {
            "threshold": 0.86,
            "cohesion_floor": 0.82,
            "n_items": 0,
            "n_items_without_embedding": 0,
            "n_ideas_now": 0,
            "n_ideas": 0,
            "max_size": 0,
            "n_singleton": 0,
            "biggest_sample": [],
        },
    )
    result = CliRunner().invoke(cli.app, ["rebuild-ideas"])
    assert result.exit_code == 0
    assert "serve prima un run" in result.stdout
