"""Astrazione comune dei collector, registry dei tipi e factory."""

from typing import Protocol

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item


class Source(Protocol):
    """Un collector sa produrre una lista di :class:`Item` grezzi."""

    def fetch(self) -> list[Item]: ...


# ``type`` di config.yaml -> classe collector. Non è hardcoded qui: ogni
# modulo collector si registra da solo con ``register_source`` all'import
# (``load_collectors`` li importa tutti). Una fonte nuova = un modulo nuovo.
_SOURCE_TYPES: dict[str, type] = {}


def register_source(type_name: str, cls: type) -> None:
    """Registra un collector per il ``type`` usato in config.yaml."""
    _SOURCE_TYPES[type_name] = cls


def load_collectors() -> None:
    """Importa i moduli dei collector: l'import registra classi e profili.

    Import locale (non in testa al modulo) per evitare cicli di import.
    """
    from app.sources import (  # noqa: F401
        arxiv,
        github,
        hackernews,
        hn_algolia,
        producthunt,
        rss,
    )


def create_source(
    source_cfg: SourceConfig,
    app_config: AppConfig,
    settings: Settings,
    client: httpx.Client | None = None,
) -> Source:
    """Istanzia il collector giusto in base a ``source_cfg.type``.

    ``client`` è iniettabile per i test (httpx.MockTransport).
    """
    load_collectors()
    try:
        cls = _SOURCE_TYPES[source_cfg.type]
    except KeyError as exc:
        raise ValueError(f"Tipo di fonte sconosciuto: {source_cfg.type!r}") from exc

    return cls(source_cfg, app_config, settings, client=client)
