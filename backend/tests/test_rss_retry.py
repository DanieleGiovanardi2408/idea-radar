"""Test del comportamento di rete robusto: retry su 429, parsing Retry-After."""

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources import rss
from app.sources.rss import RssSource, _retry_after_seconds

RSS_2 = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Rivista</title>
    <item>
      <title>Articolo</title>
      <link>https://rivista.example/a</link>
      <guid>https://rivista.example/a</guid>
    </item>
  </channel>
</rss>
"""


def _app_config() -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _source(feeds: list[str], handler) -> RssSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RssSource(
        SourceConfig(name="riviste", type="rss", limit=10, feeds=feeds),
        _app_config(),
        Settings(),
        client=client,
    )


def test_retries_on_429_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(rss, "REQUEST_DELAY", 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text=RSS_2)

    items = _source(["https://rivista.example/feed"], handler).fetch()
    assert calls["n"] == 2  # primo 429, poi il retry va a buon fine
    assert len(items) == 1


def test_gives_up_after_max_retries_without_crashing(monkeypatch) -> None:
    monkeypatch.setattr(rss, "REQUEST_DELAY", 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    items = _source(["https://rivista.example/feed"], handler).fetch()
    assert calls["n"] == rss.MAX_RETRIES + 1  # 1 tentativo + MAX_RETRIES retry
    assert items == []  # 429 persistente: feed saltato, nessuna eccezione


def test_one_429_feed_does_not_kill_the_others(monkeypatch) -> None:
    monkeypatch.setattr(rss, "REQUEST_DELAY", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if "reddit" in str(request.url):
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text=RSS_2)

    src = _source(
        ["https://www.reddit.com/r/x/.rss", "https://rivista.example/feed"], handler
    )
    assert len(src.fetch()) == 1  # il feed buono passa comunque


def test_retry_after_seconds_parses_integer() -> None:
    resp = httpx.Response(429, headers={"Retry-After": "7"})
    assert _retry_after_seconds(resp) == 7.0


def test_retry_after_seconds_defaults_when_missing() -> None:
    resp = httpx.Response(429)
    assert _retry_after_seconds(resp) == rss.DEFAULT_RETRY_WAIT


def test_retry_after_seconds_parses_http_date() -> None:
    # Data nel passato -> attesa non negativa (clampata a 0).
    resp = httpx.Response(
        429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    )
    assert _retry_after_seconds(resp) == 0.0
