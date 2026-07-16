"""Collector GitHub tramite la Search API (token gratuito opzionale).

Non esiste un endpoint ufficiale "trending": usiamo la Search API filtrando
per keyword e ordinando per stelle. Senza token funziona comunque, ma con
rate limit molto più basso.
"""

from datetime import datetime

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item

SEARCH_URL = "https://api.github.com/search/repositories"
SOURCE_NAME = "github"


class GitHubSource:
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

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    def _query(self) -> str:
        keywords = self.app_config.keywords or ["open source"]
        # Almeno 10 stelle per tagliare il rumore; keyword in OR.
        terms = " OR ".join(f'"{k}"' for k in keywords)
        return f"{terms} stars:>10"

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=20.0)
        return self._client

    def fetch(self) -> list[Item]:
        client = self._get_client()
        try:
            resp = client.get(
                SEARCH_URL,
                params={
                    "q": self._query(),
                    "sort": "stars",
                    "order": "desc",
                    "per_page": self.cfg.limit,
                },
                headers=self._headers(),
            )
            resp.raise_for_status()
            repos = resp.json().get("items", [])
            return [self._to_item(repo) for repo in repos]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(repo: dict) -> Item:
        created_raw = repo.get("created_at")
        created_at = None
        if created_raw:
            # ISO 8601 tipo "2024-01-02T03:04:05Z" -> naive UTC.
            created_at = datetime.fromisoformat(
                created_raw.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        return Item(
            source=SOURCE_NAME,
            external_id=str(repo["id"]),
            title=repo.get("full_name", "(repo sconosciuto)"),
            url=repo.get("html_url"),
            text=repo.get("description"),
            author=(repo.get("owner") or {}).get("login"),
            engagement_json={
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "watchers": repo.get("watchers_count", 0),
            },
            created_at=created_at,
            raw_json=repo,
        )
