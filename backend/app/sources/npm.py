"""Collector npm: pacchetti nuovi, prima che diventino notizia.

Il registry npm ha una search pubblica e gratuita (``/-/v1/search``) che per
ogni pacchetto restituisce anche i punteggi calcolati da npms.io: *quality*,
*popularity*, *maintenance*. Una libreria compare qui settimane prima di finire
su Hacker News — e quando ci finisce, non è più un'apertura.

Perché serve un filtro sull'età: la search ordina per rilevanza combinata con la
popolarità, quindi senza vincoli restituisce react, lodash ed express. È lo
stesso errore della vecchia query GitHub, e si risolve allo stesso modo —
scartando ciò che è nato troppo tempo fa (``max_age_days``).

Attenzione a cosa NON c'è: i download non stanno in questa risposta (servono
chiamate a ``api.npmjs.org/downloads``, una per pacchetto). Qui l'engagement è
il punteggio di popolarità normalizzato 0-1, che NON è un contatore vivo — la
heat resta quindi sull'euristica.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import USER_AGENT, clean_html_text, register_source
from app.sources.profiles import SourceProfile, register_profile

logger = logging.getLogger(__name__)

API_URL = "https://registry.npmjs.org/-/v1/search"
DOWNLOADS_URL = "https://api.npmjs.org/downloads/point/last-week"
SOURCE_NAME = "npm"
REQUEST_DELAY = 0.3
TIMEOUT = 30.0

# L'engagement sono i DOWNLOAD SETTIMANALI, non i punteggi della search.
#
# Il primo tentativo usava `score.detail.popularity` e `quality` di npms.io, e
# sui dati reali quei campi arrivano a **1.0 per ogni pacchetto**: non
# discriminano niente. Combinati con una divisione per l'età davano heat 1.00 a
# tutto, e npm si prendeva 17 dei 55 posti sopra soglia con librerie
# affermatissime (`@docusaurus/core`, `appium`) spacciate per segnali in ascesa.
#
# I download settimanali invece sono un numero vero, sono già una *velocità*
# (per settimana, quindi niente divisione per l'età) e crescono nel tempo,
# quindi sono anche un contatore vivo: la heat si misura a delta come su GitHub.
# Costano una richiesta per pacchetto ad api.npmjs.org, gratuita e senza chiave.
PROFILE = SourceProfile(
    velocity_cap=20_000.0,  # download/settimana che valgono heat = 1.0
    saturation_cap=1_000_000.0,  # oltre il milione è una libreria di sistema
    credibility_base=0.35,
    live_counter=True,
    velocity_per_age=False,  # i download settimanali sono già un tasso
    maturity_in_saturation=True,
    engagement_weights={"downloads": 1.0},
)
register_profile(SOURCE_NAME, PROFILE)


class NpmSource:
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
            self._client = httpx.Client(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def search_params(self, keyword: str, per_query: int) -> dict:
        """Ricerca per keyword, sbilanciata verso qualità e manutenzione.

        I pesi spostano il ranking dalla popolarità pura (che premia i pacchetti
        già affermati) verso i progetti curati: è la stessa idea del vincolo sulla
        data di nascita in GitHub, applicata con gli strumenti che npm offre.
        """
        return {
            "text": keyword,
            "size": per_query,
            "popularity": 0.3,
            "quality": 0.5,
            "maintenance": 0.2,
        }

    def fetch(self) -> list[Item]:
        client = self._get_client()
        keywords = self.app_config.search_keywords(self.cfg.max_keywords) or ["cli"]
        per_query = max(5, self.cfg.limit // max(len(keywords), 1) * 2)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=self.cfg.max_age_days
        )
        seen: dict[str, Item] = {}
        try:
            for index, keyword in enumerate(keywords):
                if index > 0:
                    time.sleep(REQUEST_DELAY)
                try:
                    resp = client.get(API_URL, params=self.search_params(keyword, per_query))
                    resp.raise_for_status()
                    objects = resp.json().get("objects", [])
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("npm, keyword %r: %s", keyword, exc)
                    continue
                for entry in objects:
                    item = self._to_item(entry)
                    if item is None:
                        continue
                    # Un pacchetto vecchio non è un segnale emergente, per quanto
                    # sia popolare: è il motivo per cui questa fonte esiste.
                    if item.created_at is not None and item.created_at < cutoff:
                        continue
                    seen.setdefault(item.external_id, item)
            # I download arrivano solo per i candidati sopravvissuti al filtro
            # sull'età: una richiesta a pacchetto, quindi non si spreca.
            candidates = sorted(seen.values(), key=lambda i: i.title)[
                : self.cfg.limit * 2
            ]
            self._add_downloads(client, candidates)
            ranked = sorted(
                candidates,
                key=lambda i: PROFILE.engagement(i.engagement_json),
                reverse=True,
            )
            return ranked[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    def _add_downloads(self, client: httpx.Client, items: list[Item]) -> None:
        """Riempie ``engagement_json['downloads']`` coi download dell'ultima settimana.

        Un pacchetto senza dato resta a zero: meglio una heat bassa che un numero
        inventato. Un errore qui non fa fallire la fonte — l'item c'è comunque, e
        alla prossima osservazione il download magari arriva.
        """
        for index, item in enumerate(items):
            if index > 0:
                time.sleep(REQUEST_DELAY)
            try:
                resp = client.get(f"{DOWNLOADS_URL}/{item.title}")
                resp.raise_for_status()
                downloads = int(resp.json().get("downloads") or 0)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                logger.info("npm, download di %r non disponibili: %s", item.title, exc)
                continue
            item.engagement_json = {**(item.engagement_json or {}), "downloads": downloads}

    @staticmethod
    def _to_item(entry: dict) -> Item | None:
        package = entry.get("package") or {}
        name = package.get("name")
        if not name:
            return None
        score = (entry.get("score") or {}).get("detail") or {}
        links = package.get("links") or {}
        keywords = ", ".join(str(k) for k in (package.get("keywords") or [])[:8])
        description = clean_html_text(package.get("description") or "")
        publisher = package.get("publisher") or {}
        return Item(
            source=SOURCE_NAME,
            external_id=str(name),
            title=clean_html_text(str(name))[:300],
            url=links.get("npm") or f"https://www.npmjs.com/package/{name}",
            text=(f"{description} [{keywords}]" if keywords else description)[:2000] or None,
            author=publisher.get("username"),
            # `downloads` lo riempie `_add_downloads`; i punteggi della search si
            # tengono solo nel raw_json, perché sui dati reali sono tutti 1.0.
            engagement_json={"downloads": 0},
            # `date` è l'ultima pubblicazione, non la nascita del pacchetto: per
            # una libreria è comunque il segnale di vita che ci interessa.
            created_at=_parse_iso(package.get("date")),
            raw_json=entry,
        )


def _parse_iso(value: str | None) -> datetime | None:
    """ISO 8601 -> naive UTC (convenzione del progetto)."""
    if not value:
        return None
    try:
        return (
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


register_source("npm", NpmSource)
