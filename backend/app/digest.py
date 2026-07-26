"""Digest: cosa è cambiato sul radar da quando l'hai guardato l'ultima volta.

Il radar è uno strumento a consultazione: se non lo apri, i run schedulati
accumulano dati che nessuno legge. Il digest ribalta la cosa — un file markdown
con le idee **appena** promosse sopra soglia e i temi che stanno crescendo, che
il LaunchAgent può produrre da solo.

"Appena promosse" ha un significato preciso: non le idee viste per la prima
volta nella finestra (un'idea può esistere da settimane e superare la soglia
solo ora), ma quelle il cui **primo** punteggio sopra soglia cade nella
finestra. È la domanda a cui serve rispondere: *cosa è emerso*.

La finestra parte dall'ultimo digest scritto. Non serve una tabella per
ricordarlo: il nome dei file in ``data/digests/`` è già un registro, e usarlo
significa che cancellare un digest lo fa rigenerare — comportamento prevedibile.
"""

import re
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.appconfig import AppConfig
from app.models import Idea, IdeaStatus, Run, RunStatus, Score, utcnow
from app.queries import latest_scores, topic_trends
from app.sources.base import clean_html_text

DIGEST_DIR_NAME = "digests"
_STAMP_FORMAT = "%Y-%m-%d-%H%M"
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4})\.md$")


def digests_dir(data_dir: Path) -> Path:
    return data_dir / DIGEST_DIR_NAME


def last_digest_at(data_dir: Path) -> datetime | None:
    """Quando è stato scritto l'ultimo digest, dal nome dei file."""
    directory = digests_dir(data_dir)
    if not directory.is_dir():
        return None
    stamps = []
    for path in directory.iterdir():
        match = _STAMP_RE.match(path.name)
        if match:
            try:
                stamps.append(datetime.strptime(match.group(1), _STAMP_FORMAT))
            except ValueError:
                continue
    return max(stamps, default=None)


def _promoted_at(
    session: Session, idea_id: int, threshold: float
) -> datetime | None:
    """Quando l'idea ha superato la soglia per la PRIMA volta.

    Si guarda la storia dei punteggi, non ``first_seen``: un'idea può essere in
    archivio da settimane e salire sopra soglia soltanto adesso, ed è proprio
    quella la notizia.
    """
    first = session.exec(
        select(Run)
        .join(Score, Score.run_id == Run.id)
        .where(Score.idea_id == idea_id, Score.composite >= threshold)
        .order_by(Run.started_at)
    ).first()
    return first.started_at if first is not None else None


def newly_proposed(
    session: Session, config: AppConfig, since: datetime | None
) -> list[tuple[Idea, Score, datetime]]:
    """Idee promosse sopra soglia dopo ``since``, dalla migliore alla peggiore.

    Con ``since=None`` (primo digest) vale tutto ciò che è sopra soglia adesso.
    """
    threshold = config.scoring.threshold
    scores = latest_scores(session)
    found: list[tuple[Idea, Score, datetime]] = []
    for idea in session.exec(select(Idea)).all():
        score = scores.get(idea.id)
        if score is None or score.composite < threshold:
            continue
        if idea.status == IdeaStatus.ARCHIVED or idea.dismissed_at is not None:
            continue  # archiviate e scartate a mano non sono novità da leggere
        promoted = _promoted_at(session, idea.id, threshold)
        if promoted is None:
            continue
        if since is not None and promoted <= since:
            continue
        found.append((idea, score, promoted))
    found.sort(key=lambda row: row[1].composite, reverse=True)
    return found


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _theme_of(idea: Idea, min_ideas: int) -> str | None:
    """Il tema dell'idea, se è davvero un tema e non il titolo di un'altra idea.

    Un topic sotto ``topic_label_min_ideas`` non viene nominato dall'LLM ed erede
    il titolo della PRIMA idea che lo ha aperto — che spesso non è nemmeno questa.
    Stamparlo dà righe come "tema *Why I Built OpenAgentFlow: Decoupling
    Multi-Agent Workflows from Framework Boile*" (troncata a 80 caratteri) accanto
    a un'idea su n8n. Il criterio non è la forma della stringa ma la stessa regola
    che decide il naming: sotto quella soglia l'etichetta è ereditata, quindi non
    è un tema e non si stampa.
    """
    if idea.topic is None:
        return None
    if len(idea.topic.ideas) < max(min_ideas, 1):
        return None
    label = clean_html_text(idea.topic.label)
    if not label or label[:60] == clean_html_text(idea.label)[:60]:
        return None
    return label


