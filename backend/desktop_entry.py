"""Entry point dell'app desktop: quello che PyInstaller impacchetta.

L'app Tauri lo avvia come processo interno (sidecar). Qui si risolve tutto
ciò che nel repo è implicito nel filesystem: dentro un eseguibile PyInstaller
``__file__`` punta a una dir temporanea scompattata a ogni avvio, quindi dati
e config devono vivere in una cartella utente persistente, dichiarata via
ambiente PRIMA di importare ``app.*`` (i moduli leggono i path all'import).
"""

import os
import shutil
import sys
from pathlib import Path


def user_home_dir() -> Path:
    """La cartella persistente dell'app, per piattaforma."""
    override = os.environ.get("IDEA_RADAR_HOME")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Idea Radar"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Idea Radar"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "idea-radar"


def bundled_path(name: str) -> Path:
    """Un file incluso nel pacchetto (config.yaml di default)."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def main() -> None:
    home = user_home_dir()
    home.mkdir(parents=True, exist_ok=True)

    # Il config di default viene copiato SOLO se manca: le modifiche
    # dell'utente (keyword, profili, soglie) sopravvivono agli aggiornamenti.
    config = home / "config.yaml"
    if not config.exists():
        shutil.copyfile(bundled_path("config.yaml"), config)

    os.environ.setdefault("IDEA_RADAR_DATA_DIR", str(home / "data"))
    os.environ.setdefault("IDEA_RADAR_CONFIG", str(config))
    # pydantic-settings legge `.env` dalla cwd: con la cwd sulla home dell'app,
    # un eventuale `.env` scritto lì (GITHUB_TOKEN, OLLAMA_*) viene raccolto.
    os.chdir(home)

    import uvicorn

    from app.api import app  # importato DOPO aver sistemato l'ambiente

    port = int(os.environ.get("IDEA_RADAR_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
