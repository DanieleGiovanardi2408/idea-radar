"""Verdetti sulle proposte: il radar controlla le proprie previsioni.

Il radar sostiene di riconoscere le opportunità presto. Questo modulo è il
controllo di quella affermazione: per ogni idea promossa sopra soglia da
almeno ``outcomes.horizon_days`` giorni, guarda cos'è successo DOPO la
promozione e emette un verdetto — hit (ha continuato a crescere), flat
(viva ma ferma), miss (morta lì), na (non giudicabile).

La materia prima c'è già tutta: ``scores`` dice QUANDO ogni idea ha superato
la soglia per la prima volta; ``item_stats`` conserva l'engagement dei suoi
item a ogni osservazione. Il confronto è tra velocity (engagement/giorno)
prima e dopo la promozione, misurato SOLO su fonti live-counter — dove il
delta è crescita reale e non un'euristica (stessa regola della heat).

Il verdetto memorizza i numeri che lo motivano: un giudizio senza numeri
sarebbe un'opinione, e questo modulo esiste per toglierle di mezzo.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.appconfig import AppConfig, OutcomesConfig
from app.models import (
    Idea,
    IdeaOutcome,
    ItemStat,
    OutcomeVerdict,
    Run,
    RunStatus,
    Score,
    utcnow,
)
from app.sources.profiles import profile_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Observation:
    at: datetime
    engagement: float


def promotions(session: Session, threshold: float) -> dict[int, tuple[int, datetime]]:
    """idea_id -> (run_id, started_at) del PRIMO run in cui ha superato la soglia.

    Si usa la soglia di oggi su tutta la storia: se la soglia è cambiata nel
    tempo il momento esatto può slittare di un run, ma il criterio resta
    deterministico e uguale per tutte — meglio di un mosaico di soglie storiche
    che nessuno sa più ricostruire. Contano solo i run DONE.
    """
    stmt = (
        select(Score.idea_id, func.min(Score.run_id).label("run_id"))
        .join(Run, Run.id == Score.run_id)
        .where(Score.composite >= threshold, Run.status == RunStatus.DONE)
        .group_by(Score.idea_id)
    )
    rows = session.exec(stmt).all()
    run_ids = {run_id for _, run_id in rows}
    started = {
        run.id: run.started_at
        for run in session.exec(select(Run).where(Run.id.in_(run_ids))).all()  # type: ignore[union-attr]
    }
    return {
        idea_id: (run_id, started[run_id])
        for idea_id, run_id in rows
        if run_id in started
    }


def _velocity(
    first: _Observation, last: _Observation, min_span_hours: float
) -> float | None:
    """Engagement/giorno tra due osservazioni; None se il tratto è troppo corto."""
    span_hours = (last.at - first.at).total_seconds() / 3600.0
    if span_hours < min_span_hours:
        return None
    return (last.engagement - first.engagement) / (span_hours / 24.0)


def judge_idea(
    session: Session,
    idea: Idea,
    promoted_run_id: int,
    promoted_at: datetime,
    cfg: OutcomesConfig,
    now: datetime | None = None,
) -> IdeaOutcome:
    """Il verdetto su UNA idea, coi numeri che lo motivano.

    Regole, nell'ordine in cui si applicano:

    1. nessuna osservazione live-counter → ``na``;
    2. con una velocity "prima" misurabile: hit se il dopo ne conserva almeno
       ``hit_ratio``, miss se scende sotto ``miss_ratio`` E non sono arrivati
       item nuovi, flat altrimenti;
    3. senza un "prima" misurabile (promossa appena nata): si giudica in
       assoluto — hit se nell'orizzonte ha guadagnato ``min_abs_gain`` o ha
       attirato item nuovi, miss se non ha guadagnato nulla, flat in mezzo.
    """
    now = now or utcnow()
    horizon_end = promoted_at + timedelta(days=cfg.horizon_days)

    items = list(idea.items)
    live_ids = [
        item.id
        for item in items
        if item.id is not None and profile_for(item.source).live_counter
    ]
    n_new_items = sum(
        1 for item in items if item.fetched_at and item.fetched_at > promoted_at
    )

    # Le serie di osservazioni degli item live, spezzate su "promoted_at".
    stats = (
        session.exec(
            select(ItemStat)
            .where(ItemStat.item_id.in_(live_ids))  # type: ignore[union-attr]
            .order_by(ItemStat.item_id, ItemStat.observed_at)
        ).all()
        if live_ids
        else []
    )

    pre_velocity_total = 0.0
    post_velocity_total = 0.0
    gained_total = 0.0
    any_pre = False
    any_post = False

    by_item: dict[int, list[_Observation]] = {}
    for stat in stats:
        by_item.setdefault(stat.item_id, []).append(
            _Observation(at=stat.observed_at, engagement=stat.engagement)
        )

    for series in by_item.values():
        before = [o for o in series if o.at <= promoted_at]
        window = [o for o in series if promoted_at < o.at <= horizon_end]

        if len(before) >= 2:
            pre = _velocity(before[0], before[-1], cfg.min_span_hours)
            if pre is not None:
                pre_velocity_total += max(pre, 0.0)
                any_pre = True

        # Il "dopo" parte dall'ultima osservazione pre-promozione, se esiste:
        # è la baseline della previsione. Altrimenti dalla prima nell'orizzonte.
        post_series = ([before[-1]] if before else []) + window
        if len(post_series) >= 2:
            post = _velocity(post_series[0], post_series[-1], cfg.min_span_hours)
            if post is not None:
                post_velocity_total += max(post, 0.0)
                gained_total += max(
                    post_series[-1].engagement - post_series[0].engagement, 0.0
                )
                any_post = True

    if not any_post:
        verdict = OutcomeVerdict.NA
    elif any_pre and pre_velocity_total > 0:
        ratio = post_velocity_total / pre_velocity_total
        if ratio >= cfg.hit_ratio:
            verdict = OutcomeVerdict.HIT
        elif ratio <= cfg.miss_ratio and n_new_items == 0:
            verdict = OutcomeVerdict.MISS
        else:
            verdict = OutcomeVerdict.FLAT
    else:
        if gained_total >= cfg.min_abs_gain or n_new_items >= 2:
            verdict = OutcomeVerdict.HIT
        elif gained_total <= 0 and n_new_items == 0:
            verdict = OutcomeVerdict.MISS
        else:
            verdict = OutcomeVerdict.FLAT

    return IdeaOutcome(
        idea_id=idea.id,
        promoted_run_id=promoted_run_id,
        promoted_at=promoted_at,
        horizon_days=cfg.horizon_days,
        verdict=verdict,
        pre_velocity=round(pre_velocity_total, 3),
        post_velocity=round(post_velocity_total, 3),
        gained=round(gained_total, 1),
        n_new_items=n_new_items,
        computed_at=now,
    )


def compute_outcomes(
    session: Session,
    config: AppConfig,
    *,
    recompute: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    """Giudica le idee il cui orizzonte è completo. Ritorna i contatori.

    Idempotente: un'idea già giudicata non si ritocca (il passato non cambia),
    a meno di ``recompute`` — che serve dopo un cambio dei parametri di
    giudizio, per rileggere tutta la storia con le regole nuove.
    """
    now = now or utcnow()
    cfg = config.outcomes
    horizon = timedelta(days=cfg.horizon_days)

    promoted = promotions(session, config.scoring.threshold)
    already = {
        outcome.idea_id for outcome in session.exec(select(IdeaOutcome)).all()
    }
    if recompute:
        for outcome in session.exec(select(IdeaOutcome)).all():
            session.delete(outcome)
        session.commit()
        already = set()

    judged = 0
    skipped_pending = 0
    for idea_id, (run_id, promoted_at) in promoted.items():
        if idea_id in already:
            continue
        if now - promoted_at < horizon:
            skipped_pending += 1  # l'orizzonte non è ancora completo
            continue
        idea = session.get(Idea, idea_id)
        if idea is None:
            continue
        session.add(judge_idea(session, idea, run_id, promoted_at, cfg, now=now))
        judged += 1
    session.commit()

    total = len(session.exec(select(IdeaOutcome)).all())
    return {
        "judged_now": judged,
        "pending": skipped_pending,
        "total_judged": total,
    }


def outcomes_overview(session: Session, config: AppConfig) -> dict:
    """Il track record, aggregato per il pannello: numeri globali e ripartiti.

    L'hit-rate si calcola SOLO sulle idee giudicabili (na escluse): contare
    le non-giudicabili come errori punirebbe le fonti senza contatori, non
    la qualità delle previsioni. Oltre ai verdetti emessi, dice quante
    proposte sono in ATTESA d'orizzonte e quando matura la prima: un archivio
    giovane deve mostrare un conto alla rovescia, non un pannello vuoto.
    """
    rows = session.exec(
        select(IdeaOutcome, Idea)
        .join(Idea, Idea.id == IdeaOutcome.idea_id)
        .order_by(IdeaOutcome.promoted_at.desc())
    ).all()

    from app.queries import latest_scores

    latest = latest_scores(session)

    counts = {v.value: 0 for v in OutcomeVerdict}
    by_profile: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    ideas: list[dict] = []

    for outcome, idea in rows:
        verdict = outcome.verdict.value
        counts[verdict] += 1

        score = latest.get(idea.id)
        profile = score.profile if score and score.profile else None
        if profile:
            by_profile.setdefault(profile, {v.value: 0 for v in OutcomeVerdict})
            by_profile[profile][verdict] += 1

        # La fonte "dominante" dell'idea: quella con più item. Un'idea
        # multi-fonte conta per la sua voce più forte, non per tutte.
        sources = [item.source for item in idea.items]
        if sources:
            dominant = max(set(sources), key=sources.count)
            by_source.setdefault(dominant, {v.value: 0 for v in OutcomeVerdict})
            by_source[dominant][verdict] += 1

        ideas.append(
            {
                "idea_id": idea.id,
                "label": idea.label,
                "verdict": verdict,
                "promoted_at": outcome.promoted_at,
                "horizon_days": outcome.horizon_days,
                "pre_velocity": outcome.pre_velocity,
                "post_velocity": outcome.post_velocity,
                "gained": outcome.gained,
                "n_new_items": outcome.n_new_items,
                "profile": profile,
            }
        )

    judged_ids = {outcome.idea_id for outcome, _ in rows}
    horizon = timedelta(days=config.outcomes.horizon_days)
    waiting = [
        promoted_at
        for idea_id, (_, promoted_at) in promotions(
            session, config.scoring.threshold
        ).items()
        if idea_id not in judged_ids
    ]

    judgeable = counts["hit"] + counts["flat"] + counts["miss"]
    return {
        "counts": counts,
        "judgeable": judgeable,
        "hit_rate": (counts["hit"] / judgeable) if judgeable else None,
        "by_profile": by_profile,
        "by_source": by_source,
        "ideas": ideas,
        "pending": len(waiting),
        # Quando matura il primo verdetto in attesa: il conto alla rovescia.
        "first_due": (min(waiting) + horizon) if waiting else None,
    }
