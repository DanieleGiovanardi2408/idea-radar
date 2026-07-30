"""Export CSV: stesse righe di `GET /ideas`, in un formato che un foglio apre."""

import csv
import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.api import app, get_db
from app.db import init_db
from app.export import COLUMNS, ideas_to_csv
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, Topic


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(session: Session, *, note: str | None = None) -> Idea:
    topic = Topic(label="Agenti AI")
    session.add(topic)
    session.commit()
    session.refresh(topic)

    idea = Idea(
        label="Idea A",
        status=IdeaStatus.PROPOSED,
        topic_id=topic.id,
        note=note,
        summary="Un riassunto, con virgola",
    )
    idea.items = [
        Item(source="hn", external_id="1", title="Idea A", url="https://a.example"),
        Item(source="github", external_id="2", title="Idea A repo", url="https://b.example"),
    ]
    session.add(idea)
    session.commit()
    session.refresh(idea)

    run = Run(n_items=2, status=RunStatus.DONE, phase="completato")
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.5,
            credibility=0.5,
            feasibility=0.5,
            opportunity=0.5,
            fit=0.5,
            composite=0.8123456,
            why_text="perché è interessante",
        )
    )
    session.commit()
    return idea


def _parse(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_has_the_declared_header_and_one_row_per_idea(session: Session) -> None:
    _seed(session)
    from app.queries import top_ideas

    text = ideas_to_csv(top_ideas(session, limit=10))
    rows = _parse(text)

    assert text.splitlines()[0] == ",".join(COLUMNS)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "Idea A"
    assert row["topic"] == "Agenti AI"
    assert row["status"] == "proposed"
    assert row["composite"] == "0.8123"  # 4 decimali: sotto è rumore
    assert row["n_items"] == "2"
    assert row["urls"] == "https://a.example https://b.example"


def test_a_comma_in_a_free_field_does_not_break_the_columns(session: Session) -> None:
    """Note e summary sono testo libero: il quoting è il motivo per cui si usa csv."""
    _seed(session, note='virgola, e "virgolette"')
    from app.queries import top_ideas

    row = _parse(ideas_to_csv(top_ideas(session, limit=10)))[0]

    assert row["note"] == 'virgola, e "virgolette"'
    assert row["summary"] == "Un riassunto, con virgola"


def test_an_idea_without_score_exports_empty_score_fields(session: Session) -> None:
    idea = Idea(label="Senza score", status=IdeaStatus.PROCESSED)
    session.add(idea)
    session.commit()
    from app.queries import top_ideas

    rows = _parse(ideas_to_csv(top_ideas(session, limit=10)))
    row = next(r for r in rows if r["label"] == "Senza score")

    assert row["composite"] == ""
    assert row["difficulty"] == ""
    assert row["urls"] == ""


def test_the_endpoint_speaks_csv_when_asked(client: TestClient, session: Session) -> None:
    _seed(session)

    resp = client.get("/ideas", params={"format": "csv"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    rows = _parse(resp.text)
    assert [r["label"] for r in rows] == ["Idea A"]


def test_the_endpoint_still_speaks_json_by_default(
    client: TestClient, session: Session
) -> None:
    _seed(session)

    data = client.get("/ideas").json()

    assert isinstance(data, list)
    assert data[0]["label"] == "Idea A"


def test_csv_respects_the_same_filters_as_json(client: TestClient, session: Session) -> None:
    _seed(session)

    resp = client.get("/ideas", params={"format": "csv", "status": "archived"})

    assert _parse(resp.text) == []  # header solo: stessi filtri del JSON
