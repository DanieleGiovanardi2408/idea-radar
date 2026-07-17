"""Integrazione con launchd (macOS): genera e gestisce il LaunchAgent.

Filosofia: launchd resta STUPIDO — lancia ``idea-radar run --scheduled`` al
login e ogni mezz'ora; ogni decisione (saltare, preflight, lock) vive nella
CLI dove è testabile. Questo modulo si limita a: rendere il plist (funzione
pura, testata), scriverlo in ``~/Library/LaunchAgents`` e dire a ``launchctl``
di caricarlo o scaricarlo.

Perché launchd e non cron: cron su macOS SALTA i job mentre il Mac dorme;
launchd li coalizza e li esegue al risveglio. Con ``RunAtLoad`` +
``StartInterval`` + il gate di staleness si ottiene l'effetto anacron:
"gira ~ogni N ore quando il Mac è sveglio, recupera appena possibile".
I LaunchAgent girano solo a utente loggato: per un laptop personale coincide
con "Mac utilizzabile".
"""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from app.db import DATA_DIR

LABEL = "com.idea-radar.scheduled"
# Ogni quanto launchd TENTA (secondi). Non è la cadenza dei run: quella è
# scheduling.min_interval_hours in config.yaml. Un tentativo "fresco" costa
# ~1 secondo di uv run e lascia nel log il motivo del salto.
FIRE_INTERVAL_SECONDS = 1800
LOG_PATH = DATA_DIR / "logs" / "scheduled.log"
BACKEND_DIR = Path(__file__).resolve().parent.parent


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render_plist(
    *,
    uv_path: str,
    backend_dir: Path,
    log_path: Path,
    fire_interval_seconds: int = FIRE_INTERVAL_SECONDS,
) -> dict:
    """Contenuto del LaunchAgent come dict: puro, niente filesystem, testabile."""
    return {
        "Label": LABEL,
        # Path assoluto di uv: launchd parte con un PATH minimale, non c'è
        # né la shell di login né ~/.zshrc a risolverlo.
        "ProgramArguments": [uv_path, "run", "idea-radar", "run", "--scheduled"],
        # pydantic-settings cerca .env nella cwd: senza questa WorkingDirectory
        # il run schedulato perderebbe GITHUB_TOKEN e le override di Ollama.
        "WorkingDirectory": str(backend_dir),
        "RunAtLoad": True,  # al login/caricamento: il gate decide se lavorare
        "StartInterval": fire_interval_seconds,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",  # QoS da lavoro di fondo, non ruba priorità
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["launchctl", *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=["launchctl", *args],
            returncode=127,
            stdout="",
            stderr="launchctl non trovato (sistema non macOS?)",
        )


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def install(runner=_launchctl) -> Path:
    """Scrive il plist e lo (ri)carica in launchd. Ritorna il path del plist."""
    if sys.platform != "darwin":
        raise RuntimeError("launchd esiste solo su macOS")
    uv_path = shutil.which("uv")
    if not uv_path:
        raise RuntimeError(
            "uv non trovato nel PATH: nel plist serve il suo path assoluto"
        )
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            render_plist(uv_path=uv_path, backend_dir=BACKEND_DIR, log_path=LOG_PATH)
        )
    )
    # Reinstallazione idempotente: prima scarica l'eventuale versione caricata
    # (se non c'era, launchctl si lamenta e noi ignoriamo), poi carica la nuova.
    runner("bootout", f"{_gui_domain()}/{LABEL}")
    result = runner("bootstrap", _gui_domain(), str(path))
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "?"
        raise RuntimeError(f"launchctl bootstrap fallito: {detail}")
    return path


def uninstall(runner=_launchctl) -> bool:
    """Scarica il job e cancella il plist. True se c'era qualcosa da rimuovere."""
    if sys.platform != "darwin":
        raise RuntimeError("launchd esiste solo su macOS")
    runner("bootout", f"{_gui_domain()}/{LABEL}")
    path = plist_path()
    existed = path.exists()
    if existed:
        path.unlink()
    return existed


def status(runner=_launchctl) -> dict:
    """Stato del LaunchAgent: installato? caricato? ultimo exit code?"""
    path = plist_path()
    info: dict = {
        "plist": str(path),
        "installed": path.exists(),
        "loaded": False,
        "last_exit_code": None,
    }
    result = runner("print", f"{_gui_domain()}/{LABEL}")
    if result.returncode == 0:
        info["loaded"] = True
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("last exit code"):
                info["last_exit_code"] = stripped.split("=", 1)[-1].strip()
    return info