def _format_window(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "primo digest: tutto ciò che è sopra soglia in archivio"
    hours = (now - since).total_seconds() / 3600.0
    if hours < 48:
        return f"dalle ultime {hours:.0f} ore (ultimo digest {since:%d/%m %H:%M})"
    return f"dagli ultimi {hours / 24:.0f} giorni (ultimo digest {since:%d/%m %H:%M})"


def render_digest(
    session: Session,
    config: AppConfig,
    *,
    since: datetime | None,
    now: datetime | None = None,
    max_ideas: int = 10,
    max_movers: int = 5,
) -> str:
    """Il digest in markdown. Nessuna scrittura: chi chiama decide dove metterlo."""
    now = now or utcnow()
    ideas = newly_proposed(session, config, since)
    # Anche tra i mover si scartano i "temi" che sono solo il titolo dell'idea
    # che li ha aperti: un gruppo da due idee non è un tema di cui dare notizia.
    min_named = max(config.clustering.topic_label_min_ideas, 1)
    trends = [
        t
        for t in topic_trends(session)
        if t["delta_ideas"] > 0 and t["n_ideas"] >= min_named
    ]
    trends.sort(key=lambda t: (t["delta_ideas"], t["avg_composite"]), reverse=True)
    n_runs = len(
        session.exec(select(Run).where(Run.status == RunStatus.DONE)).all()
    )

    lines = [
        f"# Idea Radar — digest del {now:%d/%m/%Y}",
        "",
        f"*{_format_window(since, now)} · {n_runs} run completati in archivio.*",
        "",
    ]

    lines.append("## Nuove idee sopra soglia")
    lines.append("")
    if not ideas:
        lines += [
            "Nessuna idea ha superato la soglia in questa finestra. Non è un "
            "guasto: il radar è selettivo per costruzione.",
            "",
        ]
    else:
        for idea, score, promoted in ideas[:max_ideas]:
            n_items = len(idea.items)
            lines.append(f"### {clean_html_text(idea.label)}")
            lines.append("")
            meta = [f"**{score.composite:.2f}**"]
            theme = _theme_of(idea, config.clustering.topic_label_min_ideas)
            if theme:
                meta.append(f"tema *{theme}*")
            meta.append(f"{n_items} {_plural(n_items, 'segnale', 'segnali')}")
            meta.append(f"sopra soglia dal {promoted:%d/%m}")
            lines.append(" · ".join(meta))
            lines.append("")
            # I collector ora ripuliscono l'HTML in ingresso, ma le righe già in
            # archivio se lo portano dietro: si ripulisce anche in lettura.
            if idea.summary:
                lines.append(clean_html_text(idea.summary))
                lines.append("")
            if score.why_text:
                lines.append(f"*Perché conta:* {clean_html_text(score.why_text)}")
                lines.append("")
            lines.append(
                f"Metriche: heat {score.heat:.2f} · credibilità "
                f"{score.credibility:.2f} · fattibilità {score.feasibility:.2f} · "
                f"opportunità {score.opportunity:.2f} · fit {score.fit:.2f}"
            )
            lines.append("")
            for item in idea.items[:4]:
                title = clean_html_text(item.title)
                if item.url:
                    lines.append(f"- [{title}]({item.url}) — {item.source}")
                else:
                    lines.append(f"- {title} — {item.source}")
            lines.append("")
        if len(ideas) > max_ideas:
            others = len(ideas) - max_ideas
            lines += [f"*(e altre {others} sopra soglia)*", ""]

    lines.append("## Temi in crescita")
    lines.append("")
    if not trends:
        lines += ["Nessun tema è cresciuto tra gli ultimi run.", ""]
    else:
        for trend in trends[:max_movers]:
            delta = trend["delta_ideas"]
            lines.append(
                f"- **{clean_html_text(trend['label'])}** — "
                f"+{delta} {_plural(delta, 'idea', 'idee')} "
                f"(ora {trend['n_ideas']}, composite medio "
                f"{trend['avg_composite']:.2f})"
            )
        lines.append("")

    return "\n".join(lines)


def write_digest(data_dir: Path, content: str, now: datetime | None = None) -> Path:
    """Salva il digest e restituisce il percorso. Il nome è anche il registro."""
    now = now or utcnow()
    directory = digests_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:{_STAMP_FORMAT}}.md"
    path.write_text(content, encoding="utf-8")
    return path
