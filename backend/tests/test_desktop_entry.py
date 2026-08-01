"""L'entry point desktop: risoluzione dei path, senza avviare il server."""

from pathlib import Path

import pytest

import desktop_entry


def test_home_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDEA_RADAR_HOME", str(tmp_path / "casa"))
    assert desktop_entry.user_home_dir() == tmp_path / "casa"


@pytest.mark.parametrize(
    ("platform", "frammento"),
    [
        ("darwin", "Application Support"),
        ("win32", "Idea Radar"),
        ("linux", "idea-radar"),
    ],
)
def test_home_per_piattaforma(
    monkeypatch: pytest.MonkeyPatch, platform: str, frammento: str
) -> None:
    monkeypatch.delenv("IDEA_RADAR_HOME", raising=False)
    monkeypatch.setattr("sys.platform", platform)
    assert frammento in str(desktop_entry.user_home_dir())


def test_bundled_path_fuori_dal_pacchetto() -> None:
    """Senza PyInstaller (_MEIPASS assente) si risolve accanto all'entry point."""
    path = desktop_entry.bundled_path("config.yaml")
    assert path.name == "config.yaml"
    assert path.parent == Path(desktop_entry.__file__).resolve().parent


def test_port_busy_su_porta_libera() -> None:
    import socket

    # Una porta appena chiusa è libera: il check non deve dare falsi positivi.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert desktop_entry.port_busy(port) is False


def test_port_busy_su_porta_occupata() -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert desktop_entry.port_busy(port) is True


def test_pidfile_stantio_viene_rimosso_senza_uccidere(tmp_path: Path) -> None:
    """Porta libera + pidfile presente = avanzo di un'uscita sporca: si pulisce."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # chiusa subito: porta libera
    pidfile = tmp_path / "backend.pid"
    pidfile.write_text("99999999")
    desktop_entry.replace_previous_instance(tmp_path, port)
    assert not pidfile.exists()


def test_pidfile_corrotto_viene_rimosso(tmp_path: Path) -> None:
    pidfile = tmp_path / "backend.pid"
    pidfile.write_text("non-un-pid")
    desktop_entry.replace_previous_instance(tmp_path, 1)
    assert not pidfile.exists()
