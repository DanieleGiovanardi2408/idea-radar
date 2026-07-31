"""Guardie dei run schedulati: staleness gate e preflight Ollama.

La policy sta tutta in ``app.scheduling`` (e nel wiring di ``run --scheduled``):
launchd si limita a sparare ogni mezz'ora, quindi è QUESTO il codice che decide
la cadenza reale e che protegge il DB dai run degradati non presidiati.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, create_engine
from typer.testing import CliRunner

from app.appconfig import AppConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.models import Run, RunStatus, utcnow
from app.scheduling import hours_since_last_done_run, is_fresh, ollama_preflight


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _run(session: Session, status: RunStatus, hours_ago: float) -> Run:
    run = Run(
        status=status, started_at=utcnow() - timedelta(hours=hours_ago), phase="x"
    )
    session.add(run)
    session.commit()
    return run


# ---- staleness gate ---------------------------------------------------------


def test_no_runs_means_stale(session: Session) -> None:
    assert hours_since_last_done_run(session) is None
    fresh, why = is_fresh(session, 4.0)
    assert fresh is False
    assert "nessun run" in why


def test_recent_done_run_is_fresh(session: Session) -> None:
    _run(session, RunStatus.DONE, hours_ago=1.0)
    fresh, why = is_fresh(session, 4.0)
    assert fresh is True
    assert "1.0h" in why


def test_old_done_run_is_stale(session: Session) -> None:
    _run(session, RunStatus.DONE, hours_ago=7.0)
    fresh, _ = is_fresh(session, 4.0)
    assert fresh is False


def test_failed_run_does_not_count_as_fresh(session: Session) -> None:
    """Dopo un fallimento si ritenta al primo tick: conta solo l'ultimo DONE."""
    _run(session, RunStatus.DONE, hours_ago=7.0)
    _run(session, RunStatus.FAILED, hours_ago=0.1)
    fresh, _ = is_fresh(session, 4.0)
    assert fresh is False


# ---- preflight Ollama -------------------------------------------------------

_SETTINGS = Settings(
    ollama_model="qwen2.5:7b", embedding_model="nomic-embed-text"
)


def _tags_client(payload: dict | None = None, fail: bool = False) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("connessione rifiutata", request=request)
        return httpx.Response(200, json=payload or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_preflight_ok_with_both_models() -> None:
    client = _tags_client(
        {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text:latest"}]}
    )
    ready, why = ollama_preflight(_SETTINGS, client=client)
    assert ready is True
    assert "pronto" in why


def test_preflight_reports_missing_model() -> None:
    client = _tags_client({"models": [{"name": "qwen2.5:7b"}]})
    ready, why = ollama_preflight(_SETTINGS, client=client)
    assert ready is False
    assert "nomic-embed-text" in why


def test_preflight_tagged_name_requires_exact_match() -> None:
    """``qwen2.5:7b`` non deve accontentarsi di ``qwen2.5:14b``."""
    client = _tags_client(
        {"models": [{"name": "qwen2.5:14b"}, {"name": "nomic-embed-text:latest"}]}
    )
    ready, why = ollama_preflight(_SETTINGS, client=client)
    assert ready is False
    assert "qwen2.5:7b" in why


def test_preflight_unreachable_ollama() -> None:
    ready, why = ollama_preflight(_SETTINGS, client=_tags_client(fail=True))
    assert ready is False
    assert "non raggiungibile" in why


def test_preflight_checks_dedicated_insight_model() -> None:
    """Se OLLAMA_INSIGHT_MODEL è impostato, anche quel modello deve esserci:
    un run non presidiato senza non deve partire e degradare in silenzio."""
    settings = Settings(
        ollama_model="qwen2.5:7b",
        ollama_insight_model="qwen2.5:3b",
        embedding_model="nomic-embed-text",
    )
    client = _tags_client(
        {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text:latest"}]}
    )
    ready, why = ollama_preflight(settings, client=client)
    assert ready is False
    assert "qwen2.5:3b" in why
    # E non deve chiedere due volte lo stesso modello quando coincidono.
    same = Settings(
        ollama_model="qwen2.5:7b",
        ollama_insight_model="qwen2.5:7b",
        embedding_model="nomic-embed-text",
    )
    client = _tags_client({"models": [{"name": "nomic-embed-text:latest"}]})
    ready, why = ollama_preflight(same, client=client)
    assert ready is False
    assert why.count("qwen2.5:7b") == 1


# ---- wiring CLI: run --scheduled --------------------------------------------


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fresh: bool,
    preflight_ok: bool = True,
    execute_calls: list | None = None,
) -> None:
    from app import cli

    @contextmanager
    def _fake_session():
        yield None

    config = AppConfig(
        sources=[], keywords=[], scoring=ScoringConfig(weights={"heat": 1.0})
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "get_session", _fake_session)
    monkeypatch.setattr(cli, "get_config", lambda: config)
    monkeypatch.setattr(cli, "get_settings", lambda: _SETTINGS)
    monkeypatch.setattr(cli, "is_fresh", lambda session, hours: (fresh, "test"))
    monkeypatch.setattr(
        cli, "ollama_preflight", lambda settings: (preflight_ok, "test")
    )

    def _execute(on_progress=None):
        if execute_calls is not None:
            execute_calls.append({"on_progress": on_progress})
        return {
            "run_id": 1,
            "n_items": 2,
            "n_ideas_processed": 1,
            "n_ideas_proposed": 1,
            "n_topics": 1,
        }

    monkeypatch.setattr(cli, "execute_run", _execute)


def test_scheduled_skips_when_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import cli

    calls: list = []
    _patch_cli(monkeypatch, fresh=True, execute_calls=calls)
    result = CliRunner().invoke(cli.app, ["run", "--scheduled"])
    assert result.exit_code == 0
    assert "salto" in result.output
    assert calls == []  # la pipeline non è nemmeno partita


def test_scheduled_exits_3_when_ollama_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import cli

    calls: list = []
    _patch_cli(monkeypatch, fresh=False, preflight_ok=False, execute_calls=calls)
    result = CliRunner().invoke(cli.app, ["run", "--scheduled"])
    assert result.exit_code == 3  # exit code parlante per `schedule status`
    assert calls == []


def test_scheduled_runs_without_progress_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import cli

    calls: list = []
    _patch_cli(monkeypatch, fresh=False, preflight_ok=True, execute_calls=calls)
    result = CliRunner().invoke(cli.app, ["run", "--scheduled"])
    assert result.exit_code == 0
    assert calls == [{"on_progress": None}]  # niente \r nel log del LaunchAgent
    assert "completato" in result.output
