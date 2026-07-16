from datetime import timedelta

from app.appconfig import AppConfig, ScoringConfig
from app.llm import IdeaInsight
from app.models import Difficulty, IdeaStatus, Item, utcnow
from app.scoring import score_item


def _cfg(threshold: float = 0.5) -> AppConfig:
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
            threshold=threshold,
        ),
    )


def _insight(difficulty: Difficulty | None = None) -> IdeaInsight:
    return IdeaInsight(summary="", why_text="", difficulty=difficulty)


def _github(stars: int, age_days: float, title: str = "ai devtools") -> Item:
    return Item(
        source="github",
        external_id=str(stars),
        title=title,
        author="owner",
        engagement_json={"stars": stars, "forks": 0},
        created_at=utcnow() - timedelta(days=age_days),
    )


def test_all_metrics_in_unit_range_and_fit_full_match() -> None:
    item = Item(
        source="hn",
        external_id="1",
        title="ai devtools per tutti",
        engagement_json={"score": 100, "comments": 20},
    )
    res = score_item(item, _insight(Difficulty.LOW), _cfg())
    for value in (
        res.heat,
        res.credibility,
        res.feasibility,
        res.opportunity,
        res.fit,
        res.composite,
    ):
        assert 0.0 <= value <= 1.0
    assert res.fit == 1.0  # entrambe le keyword presenti


def test_fit_ignores_substring_matches() -> None:
    """'ai' non deve matchare dentro 'certain'."""
    item = Item(source="hn", external_id="1", title="a certain plain story")
    assert score_item(item, _insight(), _cfg()).fit == 0.0


def test_difficulty_lowers_feasibility() -> None:
    item = Item(source="hn", external_id="1", title="qualcosa")
    low = score_item(item, _insight(Difficulty.LOW), _cfg())
    high = score_item(item, _insight(Difficulty.HIGH), _cfg())
    assert low.feasibility > high.feasibility


def test_rising_repo_beats_established_giant() -> None:
    """Il cuore del radar: 2k stelle in 90 giorni valgono più di 100k in 6 anni.

    Nota controintuitiva: la velocità *media di vita* NON basta a smascherare un
    gigante — n8n ha fatto ~45 stelle/giorno, più di un emergente a ~22/giorno,
    perché è cresciuto in fretta anche lui. A declassarlo è la saturazione:
    popolare **e** vecchio = mercato chiuso, quindi opportunity a zero.
    """
    rising = _github(stars=2_000, age_days=90)
    giant = _github(stars=100_000, age_days=2_200)

    rising_score = score_item(rising, _insight(Difficulty.LOW), _cfg())
    giant_score = score_item(giant, _insight(Difficulty.LOW), _cfg())

    assert giant_score.heat >= rising_score.heat  # il gigante è cresciuto in fretta
    assert giant_score.opportunity < 0.05  # ...ma è saturo: nessuno spazio
    assert rising_score.opportunity > 0.5
    assert rising_score.composite > giant_score.composite  # vince l'emergente


def test_young_repo_beats_old_repo_with_same_stars() -> None:
    """A parità di stelle, chi le ha fatte in meno tempo sta salendo di più."""
    young = _github(stars=3_000, age_days=100)
    old = _github(stars=3_000, age_days=1_800)
    assert (
        score_item(young, _insight(Difficulty.LOW), _cfg()).composite
        > score_item(old, _insight(Difficulty.LOW), _cfg()).composite
    )


def test_off_topic_is_crushed_even_if_popular() -> None:
    """fit è un moltiplicatore: fuori tema = abbattuta anche con tanta traction."""
    on_topic = _github(stars=2_000, age_days=60, title="ai devtools agent")
    off_topic = _github(stars=2_000, age_days=60, title="gioco di carte medievale")

    on = score_item(on_topic, _insight(Difficulty.LOW), _cfg())
    off = score_item(off_topic, _insight(Difficulty.LOW), _cfg())

    assert off.fit == 0.0
    assert off.composite < on.composite * 0.5


def test_threshold_controls_status() -> None:
    item = _github(stars=2_000, age_days=30)
    below = score_item(item, _insight(Difficulty.LOW), _cfg(threshold=0.0))
    assert below.status == IdeaStatus.PROPOSED
    above = score_item(item, _insight(Difficulty.HIGH), _cfg(threshold=1.1))
    assert above.status == IdeaStatus.PROCESSED
