"""Collector arXiv: parsing Atom, costruzione della search_query, registry."""

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.arxiv import API_URL, PROFILE, ArxivSource
from app.sources.base import USER_AGENT, create_source
from app.sources.profiles import profile_for

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query Results</title>
  <entry>
    <id>http://arxiv.org/abs/2607.01234v1</id>
    <title>Agenti AI per il
      refactoring automatico</title>
    <summary>  Un paper sugli agenti
      che rifattorizzano codice.  </summary>
    <published>2026-07-20T12:00:00Z</published>
    <link href="http://arxiv.org/abs/2607.01234v1" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2607.01234v1" rel="related" title="pdf"/>
    <author><name>Alice Rossi</name></author>
    <author><name>Bob Bianchi</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2607.05678v2</id>
    <title>Self-hosted LLM serving</title>
    <summary>Servire modelli in locale.</summary>
    <published>2026-07-19T08:30:00Z</published>
    <link href="http://arxiv.org/abs/2607.05678v2" rel="alternate" type="text/html"/>
    <author><name>Carla Verdi</name></author>
  </entry>
</feed>
"""


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "automation"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="arxiv", type="arxiv", limit=10, categories=["cs.AI", "cs.SE"])
    base.update(overrides)
    return SourceConfig(**base)


def test_the_client_it_builds_itself_is_usable_against_arxiv(monkeypatch) -> None:
    """Il collector deve chiamare arXiv in https e seguire i redirect.

    Regressione: l'URL era in ``http``, arXiv risponde 301 verso https, e un
    redirect NON è un errore per ``raise_for_status`` — il parser Atom riceveva
    il corpo del redirect e la fonte falliva a ogni run. Non se ne era accorto
    nessuno perché tutti gli altri test iniettano un client finto e non
    guardavano mai quello che il collector costruisce da sé.
    """
    assert API_URL.startswith("https://")

    # Il test guarda la CONFIGURAZIONE del client, non la rete: i proxy
    # dell'ambiente non devono entrare nel merito (httpx li leggerebbe).
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)

    src = ArxivSource(_cfg(), _app_config(), Settings())
    client = src._get_client()
    try:
        assert client.follow_redirects is True
        assert USER_AGENT in client.headers["User-Agent"]
    finally:
        client.close()


def test_a_redirect_is_followed_instead_of_reaching_the_parser() -> None:
    """Con follow_redirects il 301 si risolve; senza, l'XML era il redirect."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(
                301, headers={"Location": "https://export.arxiv.org/api/query?moved=1"}
            )
        return httpx.Response(200, text=ATOM_FEED)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    src = ArxivSource(_cfg(), _app_config(), Settings(), client=client)

    items = src.fetch()

    assert len(seen) == 2  # ha seguito il redirect
    assert len(items) == 2  # e ha parsato il feed vero


def test_fetch_parses_atom_entries() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=ATOM_FEED)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = ArxivSource(_cfg(), _app_config(), Settings(), client=client)
    items = src.fetch()

    assert len(items) == 2
    item = items[0]
    assert item.source == "arxiv"
    assert item.external_id == "2607.01234v1"  # ultima parte dell'id, versione inclusa
    assert item.title == "Agenti AI per il refactoring automatico"  # whitespace normalizzato
    assert item.text == "Un paper sugli agenti che rifattorizzano codice."
    assert item.url == "http://arxiv.org/abs/2607.01234v1"  # link abs, non il pdf
    assert item.author == "Alice Rossi"  # primo autore
    assert item.engagement_json == {}  # arXiv non ha contatori
    assert item.created_at is not None
    assert item.created_at.tzinfo is None  # naive UTC, convenzione del progetto
    assert items[1].external_id == "2607.05678v2"
    # Una sola richiesta, ordinata per data di submission.
    assert captured["params"]["sortBy"] == "submittedDate"
    assert captured["params"]["sortOrder"] == "descending"
    assert captured["params"]["max_results"] == "10"


def test_search_query_joins_categories_in_or() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.params["search_query"]
        return httpx.Response(200, text=ATOM_FEED)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = ArxivSource(_cfg(), _app_config(), Settings(), client=client)
    src.fetch()

    assert captured["query"] == "cat:cs.AI OR cat:cs.SE"


def test_search_query_falls_back_to_keywords_without_categories() -> None:
    src = ArxivSource(_cfg(categories=[]), _app_config(), Settings())
    assert src._search_query() == 'all:"ai agents" OR all:"automation"'


def test_registry_knows_arxiv_and_profile_is_registered() -> None:
    src = create_source(_cfg(), _app_config(), Settings())
    assert isinstance(src, ArxivSource)
    assert profile_for("arxiv") is PROFILE
