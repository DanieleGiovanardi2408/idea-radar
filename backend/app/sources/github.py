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
from collections.abc import Callable
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

# I due limiti della Search API non sono una differenza di gentilezza.
# Senza token GitHub concede 10 richieste al minuto, col token 30. Un run con 4
# profili e 3 fasce ne fa 12: autenticato sta larghissimo, anonimo sfonda al
# decimo e le ultime richieste tornano 403. È esattamente quello che è successo
# nel run 56 — tre fasce perse, tutte nella terza — con GITHUB_TOKEN vuoto.
# Il ritmo si deriva quindi dal limite vero, invece di essere una costante.
SEARCH_RPM_ANON = 10
SEARCH_RPM_AUTH = 30
# Margine: si sta sotto al limite, non esattamente sul bordo.
RPM_SAFETY = 1.1

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
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = source_cfg
        self.app_config = app_config
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        # Iniettabile: un test sul ritmo non deve durare un minuto per davvero.
        self._sleep = sleeper
        # Cosa è andato storto, per il Monitor. Una fascia persa in silenzio è
        # il motivo per cui i 403 sono passati inosservati per settimane.
        self.last_report: dict[str, int] = {}

    @property
    def authenticated(self) -> bool:
        return bool(self.settings.github_token)

    def _min_delay(self) -> float:
        """Secondi tra due richieste per stare dentro il limite del minuto."""
        rpm = SEARCH_RPM_AUTH if self.authenticated else SEARCH_RPM_ANON
        return (60.0 / rpm) * RPM_SAFETY

    def _wait_for_reset(self, resp: httpx.Response) -> float:
        """Quanto attendere secondo GitHub, entro il tetto configurato.

        GitHub dice sempre quando riprovare: ``Retry-After`` sui limiti
        secondari, ``x-ratelimit-reset`` (epoch) su quello del minuto. Prima si
        ignoravano entrambi e la richiesta veniva semplicemente buttata.
        """
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), self.cfg.max_wait_seconds)
            except ValueError:
                pass
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            try:
                attesa = float(reset) - time.time()
                if attesa > 0:
                    return min(attesa, self.cfg.max_wait_seconds)
            except ValueError:
                pass
        # Nessun header utile: si aspetta un giro di ritmo, non zero.
        return min(self._min_delay(), self.cfg.max_wait_seconds)

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
        richieste = 0
        fallite = 0
        attese = 0.0

        if not self.authenticated:
            logger.warning(
                "GitHub senza token: %d richieste/minuto invece di %d, quindi "
                "%.1fs tra una query e l'altra (%d query in questo run). "
                "Un token gratuito in GITHUB_TOKEN triplica il ritmo.",
                SEARCH_RPM_ANON,
                SEARCH_RPM_AUTH,
                self._min_delay(),
                len(bands) * len(groups),
            )

        try:
            for band in bands:
                in_band: dict[str, Item] = {}
                for keywords in groups:
                    # Il ritmo si tiene PRIMA della richiesta, tranne la prima:
                    # è quello che evita il 403, invece di curarlo dopo.
                    if richieste > 0:
                        self._sleep(self._min_delay())
                    repos, extra_wait, fallita = self._search(
                        client, keywords, band, per_query
                    )
                    richieste += 1
                    attese += extra_wait
                    if fallita:
                        fallite += 1
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
            # Il report esce anche se qualcosa è saltato: serve soprattutto lì.
            self.last_report = {
                "requests": richieste,
                "failed_queries": fallite,
                "waited_seconds": round(attese, 1),
            }
            if fallite:
                logger.warning(
                    "GitHub: %d query su %d perse anche dopo l'attesa. "
                    "Le fasce d'età che rappresentavano non hanno portato nulla%s.",
                    fallite,
                    richieste,
                    "" if self.authenticated else " (e il token manca)",
                )
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    def _search(
        self,
        client: httpx.Client,
        keywords: list[str],
        band: tuple[int, int],
        per_query: int,
    ) -> tuple[list[dict], float, bool]:
        """Una query, con UN ritentativo se GitHub dice quando riprovare.

        Restituisce (repo, secondi attesi, fallita). Un solo ritentativo di
        proposito: se il minuto è pieno l'attesa risolve, se il token manca del
        tutto insistere allunga il run senza cambiare l'esito — e il report dice
        cosa si è perso, che è meglio di un secondo tentativo cieco.
        """
        atteso = 0.0
        for tentativo in (1, 2):
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
                # 403 e 429 sono i due modi in cui GitHub dice "troppe": il
                # primo per il limite del minuto, il secondo per quelli
                # secondari. Entrambi portano con sé quando riprovare.
                if resp.status_code in (403, 429) and tentativo == 1:
                    attesa = self._wait_for_reset(resp)
                    logger.info(
                        "GitHub ha chiesto di attendere %.1fs (tema %r fascia %s): "
                        "aspetto invece di perdere la fascia.",
                        attesa,
                        keywords,
                        band,
                    )
                    self._sleep(attesa)
                    atteso += attesa
                    continue
                resp.raise_for_status()
                return resp.json().get("items", []), atteso, False
            except (httpx.HTTPError, ValueError) as exc:
                if tentativo == 2:
                    logger.warning(
                        "GitHub, tema %r fascia %s: %s", keywords, band, exc
                    )
                    return [], atteso, True
                # Errore non di quota al primo giro: si riprova una volta dopo
                # un giro di ritmo (una rete che sfarfalla non è un limite).
                self._sleep(self._min_delay())
                atteso += self._min_delay()
        return [], atteso, True

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
