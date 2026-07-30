"""Export delle idee in CSV, condiviso tra API (`/ideas?format=csv`) e CLI.

Una riga per idea, colonne piatte: quello che serve per aprire il radar in un
foglio di calcolo o darlo in pasto a uno script. Gli item non diventano righe
— l'unità del radar è l'idea — ma i loro URL viaggiano in un'unica colonna
separata da spazi, che è il formato più comodo da riespandere a valle.
"""

import csv
import io
from datetime import datetime

from app.models import Idea, Score

# L'ordine è un contratto: gli script a valle leggono per nome colonna, ma un
# umano che apre il file vuole label e score prima dei timestamp.
COLUMNS = [
    "id",
    "label",
    "status",
    "topic",
    "composite",
    "heat",
    "credibility",
    "feasibility",
    "opportunity",
    "fit",
    "profile",
    "difficulty",
    "n_items",
    "first_seen",
    "last_seen",
    "pinned",
    "dismissed_at",
    "seen_at",
    "note",
    "summary",
    "why",
    "urls",
]


def _dt(value: datetime | None) -> str:
    """ISO 8601 o vuoto: Excel e pandas li leggono entrambi senza aiuto."""
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


def _num(value: float | None) -> str:
    # 4 decimali: sotto c'è solo rumore dell'embedding, e i file restano diffabili.
    return f"{value:.4f}" if value is not None else ""


def idea_row(idea: Idea, score: Score | None) -> dict[str, str]:
    return {
        "id": str(idea.id),
        "label": idea.label,
        "status": idea.status.value,
        "topic": idea.topic.label if idea.topic else "",
        "composite": _num(score.composite if score else None),
        "heat": _num(score.heat if score else None),
        "credibility": _num(score.credibility if score else None),
        "feasibility": _num(score.feasibility if score else None),
        "opportunity": _num(score.opportunity if score else None),
        "fit": _num(score.fit if score else None),
        "profile": (score.profile or "") if score else "",
        "difficulty": (score.difficulty.value if score and score.difficulty else ""),
        "n_items": str(len(idea.items)),
        "first_seen": _dt(idea.first_seen),
        "last_seen": _dt(idea.last_seen),
        "pinned": "true" if idea.pinned else "false",
        "dismissed_at": _dt(idea.dismissed_at),
        "seen_at": _dt(idea.seen_at),
        "note": idea.note or "",
        "summary": idea.summary or "",
        "why": (score.why_text or "") if score else "",
        "urls": " ".join(it.url for it in idea.items if it.url),
    }


def ideas_to_csv(rows: list[tuple[Idea, Score | None]]) -> str:
    """CSV completo, header incluso. Il modulo `csv` gestisce quoting e newline

    nei campi liberi (note e summary possono contenere virgole e a capo).
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for idea, score in rows:
        writer.writerow(idea_row(idea, score))
    return buf.getvalue()
