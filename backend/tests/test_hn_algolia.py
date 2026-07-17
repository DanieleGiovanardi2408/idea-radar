"""Collector HN-Algolia: backfill per finestra temporale, stessa source "hn"."""

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.base import create_source
from app.sources.hn_algolia import HnAlgoliaSource, algolia_params


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "self-hosted"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="hn-backfill", type="hn_algolia", limit=10)
    base.update(overrides)
    return SourceConfig(**base)


def _hit(object_id: int, title: str, points: int = 50, **overrides) -> dict:
    hit = {
        "objectID": str(object_id),
        "title": title,
        "url": f"https://example.com/{object_id}",
        "author": "alice",
        "points": points,
        "num_comments": 7,
        "created_at_i": 1_700_000_000,
    }
    hit.update(overrides)
    return hit


def test_algolia_params_encode_window_and_noise_floor() -> None:
    params = algolia_params("ai agents", since_epoch=123, min_points=5, limit=30)
    assert params["query"] == "ai agents"
    assert params["tags"] == "story"
    assert params["numericFilters"] == "created_at_i>123,points>=5"
    assert params["hitsPerPage"] == 30


def test_fetch_merges_keywords_and_dedups_by_id() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["query"])
        assert "points>=5" in request.url.params["numericFilters"]
        if request.url.params["query"] == "ai agents":
            hits = [_hit(1, "storia uno", points=80), _hit(2, "storia due", points=10)]
        else:
            hits = [_hit(2, "storia due", points=10), _hit(3, "storia tre", points=40)]
        return httpx.Response(200, json={"hits": hits})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HnAlgoliaSource(_cfg(), _app_config(), Settings(), client=client)
    items = src.fetch()

    assert calls == ["ai agents", "self-hosted"]
    # Dedup per objectID e ordinamento per punti decrescenti.
    assert [i.external_id for i in items] == ["1", "3", "2"]
    assert all(i.source == "hn" for i in items)  # si fonde con le top-story
    assert items[0].engagement_json == {"score": 80, "comments": 7}
    assert items[0].created_at is not None


def test_fetch_respects_limit_and_skips_bad_hits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    _hit(1, "a", points=30),
                    _hit(2, "", points=99),  # senza titolo: scartata
                    {"points": 5},  # senza objectID: scartata
                    _hit(3, "c", points=20),
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HnAlgoliaSource(
        _cfg(limit=1), _app_config(keywords=["ai"]), Settings(), client=client
    )
    items = src.fetch()
    assert [i.external_id for i in items] == ["1"]  # limit dopo l'ordinamento


def test_failed_keyword_does_not_kill_the_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["query"] == "ai agents":
            return httpx.Response(500)
        return httpx.Response(200, json={"hits": [_hit(9, "ok")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HnAlgoliaSource(_cfg(), _app_config(), Settings(), client=client)
    items = src.fetch()
    assert [i.external_id for i in items] == ["9"]


def test_hn_link_fallback_when_story_has_no_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [_hit(7, "ask hn", url=None)]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HnAlgoliaSource(
        _cfg(), _app_config(keywords=["ai"]), Settings(), client=client
    )
    items = src.fetch()
    assert items[0].url == "https://news.ycombinator.com/item?id=7"


def test_registry_knows_hn_algolia() -> None:
    src = create_source(_cfg(), _app_config(), Settings())
    assert isinstance(src, HnAlgoliaSource)
