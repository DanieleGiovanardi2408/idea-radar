"""Collector GitHub tramite la Search API (token gratuito opzionale).

Non esiste un endpoint ufficiale "trending", quindi lo si costruisce con due
vincoli sulla Search API: **repo nati di recente**, ordinati per stelle. È la
differenza tra "i più stellati di sempre" e "quelli che stanno salendo".

La prima versione ordinava per stelle senza filtro sulla data, e in 51 run ha
raccolto 31 repo sempre uguali: freeCodeCamp (452k stelle), tensorflow (196k),
ohmyzsh (188k), 22 su 31 creati prima del 2024. Cioè l'esatto opposto del caso
che questo progetto mette in copertina — "2k stelle in tre mesi" — e con lo
scoring a gate quei giganti valgono ormai ~0.1: la fonte non contribuiva nulla.

Una richiesta per keyword invece di una sola in OR: costa 6 chiamate su un
limite di 30/minuto col token, e ogni keyword porta i suoi emergenti invece di
farsi schiacciare dal termine più popolare.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import register_source
from app.sources.profiles import SourceProfile, register_profile

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.github.com/search/repositories"
SOURCE_NAME = "github"
REQUEST_DELAY = 0.5  # una richiesta per keyword: gentilezza tra l'una e l'altra

PROFILE = SourceProfile(
    velocity_cap=30.0,  # stelle/giorno che valgono heat = 1.0
    saturation_cap=60_000.0,
    credibility_base=0.45,
    live_counter=True,  # le stelle crescono nel tempo: il delta misura crescita reale
    velocity_per_age=True,  # euristica cold-start: stelle/giorno medie di vita
    maturity_in_saturation=True,  # un repo è "maturo" se popolare E vecchio
    engagement_weights={"stars": 1.0, "forks": 2.0},
)
register_profile(SOURCE_NAME, PROFILE)


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

    def age_bands(self) -> list[tuple[int, int]]:
        """Le fasce d'età da interrogare, come coppie (giorni_da, giorni_a)."""
        edges = sorted({d for d in self.cfg.created_windows if d > 0})
        if not edges:
            return [(0, 540)]
        return list(zip([0, *edges[:-1]], edges))

    def search_query(
        self,
        keywords: str | list[str],
        band: tuple[int, int],
        today: datetime | None = None,
    ) -> str:
        """La query per un TEMA in una fascia d'età: giovane, in tema, non rumorosa.

        Le keyword arrivano in OR, ma sono quelle di un solo profilo: è la
        differenza col vecchio OR globale, che mescolava "domotica" e "prompt
        engineering" nella stessa domanda e lasciava vincere il termine più
        popolare. Dentro un profilo l'OR è legittimo — sono sinonimi di un tema.

        Il vincolo sulla nascita è quello che fa la differenza — senza, "ordinato
        per stelle" significa "i più famosi del mondo", la domanda sbagliata.
        """
        today = today or datetime.now(timezone.utc)
        newer, older = band
        start = (today - timedelta(days=older)).date().isoformat()
        end = (today - timedelta(days=newer)).date().isoformat()
        terms = [keywords] if isinstance(keywords, str) else list(keywords)
        quoted = " OR ".join(f'"{term}"' for term in terms) or '"software"'
        return f"{quoted} stars:>={self.cfg.min_stars} created:{start}..{end}"

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=20.0)
        return self._client

    def fetch(self) -> list[Item]:
        client = self._get_client()
        # Un gruppo di query per profilo: 4 profili x 3 fasce = 12 richieste,
        # dentro il limite di 30/minuto. Con una richiesta per keyword sarebbero
        # 54 e il rate limit taglierebbe la fonte a metà.
        groups = [p.keywords for p in self.app_config.effective_profiles()]
        bands = self.age_bands()
        # La quota si divide tra le FASCE, non si assegna al miglior punteggio
        # globale: le stelle si accumulano col tempo, quindi ordinare tutto
        # insieme farebbe vincere sempre la fascia più vecchia — proprio il
        # pregiudizio che questa fonte deve togliersi.
        per_band = max(1, self.cfg.limit // len(bands))
        per_query = max(5, per_band)
        collected: list[Item] = []
        seen_ids: set[str] = set()
        first_request = True
        try:
            for band in bands:
                in_band: dict[str, Item] = {}
                for keywords in groups:
                    if not first_request:
                        time.sleep(REQUEST_DELAY)
                    first_request = False
                    try:
                        resp = client.get(
                            SEARCH_URL,
                            params={
                                "q": self.search_query(keywords, band),
                                "sort": "stars",
                                "order": "desc",
                                "per_page": per_query,
                            },
                            headers=self._headers(),
                        )
                        resp.raise_for_status()
                        repos = resp.json().get("items", [])
                    except (httpx.HTTPError, ValueError) as exc:
                        # Una query fallita (o un rate limit) non ferma le altre.
                        logger.warning(
                            "GitHub, tema %r fascia %s: %s", keywords, band, exc
                        )
                        continue
                    for repo in repos:
                        item = self._to_item(repo)
                        if item.external_id not in seen_ids:
                            in_band.setdefault(item.external_id, item)
                best = sorted(
                    in_band.values(),
                    key=lambda i: (i.engagement_json or {}).get("stars", 0),
                    reverse=True,
                )[:per_band]
                collected.extend(best)
                seen_ids.update(i.external_id for i in best)
            return collected[: self.cfg.limit]
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


register_source("github", GitHubSource)
