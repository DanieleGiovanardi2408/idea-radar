"""Query di lettura condivise tra API e CLI."""

from collections import defaultdict

from sqlmodel import Session, select

from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, Topic, TopicStat


def latest_scores(session: Session) -> dict[int, Score]:
    """Mappa idea_id -> Score del run più recente."""
    latest: dict[int, Score] = {}
    for score in session.exec(select(Score)).all():
        current = latest.get(score.idea_id)
        if current is None or score.run_id > current.run_id:
            latest[score.idea_id] = score
    return latest


def top_ideas(
    session: Session,
    limit: int = 10,
    status: IdeaStatus | None = None,
    topic_id: int | None = None,
) -> list[tuple[Idea, Score | None]]:
    """Idee ordinate per composite decrescente, con il loro ultimo score."""
    latest = latest_scores(session)
    rows = [
        (idea, latest.get(idea.id))
        for idea in session.exec(select(Idea)).all()
        if (status is None or idea.status == status)
        and (topic_id is None or idea.topic_id == topic_id)
    ]
    rows.sort(key=lambda r: r[1].composite if r[1] else 0.0, reverse=True)
    return rows[:limit]


def idea_history(session: Session, idea_id: int) -> list[Score]:
    """Tutti gli score di un'idea, dal run più vecchio al più recente."""
    scores = session.exec(select(Score).where(Score.idea_id == idea_id)).all()
    return sorted(scores, key=lambda s: s.run_id)


def topics_overview(session: Session) -> list[dict]:
    """Topic con numero di idee, item e composite medio dell'ultimo run."""
    latest = latest_scores(session)
    by_topic: dict[int, list[Idea]] = defaultdict(list)
    for idea in session.exec(select(Idea)).all():
        if idea.topic_id is not None:
            by_topic[idea.topic_id].append(idea)

    overview: list[dict] = []
    for topic in session.exec(select(Topic)).all():
        ideas = by_topic.get(topic.id, [])
        if not ideas:
            continue
        composites = [latest[i.id].composite for i in ideas if i.id in latest]
        overview.append(
            {
                "id": topic.id,
                "label": topic.label,
                "n_ideas": len(ideas),
                "n_items": sum(len(i.items) for i in ideas),
                "n_proposed": sum(1 for i in ideas if i.status == IdeaStatus.PROPOSED),
                "avg_composite": (sum(composites) / len(composites)) if composites else 0.0,
                "top_composite": max(composites) if composites else 0.0,
                "first_seen": topic.first_seen,
                "last_seen": topic.last_seen,
            }
        )
    overview.sort(key=lambda t: t["top_composite"], reverse=True)
    return overview


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
        "n_runs": len(runs),
        "items_by_source": dict(by_source),
        "last_run": last_run,
        "recent_runs": runs[-10:],
    }
