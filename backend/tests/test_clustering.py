from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.clustering import (
    assign_ideas_to_topics,
    attach_item_to_idea,
    group_indices_by_similarity,
    sweep_topic_thresholds,
)
from app.db import init_db, upsert_item
from app.models import Idea, Item, Topic


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _item(session: Session, external_id: str, title: str, embedding=None) -> Item:
    item = upsert_item(
        session,
        Item(source="hn", external_id=external_id, title=title, embedding_json=embedding),
    )
    return item


def test_similar_items_merge_into_one_idea(session: Session) -> None:
    a = _item(session, "1", "un agente AI per il codice", [1.0, 0.0, 0.0])
    b = _item(session, "2", "agente AI che scrive codice", [0.99, 0.1, 0.0])

    idea_a = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    idea_b = attach_item_to_idea(session, b, b.embedding_json, threshold=0.8)

    assert idea_a.id == idea_b.id  # deduplicati nella stessa idea
    assert len(session.exec(select(Idea)).all()) == 1
    assert len(idea_b.items) == 2


def test_dissimilar_items_stay_separate(session: Session) -> None:
    a = _item(session, "1", "agente AI", [1.0, 0.0])
    b = _item(session, "2", "ricetta della carbonara", [0.0, 1.0])

    attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    attach_item_to_idea(session, b, b.embedding_json, threshold=0.8)

    assert len(session.exec(select(Idea)).all()) == 2


def test_without_embedding_falls_back_to_one_item_one_idea(session: Session) -> None:
    a = _item(session, "1", "primo")
    b = _item(session, "2", "secondo")

    attach_item_to_idea(session, a, None, threshold=0.8)
    attach_item_to_idea(session, b, None, threshold=0.8)

    assert len(session.exec(select(Idea)).all()) == 2


def test_existing_item_reuses_its_idea(session: Session) -> None:
    a = _item(session, "1", "agente AI", [1.0, 0.0])
    first = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    again = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    assert first.id == again.id
    assert len(session.exec(select(Idea)).all()) == 1


def test_ideas_group_into_topics(session: Session) -> None:
    for i, (external_id, title, emb) in enumerate(
        [
            ("1", "agente AI per il codice", [1.0, 0.0]),
            ("2", "copilota per sviluppatori", [0.95, 0.3]),
            ("3", "coltivare pomodori", [0.0, 1.0]),
        ]
    ):
        item = _item(session, external_id, title, emb)
        attach_item_to_idea(session, item, emb, threshold=0.99)  # niente merge di idee

    topics = assign_ideas_to_topics(session, threshold=0.8)
    assert len(session.exec(select(Topic)).all()) == 2  # tech + orto

    ideas = session.exec(select(Idea)).all()
    assert all(i.topic_id is not None for i in ideas)
    tech = [i for i in ideas if "pomodori" not in i.label]
    assert len({i.topic_id for i in tech}) == 1  # le due tech nello stesso topic
    assert topics


def test_topic_namer_is_used_and_failures_are_survived(session: Session) -> None:
    item = _item(session, "1", "agente AI", [1.0, 0.0])
    attach_item_to_idea(session, item, item.embedding_json, threshold=0.8)

    assign_ideas_to_topics(session, threshold=0.8, namer=lambda labels: "Agenti AI")
    assert session.exec(select(Topic)).one().label == "Agenti AI"

    def explode(labels):
        raise RuntimeError("LLM giù")

    assign_ideas_to_topics(session, threshold=0.8, namer=explode)  # non deve sollevare
    assert session.exec(select(Topic)).one().label == "Agenti AI"


def test_group_indices_by_similarity_threshold_effect() -> None:
    vectors = [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]  # cos(v0,v1)=0.8, cos(v0,v2)=0
    loose = group_indices_by_similarity(vectors, threshold=0.7)
    strict = group_indices_by_similarity(vectors, threshold=0.95)
    assert loose == [[0, 1], [2]]
    assert strict == [[0], [1], [2]]


def test_group_indices_predicts_assign_ideas_from_scratch(session: Session) -> None:
    """La simulazione dello sweep deve PREVEDERE esattamente il recluster reale."""
    vectors = [[1.0, 0.0], [0.95, 0.31225], [0.0, 1.0], [0.1, 0.995]]
    for i, vec in enumerate(vectors):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)  # 4 idee distinte

    predicted = group_indices_by_similarity(vectors, threshold=0.9)

    assign_ideas_to_topics(session, threshold=0.9)
    ideas = session.exec(select(Idea)).all()
    actual: dict[int, list[int]] = {}
    for index, idea in enumerate(ideas):
        actual.setdefault(idea.topic_id, []).append(index)

    assert sorted(map(sorted, predicted)) == sorted(map(sorted, actual.values()))


def test_sweep_topic_thresholds_reports_without_writing(session: Session) -> None:
    for i, vec in enumerate([[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)

    rows = sweep_topic_thresholds(session, [0.7, 0.95])

    assert [r["n_topics"] for r in rows] == [2, 3]
    assert rows[0]["max_size"] == 2
    assert rows[0]["n_singleton"] == 1
    assert rows[0]["biggest_sample"] == ["idea 0", "idea 1"]
    # Anteprima pura: la sweep non deve aver creato alcun topic.
    assert session.exec(select(Topic)).all() == []
