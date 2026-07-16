import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.github import GitHubSource
from app.sources.hackernews import HackerNewsSource


def _app_config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def test_hackernews_fetch_parses_stories_and_skips_non_story() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/topstories.json"):
            return httpx.Response(200, json=[1, 2])
        if path.endswith("/item/1.json"):
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "type": "story",
                    "title": "Un tool AI",
                    "url": "https://example.com",
                    "by": "alice",
                    "score": 120,
                    "descendants": 40,
                    "time": 1_700_000_000,
                },
            )
        if path.endswith("/item/2.json"):
            return httpx.Response(200, json={"id": 2, "type": "job", "title": "Job"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HackerNewsSource(
        SourceConfig(name="hn", type="hn", limit=10), _app_config(), Settings(), client=client
    )
    items = src.fetch()

    assert len(items) == 1
    item = items[0]
    assert item.source == "hn"
    assert item.external_id == "1"
    assert item.engagement_json == {"score": 120, "comments": 40}
    assert item.created_at is not None


def test_github_fetch_parses_repos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/repositories"
        assert "Authorization" in request.headers  # token passato
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 42,
                        "full_name": "owner/repo",
                        "html_url": "https://github.com/owner/repo",
                        "description": "un repo",
                        "owner": {"login": "owner"},
                        "stargazers_count": 500,
                        "forks_count": 30,
                        "watchers_count": 500,
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = GitHubSource(
        SourceConfig(name="gh", type="github", limit=10),
        _app_config(),
        Settings(github_token="tok"),
        client=client,
    )
    items = src.fetch()

    assert len(items) == 1
    item = items[0]
    assert item.source == "github"
    assert item.external_id == "42"
    assert item.engagement_json["stars"] == 500
    assert item.author == "owner"
    assert item.created_at is not None
