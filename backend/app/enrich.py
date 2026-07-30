"""Enricher pypistats: download PyPI per gli item che citano un pacchetto.

Non è una fonte — non porta item nuovi. Aggiunge a item già raccolti un
segnale di trazione che le loro fonti non hanno: un articolo RSS o un paper
arXiv che presenta una libreria non ha engagement suo, ma i download del
pacchetto sì, e sono una *velocità* già pronta (pypistats aggrega per
settimana). È il complemento di ciò che npm fa da sé per il suo ecosistema.

Il dato finisce in ``engagement_json["pypi_week"]``, che è un canale SEPARATO:

- i profili con pesi espliciti lo ignorano per costruzione;
- la riduzione "somma tutto" dei profili senza pesi lo salta apposta
  (``profiles.SourceProfile.engagement``), perché sommare migliaia di
  download ai 3 punti di un feed distorcerebbe heat e saturazione della fonte;
- lo scoring lo legge come asse autonomo: ``heat = max(heat_fonte,
  saturate(pypi_week, cap))`` — la trazione del pacchetto non può abbassare
  un item già caldo, può solo accendere un item che la fonte non sa misurare.

API: https://pypistats.org/api/ — pubblica, gratuita, senza chiave (vincolo
di progetto). Una richiesta per pacchetto, cache dentro il run, tetto in
``enrichment.max_packages_per_run``.
"""

import logging
import re
import time

import httpx

from app.appconfig import EnrichmentConfig
from app.models import Item
from app.sources.base import USER_AGENT

logger = logging.getLogger(__name__)

API_URL = "https://pypistats.org/api/packages/{package}/recent"
REQUEST_DELAY = 0.3
TIMEOUT = 15.0

# La chiave sotto cui il dato entra in engagement_json. Il prefisso "pypi_" è
# un contratto: profiles.engagement salta queste chiavi nella somma cieca.
ENGAGEMENT_KEY = "pypi_week"

# Nome di pacchetto valido (PEP 508): inizia e FINISCE con un alfanumerico —
# senza il vincolo sulla coda, "pip install fastapi." a fine frase catturava
# "fastapi." e la normalizzazione lo trasformava in "fastapi-", cioè un 404.
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
# pypi.org/project/<nome> in un URL o nel testo.
_PYPI_URL_RE = re.compile(rf"pypi\.org/project/({_NAME})")
# `pip install <nome>`: si accettano solo nomi, non flag (-r, --upgrade…).
_PIP_INSTALL_RE = re.compile(rf"\bpip3?\s+install\s+({_NAME})")


def normalize_package(name: str) -> str:
    """Normalizzazione PEP 503: minuscole, sequenze di ``-_.`` diventano ``-``.

    È il nome con cui pypistats indicizza: senza, ``Flask_Login`` e
    ``flask-login`` sembrerebbero due pacchetti (e uno dei due darebbe 404).
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def pypi_packages(item: Item) -> list[str]:
    """I pacchetti PyPI citati dall'item, normalizzati, senza duplicati."""
    haystack = f"{item.url or ''} {item.text or ''} {item.title or ''}"
    found = _PYPI_URL_RE.findall(haystack) + _PIP_INSTALL_RE.findall(haystack)
    seen: dict[str, None] = {}
    for name in found:
        seen.setdefault(normalize_package(name), None)
    return list(seen)


class PyPIStatsEnricher:
    """Una istanza per run: la cache e il budget valgono per tutte le fonti."""

    def __init__(
        self,
        config: EnrichmentConfig,
        client: httpx.Client | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self._sleep = sleeper
        # pkg -> download settimanali, o None = già chiesto, non pervenuto
        # (404 compresi: un nome inventato non va richiesto a ogni item).
        self._cache: dict[str, int | None] = {}
        self._requests = 0

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def _weekly_downloads(self, package: str) -> int | None:
        if package in self._cache:
            return self._cache[package]
        if self._requests >= self.config.max_packages_per_run:
            return None  # budget finito: niente cache, ritenterà il run dopo
        if self._requests:
            self._sleep(REQUEST_DELAY)
        self._requests += 1
        try:
            resp = self._get_client().get(API_URL.format(package=package))
            resp.raise_for_status()
            value = (resp.json().get("data") or {}).get("last_week")
        except (httpx.HTTPError, ValueError) as exc:
            # Un pacchetto senza dato non ferma gli altri; None in cache evita
            # di ripagare lo stesso errore per ogni item che lo cita.
            logger.warning("pypistats, pacchetto %r: %s", package, exc)
            self._cache[package] = None
            return None
        result = int(value) if isinstance(value, (int, float)) else None
        self._cache[package] = result
        return result

    def enrich(self, items: list[Item]) -> int:
        """Aggiunge ``pypi_week`` agli item che citano un pacchetto.

        Più pacchetti citati = si tiene il più scaricato: l'item parla di una
        cosa sola e il segnale è "quanta trazione ha ciò che presenta".
        Ritorna quanti item sono stati arricchiti (per il report della fonte).
        """
        if not self.config.pypi_downloads:
            return 0
        enriched = 0
        for item in items:
            best: int | None = None
            for package in pypi_packages(item):
                weekly = self._weekly_downloads(package)
                if weekly is not None and (best is None or weekly > best):
                    best = weekly
            if best is not None:
                item.engagement_json = {
                    **(item.engagement_json or {}),
                    ENGAGEMENT_KEY: best,
                }
                enriched += 1
        return enriched

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
