"""Azioni utente sulle idee: pin, dismiss, visto, nota — e loro effetti.

Lo stato utente è ortogonale allo ``status`` della pipeline: i run non lo
toccano mai. Qui si verifica il contratto PATCH, l'esclusione delle scartate
dalle viste, l'ordinamento (pinnate prima), la paginazione e il fatto che il
pin protegga dall'auto-archiviazione.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.api import app, get_db
from app.db import init_db
from app.lifecycle import archive_stale_ideas
from app.models import Idea, IdeaStatus, Run, RunStatus, Score, utcnow


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


def _seed_idea(session: Session, label: str, composite: float, run: Run) -> Idea:
    idea = Idea(label=label, status=IdeaStatus.PROPOSED)
    session.add(idea)
    session.commit()
    session.refresh(idea)
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.5,
            credibility=0.5,
            feasibility=0.5,
            opportunity=0.5,
            fit=0.5,
            composite=composite,
        )
    )
    session.commit()
    return idea


def _seed_run(session: Session) -> Run:
    run = Run(status=RunStatus.DONE, phase="completato")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---- PATCH /ideas/{id} ------------------------------------------------------


def test_patch_pin_and_unpin(client: TestClient, session: Session) -> None:
    run = _seed_run(session)
    idea = _seed_idea(session, "A", 0.8, run)

    data = client.patch(f"/ideas/{idea.id}", json={"pinned": True}).json()
    assert data["pinned"] is True

    data = client.patch(f"/ideas/{idea.id}", json={"pinned": False}).json()
    assert data["pinned"] is False


def test_patch_dismiss_and_restore(client: TestClient, session: Session) -> None:
    run = _seed_run(session)
    idea = _seed_idea(session, "A", 0.8, run)

    data = client.patch(f"/ideas/{idea.id}", json={"dismissed": True}).json()
    assert data["dismissed_at"] is not None

    data = client.patch(f"/ideas/{idea.id}", json={"dismissed": False}).json()
    assert data["dismissed_at"] is None


def test_patch_seen_and_note(client: TestClient, session: Session) -> None:
    run = _seed_run(session)
    idea = _seed_idea(session, "A", 0.8, run)

    data = client.patch(
        f"/ideas/{idea.id}", json={"seen": True, "note": "da approfondire"}
    ).json()
    assert data["seen_at"] is not None
    assert data["note"] == "da approfondire"

    # Payload senza "note": la nota NON va toccata.
    data = client.patch(f"/ideas/{idea.id}", json={"pinned": True}).json()
    assert data["note"] == "da approfondire"

    # "note": null esplicito la cancella.
    data = client.patch(f"/ideas/{idea.id}", json={"note": None}).json()
    assert data["note"] is None


def test_patch_404(client: TestClient) -> None:
    assert client.patch("/ideas/999", json={"pinned": True}).status_code == 404


# ---- Effetti sulle viste ----------------------------------------------------


def test_dismissed_hidden_from_default_views(
    client: TestClient, session: Session
) -> None:
    run = _seed_run(session)
    keep = _seed_idea(session, "Tienimi", 0.7, run)
    drop = _seed_idea(session, "Scartami", 0.9, run)

    client.patch(f"/ideas/{drop.id}", json={"dismissed": True})

    labels = [r["label"] for r in client.get("/ideas").json()]
    assert labels == ["Tienimi"]

    # Anche col filtro status esplicito le scartate restano fuori...
    labels = [
        r["label"] for r in client.get("/ideas", params={"status": "proposed"}).json()
    ]
    assert labels == ["Tienimi"]

    # ...e si rivedono solo chiedendole apposta.
    labels = [
        r["label"]
        for r in client.get("/ideas", params={"include_dismissed": True}).json()
    ]
    assert set(labels) == {"Tienimi", "Scartami"}
    assert keep.id is not None


def test_pinned_ideas_come_first(client: TestClient, session: Session) -> None:
    run = _seed_run(session)
    _seed_idea(session, "Alta", 0.9, run)
    low = _seed_idea(session, "Bassa ma pinnata", 0.2, run)

    client.patch(f"/ideas/{low.id}", json={"pinned": True})

    labels = [r["label"] for r in client.get("/ideas").json()]
    assert labels == ["Bassa ma pinnata", "Alta"]


def test_ideas_pagination_offset(client: TestClient, session: Session) -> None:
    run = _seed_run(session)
    for n in range(5):
        _seed_idea(session, f"Idea {n}", 0.9 - n * 0.1, run)

    page1 = client.get("/ideas", params={"limit": 2}).json()
    page2 = client.get("/ideas", params={"limit": 2, "offset": 2}).json()
    assert [r["label"] for r in page1] == ["Idea 0", "Idea 1"]
    assert [r["label"] for r in page2] == ["Idea 2", "Idea 3"]


# ---- Migrazione additiva -----------------------------------------------------


def test_init_db_adds_user_columns_to_existing_db(tmp_path: Path) -> None:
    """Su un DB creato prima dei campi utente, init_db li aggiunge da solo."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ideas (id INTEGER PRIMARY KEY, label TEXT)")
    conn.commit()
    conn.close()

    init_db(create_engine(f"sqlite:///{db}"))

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ideas)")}
    conn.close()
    assert {"pinned", "dismissed_at", "seen_at", "note"} <= cols


# ---- Interazione col ciclo di vita ------------------------------------------


def test_archive_skips_pinned_ideas(session: Session) -> None:
    stale_date = utcnow() - timedelta(days=30)
    pinned = Idea(label="Pinnata", last_seen=stale_date, pinned=True)
    plain = Idea(label="Normale", last_seen=stale_date)
    session.add(pinned)
    session.add(plain)
    session.commit()

    archived = archive_stale_ideas(session, older_than_days=14)

    session.refresh(pinned)
    session.refresh(plain)
    assert archived == 1
    assert plain.status == IdeaStatus.ARCHIVED
    assert pinned.status != IdeaStatus.ARCHIVED


def test_dismiss_survives_new_scores(client: TestClient, session: Session) -> None:
    """Un run nuovo che ri-scora l'idea non deve annullare il dismiss."""
    run = _seed_run(session)
    idea = _seed_idea(session, "Scartata", 0.8, run)
    client.patch(f"/ideas/{idea.id}", json={"dismissed": True})

    # Arriva un run successivo con uno score nuovo (la pipeline non tocca
    # mai i campi utente: qui si simula solo il suo effetto sul DB).
    run2 = _seed_run(session)
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run2.id,
            heat=0.9,
            credibility=0.9,
            feasibility=0.9,
            opportunity=0.9,
            fit=0.9,
            composite=0.95,
        )
    )
    session.commit()

    assert client.get("/ideas").json() == []
    detail = client.get(f"/ideas/{idea.id}").json()
    assert detail["dismissed_at"] is not None
    assert detail["composite"] == 0.95  # lo score si aggiorna, il dismiss resta
