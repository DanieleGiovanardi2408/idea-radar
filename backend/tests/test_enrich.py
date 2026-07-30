"""Enricher pypistats: trazione dei pacchetti citati, come canale separato."""

import httpx

from app.appconfig import AppConfig, EnrichmentConfig, ScoringConfig
from app.enrich import ENGAGEMENT_KEY, PyPIStatsEnricher, normalize_package, pypi_packages
from app.models import Item
from app.scoring import _heat
from app.sources.profiles import DEFAULT_PROFILE


def _item(url: str = "https://example.com", text: str | None = None, **kw) -> Item:
    return Item(source="rss", external_id=url, title="Un articolo", url=url, text=text, **kw)


def _enricher(handler, **cfg) -> PyPIStatsEnricher:
    return PyPIStatsEnricher(
        EnrichmentConfig(**cfg),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )


def _stats(last_week: int) -> httpx.Response:
    return httpx.Response(200, json={"data": {"last_week": last_week}})


# ---- Estrazione dei pacchetti -----------------------------------------------


def test_finds_packages_in_urls_and_pip_install() -> None:
    item = _item(
        url="https://pypi.org/project/Flask_Login",
        text="Per provarla: pip install fastapi. Niente da vedere qui.",
    )
    assert pypi_packages(item) == ["flask-login", "fastapi"]


def test_pep503_normalization_collapses_aliases() -> None:
    """Flask_Login e flask-login sono LO STESSO pacchetto: una richiesta sola."""
    assert normalize_package("Flask_Login") == "flask-login"
    assert normalize_package("zope.interface") == "zope-interface"
    item = _item(text="pip install Flask_Login oppure https://pypi.org/project/flask-login")
    assert pypi_packages(item) == ["flask-login"]


def test_pip_install_flags_are_not_package_names() -> None:
    item = _item(text="pip install -r requirements.txt")
    assert pypi_packages(item) == []


def test_an_item_without_citations_yields_nothing() -> None:
    assert pypi_packages(_item(text="un articolo qualsiasi su npm e cargo")) == []


# ---- Enrichment -------------------------------------------------------------


def test_enrich_writes_the_weekly_downloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/packages/httpx-toolbelt/recent" in str(request.url)
        return _stats(12_345)

    item = _item(text="pip install httpx_toolbelt")
    assert _enricher(handler).enrich([item]) == 1
    assert item.engagement_json[ENGAGEMENT_KEY] == 12_345


def test_the_cache_makes_one_request_per_package() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _stats(100)

    items = [_item(url=f"https://ex.com/{n}", text="pip install rich") for n in range(3)]
    assert _enricher(handler).enrich(items) == 3
    assert len(calls) == 1


def test_a_404_does_not_stop_the_others_and_is_not_retried() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "inventato" in str(request.url):
            return httpx.Response(404)
        return _stats(50)

    a = _item(url="https://ex.com/1", text="pip install inventato")
    b = _item(url="https://ex.com/2", text="pip install inventato")  # stesso 404
    c = _item(url="https://ex.com/3", text="pip install esistente")
    assert _enricher(handler).enrich([a, b, c]) == 1
    assert a.engagement_json is None or ENGAGEMENT_KEY not in (a.engagement_json or {})
    assert c.engagement_json[ENGAGEMENT_KEY] == 50
    assert sum("inventato" in u for u in calls) == 1  # il 404 è in cache


def test_the_budget_caps_the_requests_per_run() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _stats(10)

    items = [_item(url=f"https://ex.com/{n}", text=f"pip install pacchetto{n}") for n in range(5)]
    _enricher(handler, max_packages_per_run=2).enrich(items)
    assert len(calls) == 2


def test_the_switch_turns_everything_off() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nessuna richiesta deve partire")

    item = _item(text="pip install rich")
    assert _enricher(handler, pypi_downloads=False).enrich([item]) == 0


def test_with_multiple_packages_the_most_downloaded_wins() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _stats(90_000 if "grosso" in str(request.url) else 30)

    item = _item(text="pip install piccolo e pip install grosso")
    _enricher(handler).enrich([item])
    assert item.engagement_json[ENGAGEMENT_KEY] == 90_000


# ---- Canale separato: profili e scoring -------------------------------------


def test_the_blind_sum_of_profiles_skips_the_enricher_keys() -> None:
    """Sommare 12k download ai 3 punti di un feed gonfierebbe heat e saturazione."""
    engagement = {"points": 3, ENGAGEMENT_KEY: 12_000}
    assert DEFAULT_PROFILE.engagement(engagement) == 3.0


def _config(**enrichment) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        enrichment=EnrichmentConfig(**enrichment),
    )


def test_pypi_traction_heats_an_item_the_source_cannot_measure() -> None:
    freddo = _item(url="https://ex.com/a")
    caldo = _item(url="https://ex.com/b", engagement_json={ENGAGEMENT_KEY: 20_000})

    config = _config(pypi_week_cap=20_000)
    assert _heat(freddo, config) == 0.0
    assert _heat(caldo, config) == 1.0  # al cap, heat piena


def test_pypi_traction_never_cools_a_hot_item() -> None:
    """max, non media: il canale può solo accendere, mai abbassare."""
    item = _item(url="https://ex.com/c", engagement_json={"points": 500, ENGAGEMENT_KEY: 1})
    config = _config(pypi_week_cap=20_000)
    senza = _item(url="https://ex.com/d", engagement_json={"points": 500})
    assert _heat(item, config) >= _heat(senza, config)
