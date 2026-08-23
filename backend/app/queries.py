"""Query di lettura condivise tra API e CLI.

Ordinamento, filtri e paginazione stanno in SQL, non in Python: caricare
tutte le idee (o tutti gli score) in memoria per poi tagliarli funzionava
con dieci run, non con mesi di run schedulati.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    Idea,
    IdeaStatus,
    Item,
    Run,
    RunStatus,
    Score,
    Topic,
    TopicStat,
    utcnow,
)


def _latest_score_run_subq():
    """Subquery (idea_id, run_id) dell'ULTIMO score di ogni idea."""
    return (
        select(Score.idea_id, func.max(Score.run_id).label("run_id"))
        .group_by(Score.idea_id)
        .subquery()
    )


def latest_scores(session: Session) -> dict[int, Score]:
    """Mappa idea_id -> Score del run più recente (una query, niente full scan)."""
    subq = _latest_score_run_subq()
    stmt = select(Score).join(
        subq,
        (Score.idea_id == subq.c.idea_id) & (Score.run_id == subq.c.run_id),
    )
    return {score.idea_id: score for score in session.exec(stmt).all()}


def latest_score_for(session: Session, idea_id: int) -> Score | None:
    """Ultimo score di UNA idea (per il dettaglio: inutile mappare tutto)."""
    return session.exec(
        select(Score)
        .where(Score.idea_id == idea_id)
        .order_by(Score.run_id.desc())
    ).first()


def _escape_like(text: str) -> str:
    r"""Il testo dell'utente come LITERALE dentro un LIKE: % e _ non sono jolly."""
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _ideas_stmt(
    stmt,
    *,
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
    include_dismissed: bool = False,
    profile: str | None = None,
    ungrouped: bool = False,
    q: str | None = None,
):
    """I filtri di ``/ideas``, applicati a una select qualunque (righe o COUNT).

    Estrarre i filtri in un punto solo è ciò che tiene onesto il conteggio
    totale: se lista e COUNT costruissero i WHERE ciascuno per conto proprio,
    prima o poi divergerebbero in silenzio.
    """
    subq = _latest_score_run_subq()
    stmt = stmt.join(subq, subq.c.idea_id == Idea.id, isouter=True).join(
        Score,
        (Score.idea_id == subq.c.idea_id) & (Score.run_id == subq.c.run_id),
        isouter=True,
    )
    if status is None:
        stmt = stmt.where(Idea.status != IdeaStatus.ARCHIVED)
    else:
        stmt = stmt.where(Idea.status == status)
    if topic_id is not None:
        stmt = stmt.where(Idea.topic_id == topic_id)
    if ungrouped:
        # Le idee che non stanno in nessun tema. Non sono un residuo da
        # nascondere: da quando un'idea sola non apre un topic, sono la
        # maggioranza dell'archivio, e la vista Topic deve poterle mostrare
        # invece di farle sparire.
        stmt = stmt.where(Idea.topic_id.is_(None))  # type: ignore[union-attr]
    if profile is not None:
        # Il profilo vive sullo score (è il tema su cui il fit è stato misurato):
        # filtrarci significa "il radar visto da questo tema".
        stmt = stmt.where(Score.profile == profile)
    if not include_dismissed:
        stmt = stmt.where(Idea.dismissed_at.is_(None))  # type: ignore[union-attr]
    if q:
        # Ricerca in SQL, non in Python: il frontend cercava solo nella pagina
        # caricata (100 su migliaia) e chiamava il risultato "la ricerca".
        # Si cerca dove guarda l'utente: etichetta, sommario e nome del tema.
        like = f"%{_escape_like(q.strip())}%"
        stmt = stmt.join(Topic, Topic.id == Idea.topic_id, isouter=True).where(
            Idea.label.ilike(like, escape="\\")  # type: ignore[attr-defined]
            | Idea.summary.ilike(like, escape="\\")  # type: ignore[attr-defined]
            | Topic.label.ilike(like, escape="\\")  # type: ignore[attr-defined]
        )
    return stmt


