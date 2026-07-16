"""Astrazione comune dei collector e factory per tipo di fonte."""

from typing import Protocol

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item


class Source(Protocol):
    """Un collector sa produrre una lista di :class:`Item` grezzi."""

    def fetch(self) -> list[Item]: ...


def create_source(
    source_cfg: SourceConfig,
    app_config: AppConfig,
    settings: Settings,
    client: httpx.Client | None = None,
) -> Source:
    """Istanzia il collector giusto in base a ``source_cfg.type``.

    ``client`` è iniettabile per i test (httpx.MockTransport).
    """
    # Import locale per evitare cicli di import.
    from app.sources.github import GitHubSource
    from app.sources.hackernews import HackerNewsSource
    from app.sources.rss import RssSource

    registry: dict[str, type] = {
        "hn": HackerNewsSource,
        "github": GitHubSource,
        "rss": RssSource,
    }
    try:
        cls = registry[source_cfg.type]
    except KeyError as exc:
        raise ValueError(f"Tipo di fonte sconosciuto: {source_cfg.type!r}") from exc

    return cls(source_cfg, app_config, settings, client=client)
