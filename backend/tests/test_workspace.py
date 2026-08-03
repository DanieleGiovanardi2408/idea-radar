"""Il tavolo di lavoro: le idee salvate come piano, con l'attività dal radar.

La regola sotto tutti questi test: la tabella workspace è dell'UTENTE. Entrare
è idempotente, uscire non tocca l'idea, e la pipeline non c'entra mai.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.api import app, get_db
from app.db import init_db
from app.models import (
    Idea,
    Item,
    ItemStat,
    Run,
    RunStatus,
    WorkspaceStage,
)
from app.workspace import (
    WorkspaceError,
    activity_since,
    enter_workspace,
    normalize_checklist,
    normalize_links,
)

NOW = datetime(2026, 8, 3, 12, 0, 0)


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


def _idea(session: Session, moves: list[str] | None = None) -> Idea:
    idea = Idea(label="Idea da sviluppare", moves_json=moves)
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


def test_enter_seeds_the_checklist_from_moves(session: Session) -> None:
    idea = _idea(session, moves=["Scrivi il prototipo", "Compra il dominio"])
    entry = enter_workspace(session, idea)
    assert entry.stage == WorkspaceStage.EXPLORE
    assert entry.checklist_json == [
        {"text": "Scrivi il prototipo", "done": False},
        {"text": "Compra il dominio", "done": False},
    ]


def test_enter_twice_does_not_reset(session: Session) -> None:
    """Rientrare non azzera il lavoro: le spunte sopravvivono."""
    idea = _idea(session, moves=["a"])
    entry = enter_workspace(session, idea)
    entry.checklist_json = [{"text": "a", "done": True}]
    entry.stage = WorkspaceStage.BUILDING
    session.add(entry)
    session.commit()

    again = enter_workspace(session, idea)
    assert again.stage == WorkspaceStage.BUILDING
    assert again.checklist_json == [{"text": "a", "done": True}]


def test_checklist_normalization_drops_empty_and_caps_text() -> None:
    out = normalize_checklist(
        [{"text": "  fai una cosa  ", "done": 1}, {"text": "   "}, {"text": "x" * 999}]
    )
    assert out[0] == {"text": "fai una cosa", "done": True}
    assert len(out) == 2  # la voce vuota sparisce
    assert len(out[1]["text"]) == 300


def test_links_must_be_http(session: Session) -> None:
    assert normalize_links(["https://github.com/x", "https://github.com/x"]) == [
        "https://github.com/x"
    ]  # dedup
    with pytest.raises(WorkspaceError):
        normalize_links(["javascript:alert(1)"])


def test_activity_counts_only_whats_new(session: Session) -> None:
    """Item e engagement arrivati DOPO il salvataggio, non tutta la storia."""
    since = NOW - timedelta(days=7)
    idea = _idea(session)
    vecchio = Item(source="github", external_id="v", title="vecchio",
                   fetched_at=since - timedelta(days=10))
    nuovo = Item(source="github", external_id="n", title="nuovo",
                 fetched_at=since + timedelta(days=1))
    idea.items = [vecchio, nuovo]
    session.add(idea)
    session.commit()
    session.refresh(idea)

    run = Run(started_at=NOW, status=RunStatus.DONE)
    session.add(run)
    session.commit()
    # Il vecchio item: 100 → 160, ma 100→120 era PRIMA del salvataggio.
    for offset_days, engagement in [(-12, 100.0), (-9, 120.0), (6, 160.0)]:
        r = Run(started_at=since + timedelta(days=offset_days), status=RunStatus.DONE)
        session.add(r)
        session.commit()
        session.add(ItemStat(item_id=vecchio.id, run_id=r.id, engagement=engagement,
                             observed_at=since + timedelta(days=offset_days)))
        session.commit()

    activity = activity_since(session, idea, since)
    assert activity["n_new_items"] == 1
    assert activity["gained_engagement"] == 40.0  # 160 − 120, non −100
    # I titoli, non solo il conteggio: il radar dice COSA ha trovato.
    assert [i["title"] for i in activity["new_items"]] == ["nuovo"]


def test_generate_moves_on_demand(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un'idea sotto soglia non ha mosse: sul tavolo si generano al volo,
    e finiscono in coda alla checklist senza toccare le spunte esistenti."""

    class _FakeOllama:
        def __init__(self, settings) -> None: ...

        def moves(self, label, summary, why, signals) -> list[str]:
            return ["Costruisci il prototipo", "Scrivi il confronto"]

    monkeypatch.setattr("app.api.OllamaClient", _FakeOllama)

    idea = _idea(session)  # niente mosse
    client.post(f"/workspace/{idea.id}")
    client.patch(
        f"/workspace/{idea.id}",
        json={"checklist": [{"text": "Passo mio", "done": True}]},
    )

    res = client.post(f"/workspace/{idea.id}/moves")
    assert res.status_code == 200
    checklist = res.json()["checklist"]
    assert checklist[0] == {"text": "Passo mio", "done": True}  # intatto
    assert {c["text"] for c in checklist[1:]} == {
        "Costruisci il prototipo",
        "Scrivi il confronto",
    }
    # Le mosse sono state salvate anche sull'idea: il dossier le mostra.
    session.refresh(session.get(Idea, idea.id))
    assert session.get(Idea, idea.id).moves_json == [
        "Costruisci il prototipo",
        "Scrivi il confronto",
    ]
    # Rilanciare non duplica.
    res = client.post(f"/workspace/{idea.id}/moves")
    assert len(res.json()["checklist"]) == 3


def test_generate_moves_says_503_without_ollama(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import OllamaError

    class _DownOllama:
        def __init__(self, settings) -> None: ...

        def moves(self, *args) -> list[str]:
            raise OllamaError("connessione rifiutata")

    monkeypatch.setattr("app.api.OllamaClient", _DownOllama)
    idea = _idea(session)
    client.post(f"/workspace/{idea.id}")
    res = client.post(f"/workspace/{idea.id}/moves")
    assert res.status_code == 503
    assert "Ollama" in res.json()["detail"]


# ---- endpoint ----------------------------------------------------------------


def test_workspace_crud_roundtrip(client: TestClient, session: Session) -> None:
    idea = _idea(session, moves=["Prima mossa"])

    created = client.post(f"/workspace/{idea.id}")
    assert created.status_code == 201
    assert created.json()["checklist"] == [{"text": "Prima mossa", "done": False}]

    updated = client.patch(
        f"/workspace/{idea.id}",
        json={
            "stage": "building",
            "checklist": [{"text": "Prima mossa", "done": True}],
            "links": ["https://github.com/me/proto"],
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["stage"] == "building"
    assert body["checklist"][0]["done"] is True
    assert body["links"] == ["https://github.com/me/proto"]

    listing = client.get("/workspace").json()
    assert [e["idea_id"] for e in listing] == [idea.id]

    assert client.delete(f"/workspace/{idea.id}").status_code == 204
    assert client.get("/workspace").json() == []
    # L'idea nel radar non è stata toccata.
    assert session.get(Idea, idea.id) is not None


def test_workspace_rejects_bad_input(client: TestClient, session: Session) -> None:
    idea = _idea(session)
    client.post(f"/workspace/{idea.id}")
    res = client.patch(
        f"/workspace/{idea.id}", json={"links": ["javascript:alert(1)"]}
    )
    assert res.status_code == 422

    assert client.post("/workspace/9999").status_code == 404
    assert client.patch("/workspace/9999", json={"stage": "parked"}).status_code == 404


def test_delete_is_idempotent(client: TestClient, session: Session) -> None:
    assert client.delete("/workspace/9999").status_code == 204
