"""Collector Stack Exchange: la DOMANDA, non l'offerta.

Tutte le altre fonti di questo radar guardano ciò che viene costruito — repo,
modelli, paper, lanci. Questa guarda ciò che manca: una domanda che raccoglie
voti e visite, e che nessuno ha ancora risolto, è un problema reale senza una
soluzione buona. È l'asse complementare, e per un radar di *opportunità* è
quello che dovrebbe pesare di più.

API gratuita e senza chiave fino a 300 richieste al giorno (con una chiave
gratuita si sale a 10.000, ma per una fonte interrogata ogni 4 ore non serve).

Due scelte deliberate:

- ``sort=votes`` su una finestra temporale recente (``fromdate``), non
  ``sort=activity``: si cercano le domande che la gente ha *votato*, cioè "ho
  lo stesso problema", non quelle con l'ultimo commento più recente.
- si tengono solo le domande **senza risposta accettata**. Una domanda risolta
  ha già la sua soluzione: non è un'opportunità, è documentazione.
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

API_URL = "https://api.stackexchange.com/2.3/questions"
SOURCE_NAME = "stackexchange"
REQUEST_DELAY = 0.4
TIMEOUT = 30.0

# I voti su una domanda crescono nel tempo: contatore vivo, heat a delta.
# Numeri molto più piccoli di GitHub — una domanda con 50 voti è tanta roba —
# quindi cap bassi, altrimenti la fonte avrebbe heat sempre a zero.
PROFILE = SourceProfile(
    velocity_cap=3.0,  # voti-equivalenti al giorno che valgono heat = 1.0
    saturation_cap=500.0,
    credibility_base=0.35,  # moderazione forte, ma è una domanda, non un progetto
    live_counter=True,
    velocity_per_age=True,
    # NIENTE maturity_in_saturation: una domanda vecchia e votata resta un
    # problema aperto, non un "mercato chiuso" — al contrario, è più solida.
    engagement_weights={"score": 1.0, "views": 0.002, "answers": 0.5},
)
register_profile(SOURCE_NAME, PROFILE)


class StackExchangeSource:
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

    def search_tags(self) -> list[str]:
        """I tag da seguire; senza configurazione si derivano dalle keyword.

        Le keyword del radar sono frasi ("ai agents"), i tag di Stack Overflow
        sono token con trattini ("ai-agents"): la conversione è meccanica ma
        indovinata, quindi meglio elencarli a mano in config.yaml.
        """
        if self.cfg.tags:
            return self.cfg.tags
        derived = self.app_config.search_keywords(self.cfg.max_keywords)
        return [k.strip().replace(" ", "-") for k in derived if k.strip()]

    def query_params(self, tag: str, per_query: int, today: datetime | None = None) -> dict:
        today = today or datetime.now(timezone.utc)
        since = int((today - timedelta(days=self.cfg.max_age_days)).timestamp())
        return {
            "site": self.cfg.site,
            "tagged": tag,
            "fromdate": since,
            "sort": "votes",  # non "activity": conta chi ha lo stesso problema
            "order": "desc",
            "pagesize": per_query,
            "filter": "withbody",  # serve il testo per embedding e insight
        }

    def fetch(self) -> list[Item]:
        client = self._get_client()
        tags = self.search_tags()
        if not tags:
            return []
        per_tag = max(2, self.cfg.limit // len(tags))
        seen: dict[str, Item] = {}
        try:
            for index, tag in enumerate(tags):
                if index > 0:
                    time.sleep(REQUEST_DELAY)
                try:
                    resp = client.get(API_URL, params=self.query_params(tag, per_tag))
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    # Un tag fallito (o la quota esaurita) non ferma gli altri.
                    logger.warning("Stack Exchange, tag %r: %s", tag, exc)
                    continue
                if payload.get("quota_remaining") == 0:
                    logger.warning("Stack Exchange: quota giornaliera esaurita")
                for question in payload.get("items", []):
                    item = self._to_item(question)
                    if item is not None:
                        seen.setdefault(item.external_id, item)
            ranked = sorted(
                seen.values(),
                key=lambda i: PROFILE.engagement(i.engagement_json),
                reverse=True,
            )
            return ranked[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(question: dict) -> Item | None:
        question_id = question.get("question_id")
        title = clean_html_text(question.get("title") or "")
        if not question_id or not title:
            return None
        # Una domanda con risposta accettata non è un problema aperto.
        if question.get("is_answered") or question.get("accepted_answer_id"):
            return None
        created = question.get("creation_date")
        created_at = (
            datetime.fromtimestamp(created, tz=timezone.utc).replace(tzinfo=None)
            if created
            else None
        )
        owner = question.get("owner") or {}
        body = clean_html_text(question.get("body") or "")
        tags = ", ".join(str(t) for t in (question.get("tags") or [])[:8])
        return Item(
            source=SOURCE_NAME,
            external_id=str(question_id),
            title=title[:300],
            url=question.get("link"),
            # I tag nel testo aiutano il fit per keyword e il clustering.
            text=(f"{body} [{tags}]" if tags else body)[:2000] or None,
            author=owner.get("display_name"),
            engagement_json={
                "score": question.get("score") or 0,
                "views": question.get("view_count") or 0,
                "answers": question.get("answer_count") or 0,
            },
            created_at=created_at,
            raw_json=question,
        )


register_source("stackexchange", StackExchangeSource)
