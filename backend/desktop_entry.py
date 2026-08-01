"""Entry point dell'app desktop: quello che PyInstaller impacchetta.

L'app Tauri lo avvia come processo interno (sidecar). Qui si risolve tutto
ciò che nel repo è implicito nel filesystem: dentro un eseguibile PyInstaller
``__file__`` punta a una dir temporanea scompattata a ogni avvio, quindi dati
e config devono vivere in una cartella utente persistente, dichiarata via
ambiente PRIMA di importare ``app.*`` (i moduli leggono i path all'import).
"""

import os
import shutil
import signal
import socket
import sys
import time
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


def port_busy(port: int) -> bool:
    """La porta è già occupata su 127.0.0.1?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def replace_previous_instance(home: Path, port: int) -> None:
    """Sostituisce un backend orfano rimasto sulla porta.

    Un force quit dell'app (⌘⌥Esc) non passa dal gestore d'uscita di Tauri:
    il sidecar sopravvive, tiene la porta, e l'avvio successivo muore in
    silenzio lasciando la UI a parlare con un processo VECCHIO (senza le
    route o l'ambiente nuovi). Il pidfile scritto a ogni avvio ci dice chi
    era il predecessore: se la porta è occupata, lo si termina e si prende
    il suo posto. Se la porta è libera il pidfile è solo stantio.
    """
    pidfile = home / "backend.pid"
    if not pidfile.exists():
        return
    try:
        pid = int(pidfile.read_text().strip())
    except ValueError:
        pidfile.unlink(missing_ok=True)
        return
    if pid == os.getpid() or not port_busy(port):
        pidfile.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):  # fino a ~5s perché la porta si liberi
            if not port_busy(port):
                break
            time.sleep(0.25)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # il PID non era più suo: meglio non insistere
    pidfile.unlink(missing_ok=True)


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
    replace_previous_instance(home, port)
    pidfile = home / "backend.pid"
    pidfile.write_text(str(os.getpid()))
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    finally:
        pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
