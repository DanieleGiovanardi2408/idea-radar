from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.api import app, get_db
from app.db import init_db
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, Topic, TopicStat


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


def _seed(session: Session, *, runs: int = 1) -> Idea:
    topic = Topic(label="Agenti AI")
    session.add(topic)
    session.commit()
    session.refresh(topic)

    item = Item(source="hn", external_id="1", title="Idea A", url="https://a.example")
    idea = Idea(label="Idea A", status=IdeaStatus.PROPOSED, topic_id=topic.id)
    idea.items = [item]
    session.add(idea)
    session.commit()
    session.refresh(idea)

    for n in range(runs):
        run = Run(n_items=1, n_ideas_proposed=1, status=RunStatus.DONE, phase="completato")
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
                composite=0.8 + n * 0.01,
                why_text="perché è interessante",
            )
        )
        session.add(
            TopicStat(
                topic_id=topic.id, run_id=run.id, n_ideas=1, n_items=1, avg_composite=0.8
            )
        )
        session.commit()
    return idea


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ideas_empty(client: TestClient) -> None:
    assert client.get("/ideas").json() == []


def test_ideas_returns_scored_idea_with_topic(client: TestClient, session: Session) -> None:
    _seed(session)
    data = client.get("/ideas").json()
    assert len(data) == 1
    assert data[0]["label"] == "Idea A"
    assert data[0]["composite"] == 0.8
    assert data[0]["topic_label"] == "Agenti AI"
    assert data[0]["n_items"] == 1
    assert data[0]["items"][0]["source"] == "hn"


def test_ideas_filters(client: TestClient, session: Session) -> None:
    idea = _seed(session)
    assert len(client.get("/ideas", params={"status": "proposed"}).json()) == 1
    assert client.get("/ideas", params={"status": "archived"}).json() == []
    assert len(client.get("/ideas", params={"topic_id": idea.topic_id}).json()) == 1
    assert client.get("/ideas", params={"topic_id": 999}).json() == []


def test_idea_detail_includes_history(client: TestClient, session: Session) -> None:
    idea = _seed(session, runs=3)
    data = client.get(f"/ideas/{idea.id}").json()
    assert data["label"] == "Idea A"
    assert len(data["history"]) == 3
    assert data["history"][0]["run_id"] < data["history"][-1]["run_id"]


def test_idea_detail_404(client: TestClient) -> None:
    assert client.get("/ideas/999").status_code == 404


def test_topics_endpoint(client: TestClient, session: Session) -> None:
    _seed(session)
    topics = client.get("/topics").json()
    assert len(topics) == 1
    assert topics[0]["label"] == "Agenti AI"
    assert topics[0]["n_ideas"] == 1
    assert topics[0]["n_proposed"] == 1


def test_trends_endpoint(client: TestClient, session: Session) -> None:
    _seed(session, runs=2)
    trends = client.get("/trends").json()
    assert len(trends) == 1
    assert len(trends[0]["points"]) == 2
    assert trends[0]["delta_ideas"] == 0  # stesso numero di idee tra i due run


def test_stats_endpoint(client: TestClient, session: Session) -> None:
    _seed(session)
    stats = client.get("/stats").json()
    assert stats["n_items"] == 1
    assert stats["n_ideas"] == 1
    assert stats["n_topics"] == 1
    assert stats["n_proposed"] == 1
    assert stats["items_by_source"] == {"hn": 1}
    assert stats["last_run"]["status"] == "done"


def test_runs_endpoints(client: TestClient, session: Session) -> None:
    _seed(session)
    runs = client.get("/runs").json()
    assert len(runs) == 1
    assert runs[0]["phase"] == "completato"
    assert client.get(f"/runs/{runs[0]['id']}").json()["id"] == runs[0]["id"]
    assert client.get("/runs/999").status_code == 404


def test_trigger_run_is_async(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr("app.api.execute_run", lambda: called.append(True))
    # Il lock vero è condiviso con CLI/scheduler: qui va isolato, altrimenti
    # un run reale in corso durante pytest farebbe fallire il test a caso.
    monkeypatch.setattr("app.api.run_lock_busy", lambda: False)
    resp = client.post("/runs")
    assert resp.status_code == 202
    assert resp.json()["started"] is True
    assert called == [True]  # BackgroundTasks lo esegue dopo la risposta


def test_trigger_run_declines_when_another_process_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il lock su file copre anche i run partiti da CLI o scheduler."""
    monkeypatch.setattr("app.api.run_lock_busy", lambda: True)
    resp = client.post("/runs")
    assert resp.status_code == 202
    assert resp.json()["started"] is False
