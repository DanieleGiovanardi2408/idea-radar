"""Query di lettura condivise tra API e CLI.

Ordinamento, filtri e paginazione stanno in SQL, non in Python: caricare
tutte le idee (o tutti gli score) in memoria per poi tagliarli funzionava
con dieci run, non con mesi di run schedulati.
"""

from collections import Counter, defaultdict

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, Topic, TopicStat


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


def top_ideas(
    session: Session,
    limit: int = 10,
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
    offset: int = 0,
    include_dismissed: bool = False,
    profile: str | None = None,
) -> list[tuple[Idea, Score | None]]:
    """Idee con il loro ultimo score: pinnate prima, poi composite decrescente.

    Senza filtro esplicito le ARCHIVED restano fuori: il Radar mostra il
    vivo; le archiviate si chiedono apposta con ``status=ARCHIVED``. Le idee
    scartate a mano (``dismissed_at``) restano fuori da OGNI vista finché non
    si chiede ``include_dismissed`` — un dismiss è una decisione dell'utente,
    non della pipeline.
    """
    subq = _latest_score_run_subq()
    stmt = (
        select(Idea, Score)
        .join(subq, subq.c.idea_id == Idea.id, isouter=True)
        .join(
            Score,
            (Score.idea_id == subq.c.idea_id) & (Score.run_id == subq.c.run_id),
            isouter=True,
        )
    )
    if status is None:
        stmt = stmt.where(Idea.status != IdeaStatus.ARCHIVED)
    else:
        stmt = stmt.where(Idea.status == status)
    if topic_id is not None:
        stmt = stmt.where(Idea.topic_id == topic_id)
    if profile is not None:
        # Il profilo vive sullo score (è il tema su cui il fit è stato misurato):
        # filtrarci significa "il radar visto da questo tema".
        stmt = stmt.where(Score.profile == profile)
    if not include_dismissed:
        stmt = stmt.where(Idea.dismissed_at.is_(None))  # type: ignore[union-attr]
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
