"""Collector GitHub: la query cerca repo GIOVANI, non i più famosi del mondo."""

from datetime import datetime, timedelta, timezone

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.github import GitHubSource


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "self-hosted"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="github", type="github", limit=10)
    base.update(overrides)
    return SourceConfig(**base)


def _repo(repo_id: int, stars: int, name: str = "tizio/progetto") -> dict:
    return {
        "id": repo_id,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": "un progetto",
        "owner": {"login": name.split("/")[0]},
        "stargazers_count": stars,
        "forks_count": 1,
        "watchers_count": stars,
        "created_at": "2026-03-01T00:00:00Z",
    }


def test_query_asks_for_recently_created_repos() -> None:
    """Il vincolo che mancava, e senza cui la fonte era inutile.

    Ordinare per stelle senza filtro sulla data restituisce freeCodeCamp e
    tensorflow: in 51 run la fonte ha raccolto 31 repo sempre uguali, 22 dei
    quali creati prima del 2024 — l'opposto del "2k stelle in tre mesi" che il
    radar dice di cercare.
    """
    src = GitHubSource(_cfg(created_within_days=365, min_stars=50), _app_config(), Settings())
    today = datetime(2026, 7, 27, tzinfo=timezone.utc)

    query = src.search_query("ai agents", today=today)

    assert 'created:>2025-07-27' in query  # esattamente un anno indietro
    assert "stars:>=50" in query
    assert '"ai agents"' in query  # frase intera, non parole sciolte


def test_the_window_follows_the_config() -> None:
    today = datetime(2026, 7, 27, tzinfo=timezone.utc)
    src = GitHubSource(_cfg(created_within_days=30), _app_config(), Settings())
    assert "created:>2026-06-27" in src.search_query("x", today=today)


def test_one_request_per_keyword_deduped_and_ranked() -> None:
    """Una query per keyword: ognuna porta i suoi emergenti.

    Con un'unica query in OR il termine più popolare schiaccia gli altri; e i
    repo che compaiono in due keyword non devono contare due volte.
    """
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(request.url.params["q"])
        if "ai agents" in seen_queries[-1]:
            return httpx.Response(
                200, json={"items": [_repo(1, 300, "a/uno"), _repo(2, 900, "b/due")]}
            )
        return httpx.Response(
            200, json={"items": [_repo(2, 900, "b/due"), _repo(3, 500, "c/tre")]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = GitHubSource(_cfg(), _app_config(), Settings(), client=client)

    items = src.fetch()

    assert len(seen_queries) == 2  # una per keyword
    assert [i.external_id for i in items] == ["2", "3", "1"]  # dedup + per stelle


def test_a_failing_keyword_does_not_kill_the_others() -> None:
    """Rate limit su una keyword: le altre devono comunque portare a casa roba."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "ai agents" in request.url.params["q"]:
            return httpx.Response(403, json={"message": "rate limit"})
        return httpx.Response(200, json={"items": [_repo(7, 120, "d/quattro")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = GitHubSource(_cfg(), _app_config(), Settings(), client=client)

    items = src.fetch()

    assert [i.external_id for i in items] == ["7"]


def test_limit_is_respected_across_keywords() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        base = 100 if "ai agents" in request.url.params["q"] else 200
        return httpx.Response(
            200,
            json={"items": [_repo(base + n, 1_000 - n, f"o/r{base + n}") for n in range(8)]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = GitHubSource(_cfg(limit=5), _app_config(), Settings(), client=client)

    assert len(src.fetch()) == 5


def test_item_carries_the_fields_the_scoring_needs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_repo(42, 2_000, "tizio/emergente")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = GitHubSource(_cfg(), _app_config(keywords=["ai agents"]), Settings(), client=client)

    item = src.fetch()[0]

    assert item.source == "github"
    assert item.title == "tizio/emergente"
    assert item.author == "tizio"
    assert item.engagement_json == {"stars": 2_000, "forks": 1, "watchers": 2_000}
    # created_at serve alla saturazione (popolare E vecchio = mercato chiuso)
    assert item.created_at == datetime(2026, 3, 1)
    assert item.created_at.tzinfo is None  # naive UTC, convenzione del progetto


def test_default_window_is_wide_enough_to_find_something() -> None:
    """Una finestra troppo stretta lascia il radar a secco: 18 mesi di default."""
    src = GitHubSource(_cfg(), _app_config(), Settings())
    today = datetime(2026, 7, 27, tzinfo=timezone.utc)
    cutoff = today - timedelta(days=src.cfg.created_within_days)
    assert 365 <= src.cfg.created_within_days <= 730
    assert cutoff.date().isoformat() in src.search_query("x", today=today)
