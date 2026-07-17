"""Lock cross-process dei run: CLI, API e scheduler non devono sovrapporsi.

L'API ha un ``threading.Lock``, ma vale solo dentro il suo processo: un run
schedulato (processo a sé) che parte mentre gira un ``idea-radar run`` manuale
scriverebbe su SQLite in parallelo a colpi di commit (``_progress`` committa a
ogni item) → "database is locked" e run morti a metà. Qui si usa un ``flock``
su file accanto al DB: vale per qualsiasi processo della macchina e il kernel
lo rilascia da solo quando il processo muore — niente lock stantii da pulire,
a differenza di un pidfile.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.db import DATA_DIR

LOCK_PATH = DATA_DIR / ".run.lock"


class RunLockBusy(RuntimeError):
    """Un altro processo sta già eseguendo la pipeline (o un recluster)."""


@contextmanager
def run_lock(path: Path | None = None) -> Iterator[None]:
    """Lock esclusivo e NON bloccante: chi arriva secondo riceve RunLockBusy.

    Niente coda, di proposito: un run accodato dietro un run appena finito
    rifarebbe subito lo stesso lavoro.
    """
    lock_path = path or LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunLockBusy("un run è già in corso") from exc
        yield
    finally:
        handle.close()  # chiudere il file rilascia il flock


def run_lock_busy(path: Path | None = None) -> bool:
    """Sonda il lock senza tenerlo: True se qualcun altro lo detiene adesso.

    Nota su flock: la sonda apre un SUO file descriptor, quindi chiuderlo non
    tocca il lock di chi lo detiene davvero (i lock sono per open file
    description, non per file).
    """
    try:
        with run_lock(path):
            return False
    except RunLockBusy:
        return True
