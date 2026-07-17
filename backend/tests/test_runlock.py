"""Il lock dei run vale tra PROCESSI, non solo tra thread.

Prima esisteva solo il ``threading.Lock`` dell'API: un ``idea-radar run``
manuale e un run schedulato potevano scrivere su SQLite in parallelo. Il
flock su file copre CLI, API e scheduler insieme e sparisce da solo quando
il processo muore (niente lock stantii).
"""

from pathlib import Path

import pytest

from app.runlock import RunLockBusy, run_lock, run_lock_busy


def test_lock_is_exclusive(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        with pytest.raises(RunLockBusy):
            with run_lock(lock):
                pass


def test_lock_is_released_on_exit(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with run_lock(lock):
        pass
    with run_lock(lock):  # il primo uso l'ha rilasciato: nessuna eccezione
        pass


def test_lock_is_released_even_on_error(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with run_lock(lock):
            raise ValueError("run esploso a metà")
    with run_lock(lock):  # niente lock stantio dopo un crash
        pass


def test_run_lock_busy_probe(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    assert run_lock_busy(lock) is False
    with run_lock(lock):
        assert run_lock_busy(lock) is True  # la sonda non ruba né rompe il lock
    assert run_lock_busy(lock) is False


def test_cli_run_reports_busy_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """`idea-radar run` con lock occupato: messaggio chiaro, exit code 1."""
    from typer.testing import CliRunner

    from app import cli

    def _busy(on_progress=None):
        raise RunLockBusy("un run è già in corso")

    monkeypatch.setattr(cli, "execute_run", _busy)
    result = CliRunner().invoke(cli.app, ["run"])
    assert result.exit_code == 1
    assert "già in corso" in result.output
