"""Collector HN via Algolia (search_by_date): il backfill del radar.

Le top-story (collector ``hn``) sono una fotografia del PRESENTE: ciò che
passa in front page mentre il Mac dorme è perso. Questo collector interroga
l'archivio Algolia di HN per finestra temporale — le storie delle ultime
``lookback_hours`` che matchano le keyword, con almeno ``min_points`` punti —
e quindi cura i buchi da solo.

Due scelte deliberate:

- ``SOURCE_NAME = "hn"``, lo stesso delle top-story: l'``external_id`` è l'id
  HN in entrambi i collector, quindi l'upsert fonde i doppioni da solo.
- Finestra FISSA a ogni run, niente "dall'ultimo run riuscito": più semplice,
  zero accoppiamento col DB e — ripassando per due giorni sulle stesse
  storie — ogni run ne aggiorna l'engagement, così ``item_stats`` accumula
  osservazioni ripetute: il carburante della futura heat "a delta".
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import register_source

logger = logging.getLogger(__name__)

BASE_URL = "https://hn.algolia.com/api/v1/search_by_date"
SOURCE_NAME = "hn"  # condiviso con le top-story: i doppioni si fondono per id
REQUEST_DELAY = 0.3  # una richiesta per keyword: gentilezza tra l'una e l'altra
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def algolia_params(
    keyword: str, since_epoch: int, min_points: int, limit: int
) -> dict:
    """Parametri della ``search_by_date`` (funzione pura: testabile senza rete)."""
    return {
        "query": keyword,
        "tags": "story",
        "numericFilters": f"created_at_i>{since_epoch},points>={min_points}",
        "hitsPerPage": limit,
    }


class HnAlgoliaSource:
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

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def fetch(self) -> list[Item]:
        client = self._get_client()
        try:
            # Epoch calcolato su UTC esplicito: l'utcnow() del progetto è naive
            # e .timestamp() su un naive assumerebbe il fuso LOCALE (sbagliato).
            since = int(
                (
                    datetime.now(timezone.utc)
                    - timedelta(hours=self.cfg.lookback_hours)
                ).timestamp()
            )
            keywords = self.app_config.keywords or ["open source"]
            seen: dict[str, Item] = {}
            for index, keyword in enumerate(keywords):
                if index > 0:
                    time.sleep(REQUEST_DELAY)
                try:
                    resp = client.get(
                        BASE_URL,
                        params=algolia_params(
                            keyword, since, self.cfg.min_points, self.cfg.limit
                        ),
                    )
                    resp.raise_for_status()
                    hits = resp.json().get("hits", [])
                except (httpx.HTTPError, ValueError) as exc:
                    # Una keyword fallita non ferma le altre (né il run).
                    logger.warning("HN Algolia, keyword %r: %s", keyword, exc)
                    continue
                for hit in hits:
                    item = self._to_item(hit)
                    if item is not None:
                        seen.setdefault(item.external_id, item)
            ranked = sorted(
                seen.values(),
                key=lambda i: (i.engagement_json or {}).get("score", 0),
                reverse=True,
            )
            return ranked[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(hit: dict) -> Item | None:
        object_id = hit.get("objectID")
        title = (hit.get("title") or "").strip()
        if not object_id or not title:
            return None
        created = hit.get("created_at_i")
        created_at = (
            datetime.fromtimestamp(created, tz=timezone.utc).replace(tzinfo=None)
            if created
            else None
        )
        story_text = hit.get("story_text")
        return Item(
            source=SOURCE_NAME,
            external_id=str(object_id),
            title=title[:300],
            url=hit.get("url")
            or f"https://news.ycombinator.com/item?id={object_id}",
            text=_strip_html(story_text)[:2000] if story_text else None,
            author=hit.get("author"),
            engagement_json={
                "score": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
            },
            created_at=created_at,
            raw_json=hit,
        )


# Nessun register_profile: condivide SOURCE_NAME (e quindi il profilo) di "hn".
register_source("hn_algolia", HnAlgoliaSource)
