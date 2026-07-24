"""Ciclo di vita delle idee: l'archivio tiene il radar fresco.

Un'idea il cui ``last_seen`` non si muove da giorni è un segnale spento: le
fonti non la portano più. Resta nel DB con tutta la sua storia (score,
osservazioni, topic), ma esce dalle viste "vive". La de-archiviazione è
automatica e gratuita: se un item nuovo cade nell'idea, la pipeline aggiorna
``last_seen`` e la ri-scora — lo status torna processed/proposed da solo.
"""

import logging
from datetime import timedelta

from sqlmodel import Session, select

from app.models import Idea, IdeaStatus, utcnow

logger = logging.getLogger(__name__)


def archive_stale_ideas(session: Session, older_than_days: float) -> int:
    """Archivia le idee senza segnali da più di ``older_than_days`` giorni.

    Ritorna quante ne ha archiviate. Con ``older_than_days <= 0`` è spenta.
    Gira in coda a ogni run (già dentro il lock), quindi non serve schedularla
    a parte: finché il radar raccoglie, l'archivio si tiene da solo.
    Le idee PINNATE sono escluse: un pin è la dichiarazione esplicita
    dell'utente che quell'idea gli interessa, anche a segnali spenti.
    """
    if older_than_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=older_than_days)
    stale = session.exec(
        select(Idea).where(
            Idea.status != IdeaStatus.ARCHIVED,
            Idea.last_seen < cutoff,
            Idea.pinned == False,  # noqa: E712 — confronto SQL, non Python
        )
    ).all()
    for idea in stale:
        idea.status = IdeaStatus.ARCHIVED
        session.add(idea)
    session.commit()
    if stale:
        logger.info("Archiviate %d idee senza segnali recenti", len(stale))
    return len(stale)
