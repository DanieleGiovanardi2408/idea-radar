"""Collector npm: pacchetti nuovi, non react e lodash."""

from datetime import datetime, timedelta, timezone

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.base import create_source
from app.sources.npm import PROFILE, NpmSource
from app.sources.profiles import profile_for


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "self-hosted"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="npm", type="npm", limit=10, max_age_days=60)
    base.update(overrides)
    return SourceConfig(**base)


def _package(name: str, popularity: float, days_ago: int = 5) -> dict:
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "package": {
            "name": name,
            "description": "fa una cosa utile",
            "keywords": ["agent", "cli"],
            "date": published.isoformat().replace("+00:00", "Z"),
            "links": {"npm": f"https://www.npmjs.com/package/{name}"},
            "publisher": {"username": "tizio"},
        },
        "score": {"detail": {"popularity": popularity, "quality": 0.8, "maintenance": 0.9}},
    }


def _routing(search, downloads: dict[str, int] | None = None):
    """Handler che smista tra la search e i download (due endpoint diversi)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/downloads/point/" in request.url.path:
            name = request.url.path.split("/point/last-week/", 1)[1]
            value = (downloads or {}).get(name)
            if value is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"downloads": value, "package": name})
        return search(request)

    return handler


def _source(handler, **cfg_overrides) -> NpmSource:
    return NpmSource(
        _cfg(**cfg_overrides),
        _app_config(),
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_search_leans_on_quality_over_raw_popularity() -> None:
    """La popolarità pura premia i pacchetti già affermati.

    È la stessa idea del vincolo sulla data di nascita in GitHub, applicata con
    gli strumenti che npm mette a disposizione.
    """
    params = _source(_routing(lambda r: httpx.Response(200, json={'objects': []}))).search_params(
        "agent", 10
    )
    assert params["quality"] > params["popularity"]
    assert params["text"] == "agent"


def test_old_packages_are_dropped_however_popular() -> None:
    """react è popolarissimo e non è un'opportunità: la fonte esiste per questo."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "objects": [
                    _package("react", 0.99, days_ago=3_000),
                    _package("agente-nuovo", 0.2, days_ago=10),
                ]
            },
        )

    assert [i.title for i in _source(_routing(handler)).fetch()] == ["agente-nuovo"]


def test_the_age_window_follows_the_config() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"objects": [_package("tizio-cli", 0.3, days_ago=45)]})

    assert _source(_routing(handler), max_age_days=30).fetch() == []
    assert len(_source(_routing(handler), max_age_days=90).fetch()) == 1


def test_the_engagement_is_weekly_downloads_not_the_search_scores() -> None:
    """Regressione: i punteggi della search sono 1.0 per TUTTI, sui dati veri.

    La prima versione usava `score.detail.popularity`/`quality` e, combinandoli
    con una divisione per l'età, dava heat 1.00 a ogni pacchetto: npm si prendeva
    17 dei 55 posti sopra soglia con librerie affermatissime (`@docusaurus/core`,
    `appium`) spacciate per segnali in ascesa. I download settimanali sono invece
    un numero vero, sono già un tasso e crescono nel tempo.
    """
    assert profile_for("npm") is PROFILE
    assert PROFILE.live_counter is True  # i download crescono: delta misurabile
    assert PROFILE.velocity_per_age is False  # per settimana: già una velocità
    assert PROFILE.engagement_weights == {"downloads": 1.0}


def test_downloads_are_fetched_and_discriminate_between_packages() -> None:
    """Il punto di tutto l'esercizio: distinguere l'emergente dal sistema."""

    def search(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "objects": [
                    _package("emergente", 1.0, days_ago=20),
                    _package("libreria-di-sistema", 1.0, days_ago=20),
                ]
            },
        )

    handler = _routing(
        search, downloads={"emergente": 12_000, "libreria-di-sistema": 9_000_000}
    )
    items = {i.title: i for i in _source(handler).fetch()}

    assert items["emergente"].engagement_json["downloads"] == 12_000
    assert items["libreria-di-sistema"].engagement_json["downloads"] == 9_000_000

    from app.llm import IdeaInsight
    from app.models import Difficulty
    from app.scoring import score_item

    config = AppConfig(
        sources=[],
        keywords=["agent"],
        scoring=ScoringConfig(
            weights={"heat": 1.0, "credibility": 1.0, "feasibility": 1.0}, threshold=0.5
        ),
    )
    insight = IdeaInsight(summary="", why_text="", difficulty=Difficulty.MED)
    emergente = score_item(items["emergente"], insight, config)
    sistema = score_item(items["libreria-di-sistema"], insight, config)

    assert 0.3 < emergente.heat < 1.0  # slancio reale, non saturato
    assert sistema.opportunity < emergente.opportunity  # ed è un mercato chiuso
    assert sistema.composite < emergente.composite


def test_a_package_without_download_data_stays_at_zero() -> None:
    """Meglio una heat bassa che un numero inventato."""

    def search(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"objects": [_package("misterioso", 1.0)]})

    item = _source(_routing(search, downloads={})).fetch()[0]

    assert item.engagement_json["downloads"] == 0


def test_one_request_per_keyword_and_dedup() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["text"])
        return httpx.Response(200, json={"objects": [_package("condiviso", 0.4)]})

    items = _source(_routing(handler)).fetch()

    assert seen == ["ai agents", "self-hosted"]
    assert [i.title for i in items] == ["condiviso"]


def test_keywords_end_up_in_the_text_for_the_fit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"objects": [_package("qualcosa", 0.4)]})

    item = _source(_routing(handler)).fetch()[0]

    assert "[agent, cli]" in item.text
    assert item.author == "tizio"
    assert item.url == "https://www.npmjs.com/package/qualcosa"
    assert item.created_at is not None and item.created_at.tzinfo is None


def test_a_failing_keyword_does_not_kill_the_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["text"] == "ai agents":
            return httpx.Response(503)
        return httpx.Response(200, json={"objects": [_package("superstite", 0.5)]})

    assert [i.title for i in _source(_routing(handler)).fetch()] == ["superstite"]


def test_a_package_without_a_name_is_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"objects": [{"package": {}, "score": {}}]})

    assert _source(_routing(handler)).fetch() == []


def test_registered_in_the_source_registry() -> None:
    src = create_source(_cfg(), _app_config(), Settings())
    assert isinstance(src, NpmSource)
