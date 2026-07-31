from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.api import app, get_db
from app.config import Settings
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


def test_ideas_search_q(client: TestClient, session: Session) -> None:
    """La ricerca è in SQL: etichetta, sommario e nome del tema, case-insensitive."""
    _seed(session)
    other = Idea(
        label="Sensore per serre", summary="monitoraggio umidità con LoRa"
    )
    session.add(other)
    session.commit()

    # Sull'etichetta, ignorando il maiuscolo/minuscolo.
    hits = client.get("/ideas", params={"q": "idea a"}).json()
    assert [i["label"] for i in hits] == ["Idea A"]
    # Sul sommario.
    hits = client.get("/ideas", params={"q": "umidità"}).json()
    assert [i["label"] for i in hits] == ["Sensore per serre"]
    # Sul nome del tema.
    hits = client.get("/ideas", params={"q": "agenti"}).json()
    assert [i["label"] for i in hits] == ["Idea A"]
    # Nessun match: lista vuota, non un errore.
    assert client.get("/ideas", params={"q": "zzz"}).json() == []
    # I jolly di LIKE sono testo literale, non wildcard.
    assert client.get("/ideas", params={"q": "%"}).json() == []


def test_ideas_total_count_header(client: TestClient, session: Session) -> None:
    """X-Total-Count dice il totale filtrato, non la dimensione della pagina."""
    _seed(session)
    for n in range(3):
        session.add(Idea(label=f"Extra {n}"))
    session.commit()

    res = client.get("/ideas", params={"limit": 2})
    assert len(res.json()) == 2
    assert res.headers["X-Total-Count"] == "4"
    # Il conteggio rispetta gli stessi filtri della lista.
    res = client.get("/ideas", params={"q": "extra"})
    assert res.headers["X-Total-Count"] == "3"


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


def _extra_topic(session: Session, label: str, n_ideas: int) -> Topic:
    topic = Topic(label=label)
    session.add(topic)
    session.commit()
    session.refresh(topic)
    for n in range(n_ideas):
        item = Item(source="hn", external_id=f"{label}-{n}", title=f"{label} {n}")
        idea = Idea(label=f"{label} {n}", topic_id=topic.id)
        idea.items = [item]
        session.add(idea)
    session.commit()
    return topic


def test_topics_can_hide_the_ones_with_too_few_ideas(
    client: TestClient, session: Session
) -> None:
    """Con le soglie tarate i topic sono centinaia e quasi tutti da una idea."""
    _seed(session)  # topic "Agenti AI" con 1 idea
    _extra_topic(session, "Tema grosso", n_ideas=3)

    assert len(client.get("/topics").json()) == 2
    filtered = client.get("/topics?min_ideas=2").json()
    assert [t["label"] for t in filtered] == ["Tema grosso"]


def test_topics_can_be_ordered_by_size(client: TestClient, session: Session) -> None:
    _seed(session)  # 1 idea, ma con uno score: vince per top_composite
    _extra_topic(session, "Tema grosso", n_ideas=3)  # nessuno score

    by_score = client.get("/topics").json()
    by_size = client.get("/topics?order_by=n_ideas").json()

    assert by_score[0]["label"] == "Agenti AI"
    assert by_size[0]["label"] == "Tema grosso"
    # Un ordinamento inventato non deve rompere nulla: si torna al default.
    assert client.get("/topics?order_by=inesistente").json() == by_score


def test_trends_endpoint(client: TestClient, session: Session) -> None:
    _seed(session, runs=2)
    trends = client.get("/trends").json()
    assert len(trends) == 1
    assert len(trends[0]["points"]) == 2
    assert trends[0]["delta_ideas"] == 0  # stesso numero di idee tra i due run


def test_rhythm_is_built_on_when_signals_were_born(
    client: TestClient, session: Session
) -> None:
    """Su `created_at`, non su `fetched_at`.

    `fetched_at` disegnerebbe il ritmo del NOSTRO scheduler — una riga verticale
    ogni quattro ore — invece di quello della rete.
    """
    from datetime import timedelta

    from app.models import utcnow

    now = utcnow()
    # Un lunedì alle 09, e un item raccolto ora ma nato mesi fa.
    monday_9 = now - timedelta(days=now.weekday(), hours=now.hour - 9)
    session.add(
        Item(
            source="hn",
            external_id="fresco",
            title="nato lunedì",
            created_at=monday_9,
        )
    )
    session.add(
        Item(source="hn", external_id="senza", title="senza data", created_at=None)
    )
    session.commit()

    data = client.get("/rhythm?days=28").json()

    assert data["n_items"] == 1  # quello senza data resta fuori
    assert data["n_without_date"] == 1
    assert len(data["grid"]) == 7 and len(data["grid"][0]) == 24
    assert data["grid"][monday_9.weekday()][9] == 1
    assert data["peak"] == 1
    assert data["by_source"] == {"hn": 1}


def test_rhythm_ignores_signals_older_than_the_window(
    client: TestClient, session: Session
) -> None:
    from datetime import timedelta

    from app.models import utcnow

    session.add(
        Item(
            source="hn",
            external_id="vecchio",
            title="di due mesi fa",
            created_at=utcnow() - timedelta(days=60),
        )
    )
    session.commit()

    assert client.get("/rhythm?days=28").json()["n_items"] == 0
    assert client.get("/rhythm?days=90").json()["n_items"] == 1


def test_videos_endpoint_says_when_the_key_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il pannello si spegne spiegandosi, non con un 500.

    La chiave va azzerata a mano: ``get_settings`` legge ``backend/.env``, quindi
    su una macchina che la chiave ce l'ha davvero questo test verificava il
    percorso opposto a quello che dichiara — e infatti falliva.
    """
    monkeypatch.setattr(
        "app.api.get_settings", lambda: Settings(youtube_api_key="")
    )
    data = client.get("/videos").json()

    assert data["configured"] is False
    assert data["videos"] == []
    assert "YOUTUBE_API_KEY" in data["detail"]


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


def test_ideas_default_hides_archived(client: TestClient, session: Session) -> None:
    """Il Radar mostra il vivo; le archiviate si chiedono con ?status=archived."""
    _seed(session)
    session.add(Idea(label="Spenta", status=IdeaStatus.ARCHIVED))
    session.commit()

    labels = [row["label"] for row in client.get("/ideas").json()]
    assert labels == ["Idea A"]

    shown = client.get("/ideas", params={"status": "archived"}).json()
    assert [row["label"] for row in shown] == ["Spenta"]


def test_stats_counts_archived(client: TestClient, session: Session) -> None:
    _seed(session)
    session.add(Idea(label="Spenta", status=IdeaStatus.ARCHIVED))
    session.commit()
    assert client.get("/stats").json()["n_archived"] == 1


def test_ideas_can_be_asked_for_the_ungrouped_ones(
    client: TestClient, session: Session
) -> None:
    """Da quando un'idea sola non apre un topic, le non raggruppate sono la
    maggioranza dell'archivio: la vista Topic deve poterle chiedere."""
    idea = _seed(session)  # ha un topic
    sola = Idea(label="Idea senza tema", status=IdeaStatus.PROPOSED, topic_id=None)
    sola.items = [Item(source="hn", external_id="9", title="Sola")]
    session.add(sola)
    session.commit()

    tutte = client.get("/ideas").json()
    senza = client.get("/ideas?ungrouped=true").json()

    assert {i["label"] for i in tutte} == {idea.label, "Idea senza tema"}
    assert [i["label"] for i in senza] == ["Idea senza tema"]
    assert all(i["topic_id"] is None for i in senza)
