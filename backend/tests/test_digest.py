"""`digest`: cosa è emerso da quando hai guardato il radar l'ultima volta."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine
from typer.testing import CliRunner

from app import cli
from app.appconfig import AppConfig, ScoringConfig
from app.db import init_db
from app.digest import (
    last_digest_at,
    newly_proposed,
    render_digest,
    write_digest,
)
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, Topic, utcnow

NOW = datetime(2026, 7, 26, 12, 0)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _config(threshold: float = 0.6) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=threshold),
    )


def _run(session: Session, when: datetime) -> Run:
    run = Run(started_at=when, status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _idea(
    session: Session,
    label: str,
    *,
    topic: Topic | None = None,
    summary: str | None = None,
) -> Idea:
    item = Item(
        source="hn",
        external_id=label,
        title=label,
        url=f"https://example.com/{label}",
    )
    idea = Idea(
        label=label,
        summary=summary,
        status=IdeaStatus.PROPOSED,
        topic_id=topic.id if topic else None,
    )
    idea.items = [item]
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


def _score(
    session: Session, idea: Idea, run: Run, composite: float, why: str = "perché sì"
) -> None:
    session.add(
        Score(
            idea_id=idea.id,
            run_id=run.id,
            heat=0.5,
            credibility=0.5,
            feasibility=0.5,
            opportunity=0.5,
            fit=1.0,
            composite=composite,
            why_text=why,
        )
    )
    session.commit()


def test_new_means_newly_above_threshold_not_newly_seen(session: Session) -> None:
    """Il punto del digest: un'idea vecchia che sale ADESSO è una novità.

    Guardare ``first_seen`` avrebbe risposto alla domanda sbagliata.
    """
    old_run = _run(session, NOW - timedelta(days=10))
    recent_run = _run(session, NOW - timedelta(hours=2))

    veteran = _idea(session, "vecchia ma sale ora")
    _score(session, veteran, old_run, 0.20)  # sotto soglia dieci giorni fa
    _score(session, veteran, recent_run, 0.75)  # sopra soglia adesso

    already = _idea(session, "sopra soglia da sempre")
    _score(session, already, old_run, 0.80)
    _score(session, already, recent_run, 0.82)

    found = newly_proposed(session, _config(), since=NOW - timedelta(days=1))

    assert [i.label for i, _, _ in found] == ["vecchia ma sale ora"]


def test_the_first_digest_takes_everything_above_threshold(session: Session) -> None:
    run = _run(session, NOW - timedelta(days=3))
    for label, composite in (("alta", 0.9), ("bassa", 0.1)):
        _score(session, _idea(session, label), run, composite)

    found = newly_proposed(session, _config(), since=None)

    assert [i.label for i, _, _ in found] == ["alta"]


def test_archived_and_dismissed_are_not_news(session: Session) -> None:
    run = _run(session, NOW)
    archived = _idea(session, "archiviata")
    archived.status = IdeaStatus.ARCHIVED
    dismissed = _idea(session, "scartata")
    dismissed.dismissed_at = utcnow()
    session.add(archived)
    session.add(dismissed)
    session.commit()
    _score(session, archived, run, 0.9)
    _score(session, dismissed, run, 0.9)

    assert newly_proposed(session, _config(), since=None) == []


def test_render_includes_ideas_topics_and_links(session: Session) -> None:
    topic = Topic(label="Agenti AI")
    session.add(topic)
    session.commit()
    session.refresh(topic)
    run = _run(session, NOW)
    idea = _idea(session, "un runtime self-hosted", topic=topic, summary="Fa cose.")
    _score(session, idea, run, 0.77, why="risolve un problema vero")

    text = render_digest(session, _config(), since=None, now=NOW)

    assert "# Idea Radar — digest del 26/07/2026" in text
    assert "### un runtime self-hosted" in text
    assert "**0.77**" in text
    assert "Agenti AI" in text
    assert "Fa cose." in text
    assert "risolve un problema vero" in text
    assert "https://example.com/un runtime self-hosted" in text
    assert "1 segnale ·" in text  # singolare, non "1 segnali"


def test_render_survives_an_empty_window(session: Session) -> None:
    _run(session, NOW)
    text = render_digest(session, _config(), since=NOW, now=NOW)
    assert "Nessuna idea ha superato la soglia" in text
    assert "Nessun tema è cresciuto" in text


def test_render_strips_html_left_in_old_rows(session: Session) -> None:
    """I collector ora ripuliscono in ingresso, ma l'archivio se lo porta dietro."""
    run = _run(session, NOW)
    idea = _idea(
        session,
        "vecchia riga",
        summary="Vedi <p>https:&#x2F;&#x2F;esempio.it</p> per i dettagli",
    )
    _score(session, idea, run, 0.9)

    text = render_digest(session, _config(), since=None, now=NOW)

    assert "&#x2F;" not in text
    assert "<p>" not in text
    assert "https://esempio.it" in text


def test_the_filename_is_the_register(tmp_path: Path) -> None:
    """Niente tabella per ricordare l'ultimo digest: basta il nome del file."""
    assert last_digest_at(tmp_path) is None

    write_digest(tmp_path, "primo", now=datetime(2026, 7, 20, 8, 30))
    write_digest(tmp_path, "secondo", now=datetime(2026, 7, 25, 19, 5))

    assert last_digest_at(tmp_path) == datetime(2026, 7, 25, 19, 5)
    assert (tmp_path / "digests" / "2026-07-25-1905.md").read_text() == "secondo"


def test_stray_files_do_not_confuse_the_register(tmp_path: Path) -> None:
    write_digest(tmp_path, "vero", now=datetime(2026, 7, 20, 8, 30))
    (tmp_path / "digests" / "appunti.md").write_text("roba mia")
    (tmp_path / "digests" / "2026-13-99-9999.md").write_text("data impossibile")

    assert last_digest_at(tmp_path) == datetime(2026, 7, 20, 8, 30)


def test_cli_rejects_a_bad_date(monkeypatch) -> None:
    monkeypatch.setattr(cli, "init_db", lambda: None)
    result = CliRunner().invoke(cli.app, ["digest", "--since", "ieri"])
    assert result.exit_code == 2
    assert "Data non valida" in result.stdout
