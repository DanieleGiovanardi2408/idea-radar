"""Collector Product Hunt: parsing GraphQL, token obbligatorio, errori GraphQL."""

import httpx
import pytest

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.base import create_source
from app.sources.producthunt import PROFILE, ProductHuntSource
from app.sources.profiles import profile_for


def _app_config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="producthunt", type="producthunt", limit=10)
    base.update(overrides)
    return SourceConfig(**base)


def _node(post_id: str, name: str, **overrides) -> dict:
    node = {
        "id": post_id,
        "name": name,
        "tagline": "Fa cose utili",
        "description": "Descrizione lunga del prodotto.",
        "votesCount": 120,
        "commentsCount": 15,
        "createdAt": "2026-07-22T09:00:00Z",
        "url": f"https://www.producthunt.com/posts/{name.lower()}",
        "website": f"https://{name.lower()}.example",
        "user": {"username": "maker"},
    }
    node.update(overrides)
    return node


def test_fetch_parses_posts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/api/graphql"
        assert request.headers["Authorization"] == "Bearer tok"  # token passato
        return httpx.Response(
            200,
            json={
                "data": {
                    "posts": {
                        "edges": [
                            {"node": _node("P1", "Toolone")},
                            {
                                "node": _node(
                                    "P2",
                                    "Tooltwo",
                                    votesCount=5,
                                    commentsCount=1,
                                    website=None,  # fallback sull'URL PH
                                )
                            },
                        ]
                    }
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = ProductHuntSource(
        _cfg(), _app_config(), Settings(producthunt_token="tok"), client=client
    )
    items = src.fetch()

    assert len(items) == 2
    item = items[0]
    assert item.source == "producthunt"
    assert item.external_id == "P1"
    assert item.title == "Toolone — Fa cose utili"  # nome + tagline
    assert item.url == "https://toolone.example"  # preferisce il sito del prodotto
    assert item.author == "maker"
    assert item.engagement_json == {"votes": 120, "comments": 15}
    assert item.created_at is not None
    assert item.created_at.tzinfo is None  # naive UTC, convenzione del progetto
    assert items[1].engagement_json == {"votes": 5, "comments": 1}
    assert items[1].url == "https://www.producthunt.com/posts/tooltwo"


def test_missing_token_raises_clear_error() -> None:
    # Niente client: l'errore deve scattare PRIMA di qualunque richiesta.
    src = ProductHuntSource(
        _cfg(), _app_config(), Settings(producthunt_token=None)
    )
    with pytest.raises(RuntimeError, match="PRODUCTHUNT_TOKEN"):
        src.fetch()


def test_graphql_errors_raise_with_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # GraphQL risponde 200 anche in errore: il problema sta nel body.
        return httpx.Response(
            200, json={"errors": [{"message": "invalid access token"}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = ProductHuntSource(
        _cfg(), _app_config(), Settings(producthunt_token="tok"), client=client
    )
    with pytest.raises(RuntimeError, match="invalid access token"):
        src.fetch()


def test_registry_knows_producthunt_and_profile_is_registered() -> None:
    src = create_source(_cfg(), _app_config(), Settings(producthunt_token="tok"))
    assert isinstance(src, ProductHuntSource)
    assert profile_for("producthunt") is PROFILE
