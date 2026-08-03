"""Il tavolo di lavoro: le idee salvate diventano un piano, non un segnalibro.

Un'idea entra in Sviluppo per scelta esplicita dell'utente. Da quel momento:
le sue mosse LLM diventano una checklist spuntabile, l'utente ci aggancia i
suoi collegamenti (repo, note, prototipi), e il radar — che continua a
lavorare per conto suo — riferisce cos'è successo DA QUANDO la segui: item
nuovi, engagement guadagnato, punteggio che si muove. È l'asse A al servizio
del progetto del singolo: il track record di ciò che hai scelto tu.

Stessa regola di pin e dismiss: la pipeline non tocca mai questa tabella.
"""

import logging
from datetime import datetime

from sqlmodel import Session, select

from app.models import (
    Idea,
    ItemStat,
    WorkspaceEntry,
    WorkspaceStage,
    utcnow,
)
from app.queries import latest_score_for

logger = logging.getLogger(__name__)

# Tetti difensivi: la checklist e i link sono testo dell'utente, ma un PATCH
# costruito male non deve poter gonfiare il DB.
MAX_CHECKLIST = 50
MAX_LINKS = 20
MAX_TEXT = 300


class WorkspaceError(ValueError):
    """Input non accettabile per il tavolo di lavoro (il chiamante fa il 4xx)."""


def normalize_checklist(raw: list) -> list[dict]:
    """La checklist in forma canonica: [{"text": str, "done": bool}], tetti inclusi."""
    out: list[dict] = []
    for entry in raw[:MAX_CHECKLIST]:
        if not isinstance(entry, dict):
            raise WorkspaceError("ogni voce della checklist è un oggetto {text, done}")
        text = str(entry.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            continue  # una voce vuota non è un to-do
        out.append({"text": text, "done": bool(entry.get("done"))})
    return out


def normalize_links(raw: list) -> list[str]:
    """Solo URL http(s), deduplicati, con un tetto."""
    out: dict[str, None] = {}
    for link in raw:
        url = str(link or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            raise WorkspaceError(f"non è un URL http(s): {url[:60]!r}")
        out.setdefault(url[:500], None)
    return list(out)[:MAX_LINKS]


def enter_workspace(session: Session, idea: Idea) -> WorkspaceEntry:
    """Porta un'idea in Sviluppo. Idempotente: se c'è già, restituisce quella.

    Le mosse LLM dell'idea diventano la checklist di partenza — sono già
    "2-3 azioni eseguibili questa settimana", cioè dei to-do nati per questo.
    Il composite del momento fa da baseline per l'attività.
    """
    existing = session.get(WorkspaceEntry, idea.id)
    if existing is not None:
        return existing
    score = latest_score_for(session, idea.id)
    entry = WorkspaceEntry(
        idea_id=idea.id,
        checklist_json=[{"text": move, "done": False} for move in (idea.moves_json or [])],
        composite_at_save=score.composite if score else 0.0,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def update_entry(
    session: Session,
    entry: WorkspaceEntry,
    *,
    stage: WorkspaceStage | None = None,
    checklist: list | None = None,
    links: list | None = None,
) -> WorkspaceEntry:
    if stage is not None:
        entry.stage = stage
    if checklist is not None:
        entry.checklist_json = normalize_checklist(checklist)
    if links is not None:
        entry.links_json = normalize_links(links)
    entry.updated_at = utcnow()
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def activity_since(session: Session, idea: Idea, since: datetime) -> dict:
    """Cos'è successo a un'idea da quando la segui.

    Tre numeri, tutti misurati: item nuovi agganciati dopo `since`, engagement
    guadagnato dalle osservazioni (somma dei delta per item nella finestra), e
    l'ultimo segnale visto. Il delta di punteggio lo fa il chiamante, che ha
    già l'ultimo score in mano.
    """
    items = list(idea.items)
    arrivati = sorted(
        (i for i in items if i.fetched_at and i.fetched_at > since),
        key=lambda i: i.fetched_at,
        reverse=True,
    )
    n_new_items = len(arrivati)

    item_ids = [i.id for i in items if i.id is not None]
    gained = 0.0
    if item_ids:
        stats = session.exec(
            select(ItemStat)
            .where(ItemStat.item_id.in_(item_ids))  # type: ignore[union-attr]
            .order_by(ItemStat.item_id, ItemStat.observed_at)
        ).all()
        by_item: dict[int, list[ItemStat]] = {}
        for stat in stats:
            by_item.setdefault(stat.item_id, []).append(stat)
        for series in by_item.values():
            # Baseline: l'ultima osservazione prima di `since` (o la prima in
            # finestra); il guadagno è quanto è salita l'ultima rispetto a lei.
            before = [s for s in series if s.observed_at <= since]
            after = [s for s in series if s.observed_at > since]
            if not after:
                continue
            base = before[-1].engagement if before else after[0].engagement
            gained += max(after[-1].engagement - base, 0.0)

    return {
        "n_new_items": n_new_items,
        "gained_engagement": round(gained, 1),
        "last_seen": idea.last_seen,
        # I titoli, non solo il conteggio: "il radar ha trovato QUESTO per te".
        # Tappati ai 5 più recenti — è un riassunto, il resto è nel dossier.
        "new_items": [
            {
                "title": i.title,
                "url": i.url,
                "source": i.source,
                "fetched_at": i.fetched_at,
            }
            for i in arrivati[:5]
        ],
    }


def workspace_overview(session: Session) -> list[dict]:
    """Tutte le voci del tavolo, con idea, punteggio attuale e attività."""
    rows = session.exec(
        select(WorkspaceEntry, Idea)
        .join(Idea, Idea.id == WorkspaceEntry.idea_id)
        .order_by(WorkspaceEntry.updated_at.desc())
    ).all()
    out: list[dict] = []
    for entry, idea in rows:
        score = latest_score_for(session, idea.id)
        composite = score.composite if score else 0.0
        out.append(
            {
                "idea_id": idea.id,
                "label": idea.label,
                "summary": idea.summary,
                "why_text": score.why_text if score else None,
                "profile": score.profile if score else None,
                "stage": entry.stage.value,
                "checklist": entry.checklist_json or [],
                "links": entry.links_json or [],
                "composite": composite,
                "composite_at_save": entry.composite_at_save,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "activity": activity_since(session, idea, entry.created_at),
            }
        )
    return out
