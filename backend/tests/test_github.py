"""Collector GitHub: cerca repo GIOVANI, non i più famosi del mondo.

Il difetto originale è sopravvissuto a 51 run perché nessun test guardava la
query: ordinata per stelle e senza vincolo sulla nascita, restituiva sempre gli
stessi 31 repo (freeCodeCamp 452k stelle, tensorflow 196k), 22 creati prima del
2024 — l'opposto del "2k stelle in tre mesi" che il radar dice di cercare.
"""

from datetime import datetime, timezone

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.github import GitHubSource

TODAY = datetime(2026, 7, 27, tzinfo=timezone.utc)


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


def _source(handler, **cfg_overrides) -> GitHubSource:
    return GitHubSource(
        _cfg(**cfg_overrides),
        _app_config(),
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_query_constrains_the_birth_date() -> None:
    """Il vincolo senza cui la fonte cercava la cosa sbagliata."""
    src = GitHubSource(_cfg(min_stars=50), _app_config(), Settings())

    query = src.search_query("ai agents", (0, 90), today=TODAY)

    assert "created:2026-04-28..2026-07-27" in query  # ultimi 90 giorni
    assert "stars:>=50" in query
    assert '"ai agents"' in query  # frase intera, non parole sciolte


def test_bands_are_contiguous_and_start_from_today() -> None:
    """Fasce d'età da [90, 270, 540]: 0-90, 90-270, 270-540, senza buchi."""
    src = GitHubSource(_cfg(created_windows=[90, 270, 540]), _app_config(), Settings())
    assert src.age_bands() == [(0, 90), (90, 270), (270, 540)]

    # Valori disordinati o duplicati non devono produrre fasce assurde.
    messy = GitHubSource(_cfg(created_windows=[540, 90, 90, 270]), _app_config(), Settings())
    assert messy.age_bands() == [(0, 90), (90, 270), (270, 540)]


def test_one_query_per_profile_and_band() -> None:
    """Un gruppo di query per PROFILO, non per keyword.

    Coprire più fasce moltiplica lo spazio esplorato — con una fascia sola la
    stessa query ridà gli stessi repo a ogni run — ma una richiesta per keyword
    per fascia sfonderebbe il rate limit (18 keyword x 3 fasce = 54, su un
    limite di 30/minuto). Le keyword di un profilo vanno in OR: legittimo,
    perché sono sinonimi dello stesso tema.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(200, json={"items": []})

    _source(handler, created_windows=[90, 270, 540]).fetch()

    assert len(seen) == 3  # un profilo implicito x 3 fasce
    assert all('"ai agents" OR "self-hosted"' in q for q in seen)


def test_profiles_become_separate_query_groups() -> None:
    """Con più profili, ognuno ha la sua query: nessuno schiaccia gli altri.

    Era il difetto del vecchio OR globale, che mescolava temi diversi nella
    stessa domanda e lasciava vincere il termine più popolare.
    """
    from app.appconfig import ProfileConfig

    config = _app_config()
    config.profiles = [
        ProfileConfig(name="agenti", keywords=["ai agents", "mcp server"]),
        ProfileConfig(name="domotica", keywords=["home assistant"]),
    ]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(200, json={"items": []})

    GitHubSource(
        _cfg(created_windows=[90]),
        config,
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).fetch()

    assert len(seen) == 2  # due profili, una fascia
    assert any('"ai agents" OR "mcp server"' in q for q in seen)
    assert any('"home assistant"' in q and "ai agents" not in q for q in seen)


def test_the_quota_is_split_across_bands_not_won_by_the_oldest() -> None:
    """Le stelle si accumulano col tempo: ordinando tutto insieme vincerebbe
    sempre la fascia più vecchia, cioè il pregiudizio da cui si scappa."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        if "2026-04-28..2026-07-27" in query:  # fascia giovane: pochi stellati
            return httpx.Response(200, json={"items": [_repo(1, 300, "nuovo/uno")]})
        if "2025-10-30..2026-04-28" in query:  # fascia media
            return httpx.Response(200, json={"items": [_repo(2, 3_000, "medio/due")]})
        return httpx.Response(  # fascia vecchia: stelle a valanga
            200,
            json={"items": [_repo(10 + n, 90_000 - n, f"vecchio/{n}") for n in range(6)]},
        )

    items = _source(handler, limit=6, created_windows=[90, 270, 540]).fetch()
    names = [i.title for i in items]

    assert "nuovo/uno" in names  # il giovane NON viene schiacciato
    assert "medio/due" in names
    assert sum(1 for n in names if n.startswith("vecchio/")) == 2  # quota, non monopolio


def test_a_failing_query_does_not_kill_the_others() -> None:
    """Rate limit su un tema: gli altri devono comunque portare a casa roba."""
    from app.appconfig import ProfileConfig

    config = _app_config()
    config.profiles = [
        ProfileConfig(name="agenti", keywords=["ai agents"]),
        ProfileConfig(name="infra", keywords=["self-hosted"]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "ai agents" in request.url.params["q"]:
            return httpx.Response(403, json={"message": "rate limit"})
        return httpx.Response(200, json={"items": [_repo(7, 120, "d/quattro")]})

    items = GitHubSource(
        _cfg(created_windows=[540]),
        config,
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).fetch()

    assert [i.external_id for i in items] == ["7"]


def test_the_same_repo_is_not_collected_twice() -> None:
    """Un repo che compare in due keyword (o due fasce) conta una volta."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_repo(99, 500, "solo/uno")]})

    items = _source(handler, created_windows=[90, 270]).fetch()

    assert [i.external_id for i in items] == ["99"]


def test_limit_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        base = abs(hash(request.url.params["q"])) % 1000
        return httpx.Response(
            200,
            json={"items": [_repo(base * 10 + n, 900 - n, f"o/r{base}{n}") for n in range(8)]},
        )

    assert len(_source(handler, limit=5, created_windows=[90, 270, 540]).fetch()) <= 5


def test_item_carries_the_fields_the_scoring_needs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_repo(42, 2_000, "tizio/emergente")]})

    item = _source(handler, created_windows=[540]).fetch()[0]

    assert item.source == "github"
    assert item.title == "tizio/emergente"
    assert item.author == "tizio"
    assert item.engagement_json == {"stars": 2_000, "forks": 1, "watchers": 2_000}
    # created_at serve alla saturazione (popolare E vecchio = mercato chiuso)
    assert item.created_at == datetime(2026, 3, 1)
    assert item.created_at.tzinfo is None  # naive UTC, convenzione del progetto


def test_the_default_covers_a_young_band_and_a_wide_one() -> None:
    """Il default deve avere sia una fascia che si rinnova sia una che pesca a fondo."""
    bands = GitHubSource(_cfg(), _app_config(), Settings()).age_bands()
    assert bands[0][0] == 0 and bands[0][1] <= 120  # una fascia fresca
    assert bands[-1][1] >= 365  # e una che guarda indietro almeno un anno
