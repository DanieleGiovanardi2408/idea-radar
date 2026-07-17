"""Policy dei run non presidiati (schedulati): quando lavorare, quando saltare.

Il trigger (launchd) è volutamente stupido: spara al login e ogni mezz'ora,
sempre. TUTTA la decisione vive qui, dove si testa con pytest. Due guardie:

1. **Staleness** — si lavora solo se l'ultimo run DONE è più vecchio di
   ``scheduling.min_interval_hours``. Così "spara ogni 30 minuti" diventa
   "gira ~ogni N ore quando il Mac è sveglio", con recupero automatico dopo
   sonno o riavvio (comportamento alla anacron) e senza doppioni subito dopo
   un run manuale.
2. **Preflight Ollama** — un run non presidiato con Ollama giù non deve girare
   degradato: gli item entrati senza embedding diventano idee-singleton
   *definitive* (un item già assegnato a un'idea non viene mai ri-aggregato).
   In interattivo la degradazione va bene, c'è un umano che vede il warning;
   alle 7 di mattina no. Si salta, e il tick successivo ritenta gratis.
"""

import httpx
from sqlmodel import Session, select

from app.config import Settings
from app.models import Run, RunStatus, utcnow


def hours_since_last_done_run(session: Session) -> float | None:
    """Ore trascorse dall'avvio dell'ultimo run DONE; ``None`` se mai completato.

    Contano solo i run completati: un run FAILED non rende "fresco" il radar,
    così dopo un fallimento si ritenta al primo tick utile.
    """
    last = session.exec(
        select(Run)
        .where(Run.status == RunStatus.DONE)
        .order_by(Run.started_at.desc())
    ).first()
    if last is None:
        return None
    return (utcnow() - last.started_at).total_seconds() / 3600.0


def is_fresh(session: Session, min_interval_hours: float) -> tuple[bool, str]:
    """(fresco?, spiegazione) — fresco = l'ultimo run DONE è troppo recente."""
    hours = hours_since_last_done_run(session)
    if hours is None:
        return False, "nessun run completato in archivio"
    fresh = hours < min_interval_hours
    return fresh, (
        f"ultimo run completato {hours:.1f}h fa "
        f"({'<' if fresh else '>='} {min_interval_hours:g}h)"
    )


def ollama_preflight(
    settings: Settings, client: httpx.Client | None = None
) -> tuple[bool, str]:
    """(pronto?, spiegazione) — Ollama risponde e ha i due modelli richiesti.

    Usa ``/api/tags``: nessuna inference, millisecondi. Il confronto dei nomi
    tollera il tag implicito (``nomic-embed-text`` combacia con
    ``nomic-embed-text:latest``); un nome già taggato esige il match esatto,
    perché ``qwen2.5:7b`` non deve accontentarsi di ``qwen2.5:14b``.
    ``client`` è iniettabile per i test (httpx.MockTransport).
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        resp = client.get(f"{settings.ollama_host}/api/tags")
        resp.raise_for_status()
        available = {
            str(model.get("name", "")) for model in resp.json().get("models", [])
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return False, f"Ollama non raggiungibile su {settings.ollama_host} ({exc})"
    finally:
        if owns_client:
            client.close()

    def _present(wanted: str) -> bool:
        if ":" in wanted:
            return wanted in available
        return any(
            name == wanted or name.startswith(f"{wanted}:") for name in available
        )

    missing = [
        model
        for model in (settings.ollama_model, settings.embedding_model)
        if not _present(model)
    ]
    if missing:
        return False, (
            "modelli mancanti in Ollama: "
            + ", ".join(missing)
            + " (serve `ollama pull`)"
        )
    return True, "Ollama pronto"
