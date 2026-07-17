"""Il plist del LaunchAgent è GENERATO dal codice: il rendering si testa qui.

Le chiamate a ``launchctl`` restano un dettaglio iniettabile (``runner``):
nessun test tocca il launchd vero, che esiste solo su macOS.
"""

import plistlib
import subprocess
from pathlib import Path

import pytest

from app import schedule_launchd as sl


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_render_plist_shape() -> None:
    plist = sl.render_plist(
        uv_path="/opt/homebrew/bin/uv",
        backend_dir=Path("/repo/backend"),
        log_path=Path("/repo/backend/data/logs/scheduled.log"),
    )
    assert plist["Label"] == sl.LABEL
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/uv", "run", "idea-radar", "run", "--scheduled",
    ]
    # pydantic-settings cerca .env nella cwd: la WorkingDirectory è essenziale.
    assert plist["WorkingDirectory"] == "/repo/backend"
    assert plist["RunAtLoad"] is True  # recupero al login/riavvio
    assert plist["StartInterval"] == sl.FIRE_INTERVAL_SECONDS
    assert plist["StandardOutPath"] == plist["StandardErrorPath"]
    # Deve serializzare e rileggersi senza perdite (plist XML valido).
    assert plistlib.loads(plistlib.dumps(plist)) == plist


def test_install_writes_plist_and_bootstraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(*args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        return _completed(0)

    monkeypatch.setattr(sl.sys, "platform", "darwin")
    monkeypatch.setattr(sl.shutil, "which", lambda name: "/fake/uv")
    monkeypatch.setattr(sl, "LOG_PATH", tmp_path / "logs" / "scheduled.log")
    monkeypatch.setattr(
        sl, "plist_path", lambda: tmp_path / "agents" / f"{sl.LABEL}.plist"
    )

    written = sl.install(runner=runner)

    data = plistlib.loads(written.read_bytes())
    assert data["ProgramArguments"][0] == "/fake/uv"  # path assoluto risolto
    assert (tmp_path / "logs").is_dir()  # cartella log pronta prima del primo tick
    assert calls[0][0] == "bootout"  # reinstallazione idempotente: prima scarica…
    assert calls[1][0] == "bootstrap"  # …poi carica il plist nuovo


def test_install_fails_cleanly_if_bootstrap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sl.sys, "platform", "darwin")
    monkeypatch.setattr(sl.shutil, "which", lambda name: "/fake/uv")
    monkeypatch.setattr(sl, "LOG_PATH", tmp_path / "logs" / "s.log")
    monkeypatch.setattr(sl, "plist_path", lambda: tmp_path / "a" / "x.plist")

    def runner(*args: str) -> subprocess.CompletedProcess:
        return _completed(5 if args[0] == "bootstrap" else 0)

    with pytest.raises(RuntimeError, match="bootstrap"):
        sl.install(runner=runner)


def test_install_requires_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sl.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        sl.install()


def test_uninstall_removes_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plist = tmp_path / "x.plist"
    plist.write_bytes(b"x")
    monkeypatch.setattr(sl.sys, "platform", "darwin")
    monkeypatch.setattr(sl, "plist_path", lambda: plist)
    calls: list[tuple[str, ...]] = []

    def runner(*args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        return _completed(0)

    assert sl.uninstall(runner=runner) is True
    assert not plist.exists()
    assert calls[0][0] == "bootout"
    assert sl.uninstall(runner=runner) is False  # seconda volta: niente da fare


def test_status_parses_launchctl_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sl, "plist_path", lambda: tmp_path / "x.plist")

    def runner(*args: str) -> subprocess.CompletedProcess:
        return _completed(0, stdout="stuff\n\tlast exit code = 3\nmore")

    info = sl.status(runner=runner)
    assert info["installed"] is False  # plist non scritto in questo test
    assert info["loaded"] is True
    assert info["last_exit_code"] == "3"


def test_status_when_not_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sl, "plist_path", lambda: tmp_path / "x.plist")
    info = sl.status(runner=lambda *a: _completed(113))
    assert info["loaded"] is False
    assert info["last_exit_code"] is None
