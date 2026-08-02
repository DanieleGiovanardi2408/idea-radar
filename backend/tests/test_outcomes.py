"""Il giudice delle previsioni: hit/flat/miss/na su storie costruite a mano.

Ogni test fabbrica una storia completa — run, idea, item, osservazioni di
engagement — e verifica che il verdetto sia quello che un umano darebbe
guardando la stessa curva.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, OutcomesConfig, ScoringConfig
from app.db import init_db
from app.models import (
    Idea,
    IdeaOutcome,
    IdeaStatus,
    Item,
    ItemStat,
    OutcomeVerdict,
    Run,
    RunStatus,
    Score,
)
from app.outcomes import compute_outcomes, judge_idea, outcomes_overview, promotions

NOW = datetime(2026, 8, 1, 12, 0, 0)
PROMOTED = NOW - timedelta(days=40)  # orizzonte (30g) completo da 10 giorni


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _config(**overrides) -> AppConfig:
    return AppConfig(
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        outcomes=OutcomesConfig(**overrides),
    )


def _run(session: Session, at: datetime) -> Run:
    run = Run(started_at=at, finished_at=at, status=RunStatus.DONE, phase="completato")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _idea_promossa(
    session: Session,
    *,
    source: str = "github",
    composite: float = 0.8,
    promoted_at: datetime = PROMOTED,
) -> tuple[Idea, Run, Item]:
    """Un'idea con un item, promossa sopra soglia in un run a `promoted_at`."""
    run = _run(session, promoted_at)
    item = Item(
        source=source,
        external_id=f"x-{run.id}",
        title="repo che sale",
        fetched_at=promoted_at - timedelta(days=5),
    )
    idea = Idea(label="idea sotto esame", status=IdeaStatus.PROPOSED)
    idea.items = [item]
    session.add(idea)
    session.commit()
    session.refresh(idea)
    session.add(Score(idea_id=idea.id, run_id=run.id, heat=1, credibility=1,
                      feasibility=1, opportunity=1, fit=1, composite=composite))
    session.commit()
    return idea, run, item


def _osserva(session: Session, item: Item, at: datetime, engagement: float) -> None:
    """Un'osservazione di engagement, col suo run fittizio."""
    run = _run(session, at)
    session.add(ItemStat(item_id=item.id, run_id=run.id, engagement=engagement,
                         observed_at=at))
    session.commit()


def _serie(session: Session, item: Item, punti: list[tuple[int, float]]) -> None:
    """Osservazioni come (giorni rispetto alla promozione, engagement)."""
    for giorni, engagement in punti:
        _osserva(session, item, PROMOTED + timedelta(days=giorni), engagement)


def test_promotions_trova_il_primo_run_sopra_soglia(session: Session) -> None:
    idea, run, _ = _idea_promossa(session, composite=0.8)
    # Un run successivo con score più alto NON sposta la promozione.
    later = _run(session, PROMOTED + timedelta(days=1))
    session.add(Score(idea_id=idea.id, run_id=later.id, heat=1, credibility=1,
                      feasibility=1, opportunity=1, fit=1, composite=0.9))
    session.commit()

    promoted = promotions(session, threshold=0.5)
    assert promoted[idea.id] == (run.id, PROMOTED)


def test_hit_quando_la_crescita_continua(session: Session) -> None:
    idea, run, item = _idea_promossa(session)
    # Prima: 10 → 100 in 10 giorni (9/g). Dopo: continua allo stesso ritmo.
    _serie(session, item, [(-10, 10), (0, 100), (10, 190), (20, 280)])
    outcome = judge_idea(session, idea, run.id, PROMOTED, OutcomesConfig(), now=NOW)
    assert outcome.verdict == OutcomeVerdict.HIT
    assert outcome.pre_velocity > 0
    assert outcome.post_velocity > 0