def top_ideas(
    session: Session,
    limit: int = 10,
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
    offset: int = 0,
    include_dismissed: bool = False,
    profile: str | None = None,
    ungrouped: bool = False,
    q: str | None = None,
) -> list[tuple[Idea, Score | None]]:
    """Idee con il loro ultimo score: pinnate prima, poi composite decrescente.

    Senza filtro esplicito le ARCHIVED restano fuori: il Radar mostra il
    vivo; le archiviate si chiedono apposta con ``status=ARCHIVED``. Le idee
    scartate a mano (``dismissed_at``) restano fuori da OGNI vista finché non
    si chiede ``include_dismissed`` — un dismiss è una decisione dell'utente,
    non della pipeline. ``q`` filtra su etichetta, sommario e nome del tema.
    """
    stmt = _ideas_stmt(
        select(Idea, Score),
        status=status,
        topic_id=topic_id,
        include_dismissed=include_dismissed,
        profile=profile,
        ungrouped=ungrouped,
        q=q,
    )
    stmt = (
        stmt.order_by(
            Idea.pinned.desc(),  # type: ignore[union-attr]
            func.coalesce(Score.composite, 0.0).desc(),
            Idea.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return [(idea, score) for idea, score in session.exec(stmt).all()]


def count_ideas(
    session: Session,
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
    include_dismissed: bool = False,
    profile: str | None = None,
    ungrouped: bool = False,
    q: str | None = None,
) -> int:
    """Quante idee passano gli STESSI filtri di ``top_ideas``, senza paginazione.

    Serve al frontend per dire "N di T" invece di spacciare la pagina caricata
    per il totale. I join sono al più 1:1 (ultimo score, topic), quindi il
    COUNT su id distinti non gonfia.
    """
    stmt = _ideas_stmt(
        select(func.count(func.distinct(Idea.id))),
        status=status,
        topic_id=topic_id,
        include_dismissed=include_dismissed,
        profile=profile,
        ungrouped=ungrouped,
        q=q,
    )
    return session.exec(stmt).one()


def idea_history(session: Session, idea_id: int) -> list[Score]:
    """Tutti gli score di un'idea, dal run più vecchio al più recente."""
    return list(
        session.exec(
            select(Score)
            .where(Score.idea_id == idea_id)
            .order_by(Score.run_id.asc())
        ).all()
    )


TOPIC_ORDERS = ("top_composite", "n_ideas", "last_seen")


def topics_overview(
    session: Session,
    *,
    min_ideas: int = 1,
    order_by: str = "top_composite",
) -> list[dict]:
    """Topic con numero di idee, item e composite medio dell'ultimo run.

    ``min_ideas`` nasconde i topic troppo piccoli per essere un tema: con le
    soglie tarate i topic sono centinaia e la maggioranza ha una sola idea, che
    è vero ma illeggibile da scorrere. ``order_by`` sceglie tra il punteggio
    migliore (default), la dimensione e la data dell'ultimo segnale.
    """
    latest = latest_scores(session)
    by_topic: dict[int, list[Idea]] = defaultdict(list)
    for idea in session.exec(select(Idea)).all():
        # Le archiviate e le scartate a mano non contano: i topic descrivono
        # ciò che è vivo ora (la storia resta nei TopicStat già scritti).
        if (
            idea.topic_id is not None
            and idea.status != IdeaStatus.ARCHIVED
            and idea.dismissed_at is None
        ):
            by_topic[idea.topic_id].append(idea)

    overview: list[dict] = []
    for topic in session.exec(select(Topic)).all():
        ideas = by_topic.get(topic.id, [])
        if len(ideas) < max(min_ideas, 1):
            continue
        # Il macro-tema del topic è quello della maggioranza delle sue idee. Un
        # topic PUÒ stare a cavallo di due profili ("agenti AI per il
        # self-hosting" appartiene sia agli agenti sia a dev-infra): la
        # maggioranza è una scelta, non una verità, ma dà una gerarchia leggibile
        # invece di 900 topic piatti.
        votes = Counter(
            latest[i.id].profile
            for i in ideas
            if i.id in latest and latest[i.id].profile
        )
        profile = votes.most_common(1)[0][0] if votes else None
        composites = [latest[i.id].composite for i in ideas if i.id in latest]
        overview.append(
            {
                "id": topic.id,
                "label": topic.label,
                "profile": profile,
                "n_ideas": len(ideas),
                "n_items": sum(len(i.items) for i in ideas),
                "n_proposed": sum(1 for i in ideas if i.status == IdeaStatus.PROPOSED),
                "avg_composite": (sum(composites) / len(composites)) if composites else 0.0,
                "top_composite": max(composites) if composites else 0.0,
                "first_seen": topic.first_seen,
                "last_seen": topic.last_seen,
            }
        )
    key = order_by if order_by in TOPIC_ORDERS else "top_composite"
    # A parità (frequentissima: tanti topic con lo stesso numero di idee) vince
    # il punteggio migliore, così l'ordine è stabile e utile.
    overview.sort(key=lambda t: (t[key], t["top_composite"]), reverse=True)
    return overview


def ideas_per_profile(session: Session) -> dict[str, int]:
    """Quante idee vive rappresenta ciascun profilo, secondo l'ultimo score."""
    subq = _latest_score_run_subq()
    stmt = (
        select(Score.profile, func.count())
        .join(
            subq,
            (Score.idea_id == subq.c.idea_id) & (Score.run_id == subq.c.run_id),
        )
        .join(Idea, Idea.id == Score.idea_id)
        .where(
            Idea.status != IdeaStatus.ARCHIVED,
            Idea.dismissed_at.is_(None),  # type: ignore[union-attr]
            Score.profile.is_not(None),  # type: ignore[union-attr]
        )
        .group_by(Score.profile)
    )
    return {name: count for name, count in session.exec(stmt).all()}


def ungrouped_per_profile(session: Session) -> dict[str, int]:
    """Quante idee vive di ciascun profilo non stanno in nessun tema.

    Il numero serve nell'intestazione del macro-tema: da quando un'idea sola non
    apre un topic, le non raggruppate sono la maggioranza dell'archivio, e la
    vista deve dire quante sono invece di lasciarle intuire per differenza —
    ``n_ideas`` del profilo e la somma dei topic si contano su insiemi diversi,
    quindi la sottrazione mentirebbe.
    """
    subq = _latest_score_run_subq()
    stmt = (
        select(Score.profile, func.count())
        .join(
            subq,
            (Score.idea_id == subq.c.idea_id) & (Score.run_id == subq.c.run_id),
        )
        .join(Idea, Idea.id == Score.idea_id)
        .where(
            Idea.status != IdeaStatus.ARCHIVED,
            Idea.dismissed_at.is_(None),  # type: ignore[union-attr]
            Idea.topic_id.is_(None),  # type: ignore[union-attr]
            Score.profile.is_not(None),  # type: ignore[union-attr]
        )
        .group_by(Score.profile)
    )
    return {name: count for name, count in session.exec(stmt).all()}


def signal_rhythm(session: Session, days: int = 28) -> dict:
    """Quando nascono i segnali: matrice giorno-della-settimana x ora.

    Si usa ``created_at`` — la nascita del segnale nel mondo — e non
    ``fetched_at``, che dice solo quando è passato il nostro scheduler e
    disegnerebbe il ritmo dei nostri run invece di quello della rete.

    Gli item senza data restano fuori: meglio una cella vuota che un'ora
    inventata. Le ore sono UTC, come tutto il resto del progetto.
    """
    since = utcnow() - timedelta(days=days)
    rows = session.exec(
        select(Item.created_at, Item.source).where(
            Item.created_at.is_not(None),  # type: ignore[union-attr]
            Item.created_at >= since,
        )
    ).all()

    # 7 giorni x 24 ore, lunedì = 0 (convenzione di weekday()).
    grid = [[0] * 24 for _ in range(7)]
    by_source: dict[str, int] = defaultdict(int)
    for created, source in rows:
        grid[created.weekday()][created.hour] += 1
        by_source[source] += 1

    counts = [n for row in grid for n in row]
    return {
        "days": days,
        "n_items": len(rows),
        "n_without_date": session.exec(
            select(func.count()).select_from(Item).where(
                Item.created_at.is_(None)  # type: ignore[union-attr]
            )
        ).one(),
        "grid": grid,
        "peak": max(counts) if counts else 0,
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
    }


def topic_trends(session: Session, max_runs: int = 12) -> list[dict]:
    """Serie storica per topic: come cresce (o cala) tra un run e l'altro.

    Un trend esiste solo con almeno due run: con un run solo le serie hanno un
    punto e la delta è nulla, per costruzione.

    Contano SOLO i run ``DONE``: un run fallito o ancora in corso non ha i suoi
    ``TopicStat`` e produrrebbe un cratere a zero in tutte le serie. Coi run
    schedulati — che falliranno ogni tanto senza nessuno a guardare — il caso
    passa da teorico a quotidiano.
    """
    runs = sorted(
        session.exec(select(Run).where(Run.status == RunStatus.DONE)).all(),
        key=lambda r: r.id or 0,
    )[-max_runs:]
    run_ids = [r.id for r in runs]
    run_by_id = {r.id: r for r in runs}

    stats_by_topic: dict[int, dict[int, TopicStat]] = defaultdict(dict)
    for stat in session.exec(select(TopicStat)).all():
        if stat.run_id in run_by_id:
            stats_by_topic[stat.topic_id][stat.run_id] = stat

    trends: list[dict] = []
    for topic in session.exec(select(Topic)).all():
        per_run = stats_by_topic.get(topic.id, {})
        if not per_run:
            continue
        points = [
            {
                "run_id": rid,
                "started_at": run_by_id[rid].started_at,
                "n_ideas": per_run[rid].n_ideas if rid in per_run else 0,
                "n_items": per_run[rid].n_items if rid in per_run else 0,
                "avg_composite": per_run[rid].avg_composite if rid in per_run else 0.0,
            }
            for rid in run_ids
        ]
        last = points[-1]
        prev = points[-2] if len(points) > 1 else None
        trends.append(
            {
                "topic_id": topic.id,
                "label": topic.label,
                "points": points,
                "n_ideas": last["n_ideas"],
                "avg_composite": last["avg_composite"],
                "delta_ideas": last["n_ideas"] - prev["n_ideas"] if prev else 0,
                "delta_composite": (
                    last["avg_composite"] - prev["avg_composite"] if prev else 0.0
                ),
            }
        )
    trends.sort(key=lambda t: (t["delta_ideas"], t["avg_composite"]), reverse=True)
    return trends


def monitor_stats(session: Session) -> dict:
    """Numeri per il monitor: imbuto di ingestione e stato delle fonti."""
    items = session.exec(select(Item)).all()
    ideas = session.exec(select(Idea)).all()
    topics = session.exec(select(Topic)).all()
    runs = sorted(session.exec(select(Run)).all(), key=lambda r: r.id or 0)

    by_source: dict[str, int] = defaultdict(int)
    for item in items:
        by_source[item.source] += 1

    last_run = runs[-1] if runs else None
    return {
        "n_items": len(items),
        "n_ideas": len(ideas),
        "n_topics": len(topics),
        "n_proposed": sum(1 for i in ideas if i.status == IdeaStatus.PROPOSED),
        "n_archived": sum(1 for i in ideas if i.status == IdeaStatus.ARCHIVED),
        "n_runs": len(runs),
        "items_by_source": dict(by_source),
        "last_run": last_run,
        "recent_runs": runs[-10:],
    }


def profile_anchors(
    session: Session,
    profile_names: list[str],
    per_profile: int = 3,
    max_chars: int = 800,
) -> dict[str, str]:
    """Per ogni tema, il testo di ciò che il radar ci ha davvero trovato.

    È l'ancoraggio del pannello video: la pertinenza di un risultato si misura
    contro le idee in cima a quel tema — label e sommario, cioè frasi vere —
    invece che contro la lista delle keyword, che come embedding vale poco
    (una fila di termini separati da virgole non è una frase, e la similarità
    finisce per misurare "sono entrambi testi tecnici in inglese").

    Un tema senza idee non compare nel risultato: il chiamante ripiega da sé
    sulle keyword, che al primo avvio sono tutto ciò che c'è.
    """
    anchors: dict[str, str] = {}
    for name in profile_names:
        parts: list[str] = []
        for idea, _score in top_ideas(session, limit=per_profile, profile=name):
            parts.append(idea.label)
            if idea.summary:
                parts.append(idea.summary[:200])
        text = " ".join(p.strip() for p in parts if p and p.strip())
        if text:
            anchors[name] = text[:max_chars]
    return anchors
