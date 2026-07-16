"""Collector Hacker News tramite la Firebase API pubblica (nessun token)."""

from datetime import datetime, timezone

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item

BASE_URL = "https://hacker-news.firebaseio.com/v0"
SOURCE_NAME = "hn"


class HackerNewsSource:
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
            top_ids = client.get(f"{BASE_URL}/topstories.json").raise_for_status().json()
            items: list[Item] = []
            for story_id in top_ids[: self.cfg.limit]:
                raw = (
                    client.get(f"{BASE_URL}/item/{story_id}.json")
                    .raise_for_status()
                    .json()
                )
                if not raw or raw.get("type") != "story":
                    continue
                items.append(self._to_item(raw))
            return items
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(raw: dict) -> Item:
        created = raw.get("time")
        created_at = (
            datetime.fromtimestamp(created, tz=timezone.utc).replace(tzinfo=None)
            if created
            else None
        )
        return Item(
            source=SOURCE_NAME,
            external_id=str(raw["id"]),
            title=raw.get("title", "(senza titolo)"),
            url=raw.get("url"),
            text=raw.get("text"),
            author=raw.get("by"),
            engagement_json={
                "score": raw.get("score", 0),
                "comments": raw.get("descendants", 0),
            },
            created_at=created_at,
            raw_json=raw,
        )