def test_miss_quando_muore_li(session: Session) -> None:
    idea, run, item = _idea_promossa(session)
    # Prima cresceva; dopo la promozione: piatto encefalogramma.
    _serie(session, item, [(-10, 10), (0, 100), (10, 100), (20, 100), (29, 101)])
    outcome = judge_idea(session, idea, run.id, PROMOTED, OutcomesConfig(), now=NOW)
    assert outcome.verdict == OutcomeVerdict.MISS


def test_flat_quando_rallenta_ma_non_muore(session: Session) -> None:
    idea, run, item = _idea_promossa(session)
    # Da 9/g a ~2/g: sotto il 50% (niente hit), sopra il 10% (niente miss).
    _serie(session, item, [(-10, 10), (0, 100), (15, 130), (29, 160)])
    outcome = judge_idea(session, idea, run.id, PROMOTED, OutcomesConfig(), now=NOW)
    assert outcome.verdict == OutcomeVerdict.FLAT


def test_na_senza_fonti_live_counter(session: Session) -> None:
    idea, run, item = _idea_promossa(session, source="rss")
    _serie(session, item, [(-10, 10), (0, 100), (10, 500)])
    outcome = judge_idea(session, idea, run.id, PROMOTED, OutcomesConfig(), now=NOW)
    # rss non è live-counter: qualunque delta sarebbe inventato → non si giudica.
    assert outcome.verdict == OutcomeVerdict.NA


def test_senza_storia_pre_si_giudica_in_assoluto(session: Session) -> None:
    idea, run, item = _idea_promossa(session)
    # Promossa alla prima osservazione: nessun "prima". Guadagna 80 → hit.
    _serie(session, item, [(0, 20), (15, 60), (29, 100)])
    outcome = judge_idea(session, idea, run.id, PROMOTED, OutcomesConfig(), now=NOW)
    assert outcome.verdict == OutcomeVerdict.HIT
    assert outcome.gained == 80.0


def test_compute_salta_orizzonti_incompleti_ed_e_idempotente(
    session: Session,
) -> None:
    # Una giudicabile (40 giorni fa) e una troppo giovane (5 giorni fa).
    idea, _, item = _idea_promossa(session)
    _serie(session, item, [(-5, 10), (0, 50), (20, 120)])
    _idea_promossa(session, promoted_at=NOW - timedelta(days=5))

    config = _config()
    summary = compute_outcomes(session, config, now=NOW)
    assert summary == {"judged_now": 1, "pending": 1, "total_judged": 1}

    # Secondo giro: il passato non si rigiudica.
    summary = compute_outcomes(session, config, now=NOW)
    assert summary["judged_now"] == 0
    assert summary["total_judged"] == 1

    # recompute: si riparte da zero, stesso totale.
    summary = compute_outcomes(session, config, recompute=True, now=NOW)
    assert summary == {"judged_now": 1, "pending": 1, "total_judged": 1}
    assert len(session.exec(select(IdeaOutcome)).all()) == 1


def test_overview_aggrega_e_esclude_na_dallo_hit_rate(session: Session) -> None:
    # Una hit su github e una na su rss.
    idea_hit, run_hit, item_hit = _idea_promossa(session)
    _serie(session, item_hit, [(-10, 10), (0, 100), (20, 280)])
    idea_na, run_na, _ = _idea_promossa(session, source="rss")

    session.add(judge_idea(session, idea_hit, run_hit.id, PROMOTED,
                           OutcomesConfig(), now=NOW))
    session.add(judge_idea(session, idea_na, run_na.id, PROMOTED,
                           OutcomesConfig(), now=NOW))
    session.commit()

    overview = outcomes_overview(session, _config())
    assert overview["counts"]["hit"] == 1
    assert overview["counts"]["na"] == 1
    assert overview["judgeable"] == 1
    assert overview["hit_rate"] == 1.0
    assert overview["by_source"]["github"]["hit"] == 1
    assert len(overview["ideas"]) == 2
    # Tutte le promosse sono già giudicate: niente in attesa.
    assert overview["pending"] == 0
    assert overview["first_due"] is None
