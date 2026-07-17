from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

from app.db import init_db, make_engine, upsert_item
from app.models import Difficulty, Idea, Item, Run, Score, utcnow


@pytest.fixture
def engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def make_item(**overrides) -> Item:
    defaults = dict(
        source="hn",
        external_id="12345",
        title="Un titolo",
        url="https://example.com/post",
        text="Testo del post",
        author="alice",
        engagement_json={"points": 10},
        created_at=datetime(2026, 7, 1),
        raw_json={"id": "12345"},
    )
    defaults.update(overrides)
    return Item(**defaults)


def test_init_db_creates_all_tables(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"items", "ideas", "idea_items", "scores", "runs"} <= tables


def test_upsert_inserts_new_item(session: Session) -> None:
    item = upsert_item(session, make_item())

    assert item.id is not None
    assert session.exec(select(Item)).all() == [item]


def test_upsert_is_idempotent_and_updates(session: Session) -> None:
    first = upsert_item(session, make_item(title="Vecchio titolo"))
    second = upsert_item(
        session,
        make_item(title="Nuovo titolo", engagement_json={"points": 99}),
    )

    assert second.id == first.id
    items = session.exec(select(Item)).all()
    assert len(items) == 1
    assert items[0].title == "Nuovo titolo"
    assert items[0].engagement_json == {"points": 99}


def test_utcnow_is_naive(session: Session) -> None:
    assert utcnow().tzinfo is None

    item = upsert_item(session, make_item())
    session.refresh(item)
    assert item.fetched_at.tzinfo is None


def test_json_columns_round_trip_dicts(engine, session: Session) -> None:
    upsert_item(
        session,
        make_item(engagement_json={"points": 10, "tags": ["ai", "devtools"]}),
    )

    with Session(engine) as fresh:
        stored = fresh.exec(select(Item)).one()
        assert stored.engagement_json == {"points": 10, "tags": ["ai", "devtools"]}
        assert stored.raw_json == {"id": "12345"}


def test_upsert_distinguishes_source_and_external_id(session: Session) -> None:
    upsert_item(session, make_item(source="hn", external_id="1"))
    upsert_item(session, make_item(source="github", external_id="1"))
    upsert_item(session, make_item(source="hn", external_id="2"))

    assert len(session.exec(select(Item)).all()) == 3


def test_idea_items_many_to_many(session: Session) -> None:
    item_a = upsert_item(session, make_item(external_id="a"))
    item_b = upsert_item(session, make_item(external_id="b"))
    idea = Idea(label="idea di prova", summary="riassunto")
    idea.items = [item_a, item_b]
    session.add(idea)
    session.commit()
    session.refresh(idea)

    reloaded = session.exec(select(Idea).where(Idea.id == idea.id)).one()
    assert {i.external_id for i in reloaded.items} == {"a", "b"}
    assert reloaded.status == "processed"
    assert item_a.ideas == [reloaded]


def make_score(idea: Idea, run: Run, **overrides) -> Score:
    defaults = dict(
        idea_id=idea.id,
        run_id=run.id,
        heat=0.5,
        credibility=0.5,
        feasibility=0.5,
        opportunity=0.5,
        fit=0.5,
        composite=0.5,
        why_text="motivazione",
    )
    defaults.update(overrides)
    return Score(**defaults)


def test_score_difficulty_is_textual_enum(session: Session) -> None:
    idea = Idea(label="idea")
    run = Run()
    session.add(idea)
    session.add(run)
    session.commit()

    session.add(make_score(idea, run, difficulty=Difficulty.MED))
    session.commit()

    stored = session.exec(select(Score)).one()
    assert stored.difficulty == Difficulty.MED
    assert stored.difficulty == "med"
    assert {d.value for d in Difficulty} == {"low", "med", "high"}


def test_score_difficulty_is_nullable(session: Session) -> None:
    idea = Idea(label="idea")
    run = Run()
    session.add(idea)
    session.add(run)
    session.commit()

    session.add(make_score(idea, run, difficulty=None))
    session.commit()

    assert session.exec(select(Score)).one().difficulty is None


def test_make_engine_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    """Coi run schedulati si scrive mentre la UI legge: WAL non è un vezzo."""
    engine = make_engine(tmp_path / "wal.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
