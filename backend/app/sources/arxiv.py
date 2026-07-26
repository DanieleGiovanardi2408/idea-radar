"""Collector arXiv tramite l'API Atom ufficiale (gratuita, senza chiave).

I paper non hanno contatori di engagement (niente stelle, voti o commenti):
la heat resta sull'euristica e non c'è nessun delta da misurare. In compenso
la fonte è curata (submission moderate, autori identificati), quindi la
credibilità di partenza è alta.

Sul fronte rete il collector è "educato" come chiede la netiquette di arXiv:
una SOLA richiesta per fetch, con tutte le categorie in OR in un'unica
``search_query`` invece di una chiamata per categoria.
"""

from datetime import datetime, timezone
from xml.etree import ElementTree

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import USER_AGENT, register_source
from app.sources.profiles import SourceProfile, register_profile

# HTTPS, non HTTP: su http arXiv risponde 301 verso https, e un redirect non è
# un errore per ``raise_for_status`` — il parser Atom si trovava a masticare il
# corpo del redirect e la fonte falliva a ogni run, silenziosamente.
API_URL = "https://export.arxiv.org/api/query"
SOURCE_NAME = "arxiv"

# Niente live counter (arXiv non espone engagement): la heat usa l'euristica
# e i cap restano quelli di default. Credibilità di base alta perché i paper
# passano una moderazione e gli autori sono reali e verificabili.
PROFILE = SourceProfile(credibility_base=0.45)
register_profile(SOURCE_NAME, PROFILE)

# arXiv è lenta: una query ordinata per submittedDate su più categorie prende
# regolarmente più di 20 secondi, e a 20 il run #50 è morto in "read operation
# timed out" (visibile nel Monitor solo dopo aver sistemato il salvataggio degli
# errori per fonte). Per una fonte interrogata ogni 4 ore aspettare vale più che
# perdere il giro: gli altri collector restano sui loro 15-20s, che a loro bastano.
TIMEOUT = 60.0

_ATOM = "{http://www.w3.org/2005/Atom}"
# Gli abstract possono essere lunghissimi: stesso tetto del collector RSS.
MAX_TEXT = 2000


class ArxivSource:
    def __init__(
        self,
        source_cfg: SourceConfig,
        app_config: AppConfig,
        settings: Settings,
        client: httpx.Client | None = None,
    ) -> None:
        self.cfg = source_cfg
        self.app_config = app_config
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def _search_query(self) -> str:
        """Categorie in OR; senza categorie si ripiega sulle keywords globali.

        Un'unica query per tutte le categorie: una sola richiesta per fetch.
        """
        if self.cfg.categories:
            return " OR ".join(f"cat:{cat}" for cat in self.cfg.categories)
        keywords = self.app_config.keywords or ["software"]
        return " OR ".join(f'all:"{kw}"' for kw in keywords)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            # Come il collector RSS: si seguono i redirect e si dichiara chi
            # siamo. La netiquette di arXiv chiede uno User-Agent riconoscibile,
            # e senza ``follow_redirects`` un 301 arriverebbe intatto al parser.
            self._client = httpx.Client(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def fetch(self) -> list[Item]:
        client = self._get_client()
        try:
            resp = client.get(
                API_URL,
                params={
                    "search_query": self._search_query(),
                    # I più recenti prima: il radar cerca segnali freschi,
                    # non i paper più citati di sempre.
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "max_results": self.cfg.limit,
                },
            )
            resp.raise_for_status()
            return self._parse_feed(resp.text)[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @classmethod
    def _parse_feed(cls, xml_text: str) -> list[Item]:
        root = ElementTree.fromstring(xml_text)
        return [
            item
            for item in (cls._to_item(entry) for entry in root.findall(f"{_ATOM}entry"))
            if item is not None
        ]

    @staticmethod
    def _to_item(entry: ElementTree.Element) -> Item | None:
        def text_of(tag: str) -> str | None:
            node = entry.find(f"{_ATOM}{tag}")
            if node is not None and (node.text or "").strip():
                return node.text.strip()
            return None

        title = text_of("title")
        abs_url = text_of("id")
        if not title or not abs_url:
            return None

        # L'id Atom è l'URL della pagina abs (es. http://arxiv.org/abs/2401.01234v2):
        # l'ultima parte, versione inclusa, è un id stabile e leggibile.
        external_id = abs_url.rstrip("/").rsplit("/", 1)[-1]

        # Link alla pagina abs (rel="alternate"); in mancanza va bene l'id,
        # che È già quell'URL.
        url = abs_url
        for node in entry.findall(f"{_ATOM}link"):
            if node.get("rel") == "alternate" and node.get("href"):
                url = node.get("href")
                break

        summary = text_of("summary")
        published = text_of("published")
        created_at = None
        if published:
            # ISO 8601 -> naive UTC (convenzione del progetto).
            created_at = (
                datetime.fromisoformat(published.replace("Z", "+00:00"))
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )

        # Primo autore: nei paper è quello "che conta" per la credibilità.
        author_node = entry.find(f"{_ATOM}author/{_ATOM}name")
        author = (
            author_node.text.strip()
            if author_node is not None and author_node.text
            else None
        )

        # Titoli e abstract arrivano con a-capo e rientri del feed: si normalizzano.
        return Item(
            source=SOURCE_NAME,
            external_id=external_id,
            title=" ".join(title.split())[:300],
            url=url,
            text=" ".join(summary.split())[:MAX_TEXT] if summary else None,
            author=author,
            engagement_json={},  # arXiv non espone contatori di engagement
            created_at=created_at,
            raw_json={"arxiv_id": external_id, "abs_url": abs_url},
        )


register_source("arxiv", ArxivSource)
