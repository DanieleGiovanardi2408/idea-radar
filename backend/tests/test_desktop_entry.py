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
