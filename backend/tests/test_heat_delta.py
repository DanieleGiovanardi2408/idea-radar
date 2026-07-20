"""Heat "a delta": la velocità si MISURA tra osservazioni di ``item_stats``.

L'euristica engagement/età resta solo come cold start: premia i repo giovani
anche se fermi e punisce i vecchi anche se stanno esplodendo ora. Il delta
corregge entrambe le cose — e vale solo per le fonti con contatori vivi
(GitHub, HN): i feed RSS fotografano il valore alla pubblicazione, il loro
delta è zero per costruzione.
"""

import math
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Difficulty, Item, ItemStat, Score, utcnow
from app.pipeline import run_pipeline
from app.scoring import score_item


def _cfg() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai", "devtools"],
        scoring=ScoringConfig(
            weights={
                "heat": 0.25,
                "credibility": 0.25,
                "feasibility": 0.25,
                "opportunity": 0.25,
            },
            threshold=0.5,
        ),
    )


def _insight() -> IdeaInsight:
    return IdeaInsight(summary="", why_text="", difficulty=Difficulty.LOW)


def _github(stars: int, age_days: float) -> Item:
    return Item(
        source="github",
        external_id="repo",
        title="ai devtools",
        author="owner",
        engagement_json={"stars": stars, "forks": 0},
        created_at=utcnow() - timedelta(days=age_days),
    )


def _hn(score: int) -> Item:
    return Item(
        source="hn",
        external_id="story",
        title="ai devtools",
        engagement_json={"score": score, "comments": 0},
    )


def _obs(days_ago: float, engagement: float, run_id: int = 0) -> ItemStat:
    return ItemStat(
        item_id=1,
        run_id=run_id,
        engagement=engagement,
        observed_at=utcnow() - timedelta(days=days_ago),
    )


def _expected_heat(velocity: float, cap: float) -> float:
    """La stessa compressione logaritmica dello scoring, ricalcolata a mano."""
    return min(1.0, math.log10(1 + velocity) / math.log10(1 + cap))


def test_old_repo_exploding_now_gets_hot() -> None:
    """Il difetto dell'euristica: un repo di 2 anni che fa +30 stelle/giorno
    OGGI ha una media di vita bassa e restava freddo. Il delta lo vede."""
    item = _github(stars=1_200, age_days=400)
    cold = score_item(item, _insight(), _cfg())  # euristica: 3 stelle/giorno
    hot = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(2.5, 1_125, run_id=1), _obs(0.0, 1_200, run_id=2)],
    )
    assert hot.heat == pytest.approx(1.0)  # 30 stelle/giorno = cap GitHub
    assert hot.heat > cold.heat


def test_stalled_young_repo_cools_down() -> None:
    """Il difetto opposto: giovane con tante stelle ma FERMO da giorni.
    L'euristica gli darebbe heat piena, il delta lo raffredda."""
    item = _github(stars=3_000, age_days=30)
    heuristic = score_item(item, _insight(), _cfg())
    stalled = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(2.5, 3_000, run_id=1), _obs(0.0, 3_000, run_id=2)],
    )
    assert heuristic.heat == pytest.approx(1.0)  # 100 stelle/giorno di media
    assert stalled.heat == 0.0


def test_single_observation_falls_back_to_heuristic() -> None:
    item = _github(stars=900, age_days=90)
    baseline = score_item(item, _insight(), _cfg())
    with_one = score_item(
        item, _insight(), _cfg(), observations=[_obs(0.0, 900)]
    )
    # approx: le due chiamate a utcnow() distano microsecondi, l'età cambia.
    assert with_one.heat == pytest.approx(baseline.heat)


def test_observations_too_close_fall_back() -> None:
    """Sotto heat_min_span_hours il rapporto engagement/tempo è rumore:
    +3 stelle in 10 minuti sembrerebbero 432/giorno."""
    item = _github(stars=900, age_days=90)
    baseline = score_item(item, _insight(), _cfg())
    noisy = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(10 / 1440, 897, run_id=1), _obs(0.0, 900, run_id=2)],
    )
    assert noisy.heat == pytest.approx(baseline.heat)


def test_growth_outside_window_does_not_count() -> None:
    """Cresciuto un mese fa, fermo negli ultimi giorni: la finestra guarda
    solo la coda recente, niente rendita sulla storia passata."""
    item = _github(stars=1_010, age_days=60)
    res = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[
            _obs(30.0, 0, run_id=1),
            _obs(29.0, 1_000, run_id=2),  # il boom, fuori finestra
            _obs(2.0, 1_010, run_id=3),
            _obs(0.0, 1_010, run_id=4),
        ],
    )
    assert res.heat == 0.0


def test_negative_delta_clamps_to_zero() -> None:
    item = _hn(score=80)
    res = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(1.0, 120, run_id=1), _obs(0.0, 80, run_id=2)],
    )
    assert res.heat == 0.0


def test_hn_story_heat_is_measured_velocity() -> None:
    item = _hn(score=200)
    res = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(1.0, 50, run_id=1), _obs(0.0, 200, run_id=2)],
    )
    assert res.heat == pytest.approx(_expected_heat(150.0, 300.0))


def test_rss_keeps_heuristic_even_with_observations() -> None:
    """I contatori RSS non sono vivi: un delta lì non misura crescita reale."""
    item = Item(
        source="rss",
        external_id="post",
        title="ai devtools",
        engagement_json={"points": 40},
    )
    baseline = score_item(item, _insight(), _cfg())
    with_obs = score_item(
        item,
        _insight(),
        _cfg(),
        observations=[_obs(2.0, 10, run_id=1), _obs(0.0, 40, run_id=2)],
    )
    assert with_obs.heat == baseline.heat


# --- Integrazione: la pipeline passa la storia allo scoring -----------------


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(summary="s", why_text="w", difficulty=None)

    def topic_label(self, labels: list[str]) -> str:
        return "topic"


class FakeEmbedder:
    unavailable = False

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as s:
        yield s


def _pipeline_config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.4),
        clustering=ClusteringConfig(idea_threshold=0.99, topic_threshold=0.8),
    )


def _run(session: Session, items: list[Item]):
    return run_pipeline(
        session,
        _pipeline_config(),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )


def test_pipeline_scores_with_delta_heat(session: Session) -> None:
    """Due run sulla stessa storia HN: il secondo score usa la velocità
    misurata tra le due osservazioni, non l'engagement assoluto."""
    _run(
        session,
        [Item(source="hn", external_id="1", title="ai tool",
              engagement_json={"score": 100, "comments": 0})],
    )
    # Retrodata la prima osservazione: nei test i run distano millisecondi,
    # nella realtà ore — e sotto heat_min_span_hours il delta non scatta.
    first = session.exec(select(ItemStat)).one()
    first.observed_at = first.observed_at - timedelta(days=1)
    session.add(first)
    session.commit()

    second_run = _run(
        session,
        [Item(source="hn", external_id="1", title="ai tool",
              engagement_json={"score": 250, "comments": 10})],
    )

    score = session.exec(select(Score).where(Score.run_id == second_run.id)).one()
    # (260 - 100) / 1 giorno = 160/giorno, saturata sul cap HN (300).
    assert score.heat == pytest.approx(_expected_heat(160.0, 300.0))
