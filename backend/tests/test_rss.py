import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.rss import RssSource

RSS_2 = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Rivista</title>
    <item>
      <title>Un &lt;b&gt;nuovo&lt;/b&gt; tool AI</title>
      <link>https://rivista.example/a</link>
      <description>&lt;p&gt;Un articolo su&lt;/p&gt; agenti AI</description>
      <guid>https://rivista.example/a</guid>
      <pubDate>Tue, 14 Jul 2026 10:00:00 GMT</pubDate>
      <author>redazione@rivista.example</author>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Post su un forum</title>
    <link href="https://forum.example/t/1"/>
    <id>tag:forum.example,2026:1</id>
    <summary>Discussione su self-hosted</summary>
    <published>2026-07-13T08:30:00Z</published>
    <author><name>utente</name></author>
  </entry>
</feed>
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


def test_parses_rss2_and_strips_html() -> None:
    src = _source(
        ["https://rivista.example/feed"],
        lambda r: httpx.Response(200, text=RSS_2),
    )
    items = src.fetch()
    assert len(items) == 1
    item = items[0]
    assert item.source == "rss"
    assert item.title == "Un nuovo tool AI"  # tag rimossi
    assert item.text == "Un articolo su agenti AI"
    assert item.url == "https://rivista.example/a"
    assert item.created_at is not None
    assert item.external_id  # hash stabile del guid


def test_parses_atom() -> None:
    src = _source(["https://forum.example/feed"], lambda r: httpx.Response(200, text=ATOM))
    items = src.fetch()
    assert len(items) == 1
    assert items[0].title == "Post su un forum"
    assert items[0].url == "https://forum.example/t/1"
    assert items[0].author == "utente"
    assert items[0].created_at is not None


def test_external_id_is_stable_across_fetches() -> None:
    handler = lambda r: httpx.Response(200, text=RSS_2)  # noqa: E731
    first = _source(["https://rivista.example/feed"], handler).fetch()[0]
    second = _source(["https://rivista.example/feed"], handler).fetch()[0]
    assert first.external_id == second.external_id


def test_broken_feed_does_not_kill_the_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "rotto" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=RSS_2)

    src = _source(["https://rotto.example/feed", "https://rivista.example/feed"], handler)
    items = src.fetch()
    assert len(items) == 1  # il feed buono passa comunque
