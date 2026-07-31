"""Collector Hugging Face Hub: modelli e dataset che stanno salendo.

API pubblica, gratuita, senza chiave (``huggingface.co/api/models``). Il motivo
per cui vale la pena averla: ``likes`` e ``downloads`` sono **contatori vivi**,
crescono nel tempo — quindi qui la heat si misura a delta tra osservazioni, come
per GitHub e Hacker News, invece di essere stimata dall'età. Con arXiv e i feed
RSS che non hanno engagement, era l'asse mancante per un radar puntato sull'AI.

Due accorgimenti presi dagli errori delle altre fonti:

- ordinamento per ``lastModified``, non per ``downloads``. Ordinare per download
  restituisce i modelli più scaricati del mondo (Llama, BERT) — gli stessi a
  ogni run, e mercati chiusi per definizione: è lo stesso errore che teneva la
  fonte GitHub ferma su freeCodeCamp e tensorflow.
- una richiesta per keyword, così nessun termine popolare schiaccia gli altri.

Il ``downloads`` di HF è una finestra a 30 giorni, quindi è già una velocità:
sommarlo alle ``likes`` con peso pieno lo farebbe dominare, da cui i pesi
nell'engagement del profilo.
"""

import logging
import time
from datetime import UTC, datetime

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import USER_AGENT, clean_html_text, register_source
from app.sources.profiles import SourceProfile, register_profile

logger = logging.getLogger(__name__)

API_URL = "https://huggingface.co/api"
SOURCE_NAME = "huggingface"
REQUEST_DELAY = 0.3
TIMEOUT = 30.0

# Contatore vivo come GitHub: le likes crescono e il delta misura crescita vera.
# I cap sono più bassi di GitHub perché su HF i numeri assoluti sono più piccoli:
# un modello con 2k likes è un fenomeno, un repo con 2k stelle è un buon inizio.
PROFILE = SourceProfile(
    velocity_cap=15.0,  # likes-equivalenti al giorno che valgono heat = 1.0
    saturation_cap=8_000.0,
    credibility_base=0.45,  # l'hub mostra autore e organizzazione
    live_counter=True,
    velocity_per_age=True,
    maturity_in_saturation=True,
    # `downloads` è già una finestra a 30 giorni: pesarlo come le likes lo
    # farebbe dominare la riduzione scalare.
    engagement_weights={"likes": 1.0, "downloads": 0.01},
)
register_profile(SOURCE_NAME, PROFILE)


class HuggingFaceSource:
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

    def kinds(self) -> list[str]:
        """Che cosa cercare sull'hub: ``models``, ``datasets``, o entrambi."""
        allowed = ("models", "datasets")
        chosen = [k for k in self.cfg.hf_kinds if k in allowed]
        return chosen or ["models"]

    def search_params(self, keyword: str, per_query: int) -> dict:
        """Parametri di ricerca. ``sort=lastModified`` è la scelta che conta.

        Ordinare per ``downloads`` darebbe i modelli più scaricati di sempre —
        sempre gli stessi, e per definizione già affermati.
        """
        return {
            "search": keyword,
            "sort": "lastModified",
            "direction": -1,
            "limit": per_query,
            "full": "true",
        }

    def fetch(self) -> list[Item]:
        client = self._get_client()
        keywords = self.app_config.search_keywords(self.cfg.max_keywords) or ["agent"]
        kinds = self.kinds()
        per_kind = max(1, self.cfg.limit // len(kinds))
        per_query = max(5, per_kind)
        collected: list[Item] = []
        seen: set[str] = set()
        first = True
        try:
            for kind in kinds:
                in_kind: dict[str, Item] = {}
                for keyword in keywords:
                    if not first:
                        time.sleep(REQUEST_DELAY)
                    first = False
                    try:
                        resp = client.get(
                            f"{API_URL}/{kind}",
                            params=self.search_params(keyword, per_query),
                        )
                        resp.raise_for_status()
                        entries = resp.json()
                    except (httpx.HTTPError, ValueError) as exc:
                        # Una keyword fallita non ferma le altre (né il run).
                        logger.warning(
                            "Hugging Face, %s keyword %r: %s", kind, keyword, exc
                        )
                        continue
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        try:
                            item = self._to_item(entry, kind)
                        except (TypeError, ValueError, AttributeError) as exc:
                            # Come per le keyword: una entry malformata non deve
                            # far perdere gli altri segnali già raccolti.
                            logger.warning(
                                "Hugging Face, %s entry %r scartata: %s",
                                kind,
                                entry.get("id"),
                                exc,
                            )
                            continue
                        if item is not None and item.external_id not in seen:
                            in_kind.setdefault(item.external_id, item)
                best = sorted(
                    in_kind.values(),
                    key=lambda i: PROFILE.engagement(i.engagement_json),
                    reverse=True,
                )[:per_kind]
                collected.extend(best)
                seen.update(i.external_id for i in best)
            return collected[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(entry: dict, kind: str) -> Item | None:
        repo_id = entry.get("id") or entry.get("modelId")
        if not repo_id:
            return None
        # `createdAt` non è sempre presente; `lastModified` da solo non dice
        # l'età, quindi senza data di nascita si lascia None e lo scoring
        # ripiega sull'euristica invece di inventare una recency.
        created_at = _parse_iso(entry.get("createdAt"))
        card_data = entry.get("cardData")
        description = " ".join(
            part
            for part in (
                _as_text(entry.get("pipeline_tag")),
                _as_text(entry.get("tags"), limit=12),
                _as_text(card_data.get("license") if isinstance(card_data, dict) else None),
            )
            if part
        )
        return Item(
            source=SOURCE_NAME,
            # `kind` nell'id: un modello e un dataset omonimi sono cose diverse.
            external_id=f"{kind}:{repo_id}",
            title=clean_html_text(str(repo_id))[:300],
            url=f"https://huggingface.co/{'datasets/' if kind == 'datasets' else ''}{repo_id}",
            text=clean_html_text(description)[:2000] or None,
            author=entry.get("author") or str(repo_id).split("/")[0],
            engagement_json={
                "likes": entry.get("likes") or 0,
                "downloads": entry.get("downloads") or 0,
            },
            created_at=created_at,
            raw_json=entry,
        )


def _as_text(value: object, limit: int | None = None) -> str:
    """Appiattisce a testo un campo del card data, che non ha schema garantito.

    L'hub non tipizza il `cardData`: `license` arriva come stringa sui modelli a
    licenza singola e come lista su quelli multi-licenza (`["apache-2.0",
    "other"]`), e lo stesso vale per gli altri campi liberi. Concatenare senza
    normalizzare faceva morire l'intero collector con
    ``sequence item 1: expected str instance, list found`` — un modello con
    licenza doppia bastava a perdere tutti i segnali del run.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts = [_as_text(v) for v in list(value)[:limit]]
        return " ".join(p for p in parts if p)
    if isinstance(value, dict):
        return _as_text(list(value.values()), limit=limit)
    return str(value).strip()


def _parse_iso(value: str | None) -> datetime | None:
    """ISO 8601 -> naive UTC (convenzione del progetto)."""
    if not value:
        return None
    try:
        return (
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


register_source("huggingface", HuggingFaceSource)
