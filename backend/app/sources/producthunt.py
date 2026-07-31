"""Collector Product Hunt tramite l'API GraphQL v2 (token developer gratuito).

I voti di un lancio crescono per tutta la giornata (e oltre): sono un
contatore vivo, quindi la heat può misurarsi "a delta" tra osservazioni di
run consecutivi, come per le stelle GitHub.

Il token è obbligatorio: senza, ``fetch`` alza un ``RuntimeError`` con un
messaggio chiaro. La pipeline cattura gli errori per-fonte e li annota senza
uccidere il run, quindi una config con producthunt abilitato ma senza token
degrada in modo visibile, non silenzioso.
"""

from datetime import UTC, datetime

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item
from app.sources.base import register_source
from app.sources.profiles import SourceProfile, register_profile

API_URL = "https://api.producthunt.com/v2/api/graphql"
SOURCE_NAME = "producthunt"

# Razionale dei numeri:
# - live_counter: voti e commenti crescono nel tempo, il delta tra
#   osservazioni misura crescita reale (come le stelle GitHub).
# - velocity_cap=300: i voti/giorno di un buon lancio valgono heat = 1.0;
#   sopra si è in top-5 di giornata, non serve distinguere oltre.
# - saturation_cap=2000: oltre questi voti il prodotto ha già "vinto" la
#   front page, il segnale è affermato più che emergente.
# - credibility_base=0.40: community curata ma promozionale per natura
#   (chi posta sta lanciando il proprio prodotto).
PROFILE = SourceProfile(
    velocity_cap=300.0,
    saturation_cap=2_000.0,
    credibility_base=0.40,
    live_counter=True,
    engagement_weights={"votes": 1.0, "comments": 1.0},
)
register_profile(SOURCE_NAME, PROFILE)

# I post più recenti: i campi minimi per un Item + i contatori di engagement.
QUERY = """
query($first: Int!) {
  posts(order: NEWEST, first: $first) {
    edges {
      node {
        id
        name
        tagline
        description
        votesCount
        commentsCount
        createdAt
        url
        website
        user { username }
      }
    }
  }
}
"""


class ProductHuntSource:
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
            self._client = httpx.Client(timeout=20.0)
        return self._client

    def fetch(self) -> list[Item]:
        if not self.settings.producthunt_token:
            raise RuntimeError(
                "Product Hunt richiede PRODUCTHUNT_TOKEN nel .env "
                "(token developer gratuito: https://api.producthunt.com/v2/docs); "
                "in alternativa disabilita la fonte in config.yaml."
            )
        client = self._get_client()
        try:
            resp = client.post(
                API_URL,
                json={"query": QUERY, "variables": {"first": self.cfg.limit}},
                headers={
                    "Authorization": f"Bearer {self.settings.producthunt_token}"
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                # GraphQL risponde 200 anche in errore: il problema sta nel body.
                messages = "; ".join(
                    str(err.get("message", err)) for err in payload["errors"]
                )
                raise RuntimeError(f"Errore GraphQL da Product Hunt: {messages}")
            edges = ((payload.get("data") or {}).get("posts") or {}).get("edges", [])
            return [
                self._to_item(edge["node"]) for edge in edges if edge.get("node")
            ][: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @staticmethod
    def _to_item(node: dict) -> Item:
        created_raw = node.get("createdAt")
        created_at = None
        if created_raw:
            # ISO 8601 -> naive UTC (convenzione del progetto).
            created_at = (
                datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                .astimezone(UTC)
                .replace(tzinfo=None)
            )
        name = node.get("name") or "(prodotto sconosciuto)"
        tagline = node.get("tagline")
        return Item(
            source=SOURCE_NAME,
            external_id=str(node["id"]),
            # La tagline è la vera descrizione breve: nel titolo aiuta il
            # clustering più del solo nome del prodotto.
            title=f"{name} — {tagline}" if tagline else name,
            # Meglio il sito del prodotto; la pagina PH è il fallback.
            url=node.get("website") or node.get("url"),
            text=node.get("description"),
            author=(node.get("user") or {}).get("username"),
            engagement_json={
                "votes": node.get("votesCount", 0),
                "comments": node.get("commentsCount", 0),
            },
            created_at=created_at,
            raw_json=node,
        )


register_source("producthunt", ProductHuntSource)
