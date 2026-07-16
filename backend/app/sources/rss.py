"""Collector RSS/Atom generico: riviste, blog e forum tech.

Nessuna API a pagamento: quasi tutte le testate e i forum espongono un feed.
Il parsing è fatto a mano con ElementTree per non aggiungere dipendenze e per
gestire RSS 2.0 e Atom con lo stesso codice.

Sul fronte rete il collector è volutamente "educato": molti host (Reddit su
tutti) limitano l'RSS non autenticato e rispondono 429. Per questo usiamo uno
User-Agent credibile, una pausa tra un feed e l'altro e un retry che rispetta
l'header ``Retry-After``. Un 429 residuo resta comunque non fatale: il feed
viene saltato e gli altri proseguono.
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from app.appconfig import AppConfig, SourceConfig
from app.config import Settings
from app.models import Item

logger = logging.getLogger(__name__)

SOURCE_NAME = "rss"
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")

# Uno User-Agent ONESTO e descrittivo è, controintuitivamente, il più affidabile:
# un UA da browser "finto" (dice Chrome ma il TLS è quello di httpx) fa scattare
# i muri anti-bot di Cloudflare & co. — verificato sul campo, dava 404/403/429
# dove questo UA dà 200. Metti pure il tuo repo/contatto reale al posto del link.
USER_AGENT = "idea-radar/0.1 (+https://github.com/idea-radar)"
# Pausa tra una richiesta e l'altra: evita di colpire lo stesso host con feed
# consecutivi (es. i due r/... di fila), causa tipica del 429.
REQUEST_DELAY = 0.5
# Ritenta al massimo N volte su 429; tetto d'attesa per non bloccare il run se
# un server risponde con un Retry-After esagerato.
MAX_RETRIES = 2
MAX_RETRY_WAIT = 30.0
# Backoff di cortesia quando il 429 non porta un Retry-After leggibile.
DEFAULT_RETRY_WAIT = 5.0


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _retry_after_seconds(resp: httpx.Response) -> float:
    """Secondi da attendere su un 429, leggendo l'header ``Retry-After``.

    Il valore può essere un intero (secondi) o una data HTTP; se manca o non è
    interpretabile si usa un backoff di default.
    """
    value = resp.headers.get("Retry-After")
    if not value:
        return DEFAULT_RETRY_WAIT
    value = value.strip()
    try:  # forma "secondi"
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    try:  # forma "data HTTP"
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_WAIT


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:  # RFC 822 (RSS 2.0), es. "Tue, 15 Jul 2026 10:00:00 GMT"
        return parsedate_to_datetime(value).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    try:  # ISO 8601 (Atom)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).replace(tzinfo=None)
    except ValueError:
        return None


class RssSource:
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
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def _get_with_retry(self, client: httpx.Client, url: str) -> httpx.Response:
        """GET con retry su 429 (rispetta ``Retry-After``).

        Alza ``HTTPStatusError`` se dopo i tentativi lo stato resta d'errore,
        così il chiamante può saltare il singolo feed senza fermare gli altri.
        """
        resp = client.get(url)
        for attempt in range(1, MAX_RETRIES + 1):
            if resp.status_code != 429:
                break
            wait = min(_retry_after_seconds(resp), MAX_RETRY_WAIT)
            logger.info(
                "429 su %s: attendo %.1fs e ritento (%d/%d)",
                url,
                wait,
                attempt,
                MAX_RETRIES,
            )
            time.sleep(wait)
            resp = client.get(url)
        resp.raise_for_status()
        return resp

    def fetch(self) -> list[Item]:
        client = self._get_client()
        items: list[Item] = []
        try:
            per_feed = max(1, self.cfg.limit // max(len(self.cfg.feeds), 1))
            for index, feed_url in enumerate(self.cfg.feeds):
                if index > 0:
                    # Educati con gli host: niente raffiche di richieste.
                    time.sleep(REQUEST_DELAY)
                try:
                    resp = self._get_with_retry(client, feed_url)
                    items.extend(self._parse_feed(resp.text, feed_url)[:per_feed])
                except (httpx.HTTPError, ElementTree.ParseError) as exc:
                    # Un feed rotto non deve far saltare tutti gli altri.
                    logger.warning("Feed %s non leggibile: %s", feed_url, exc)
            return items[: self.cfg.limit]
        finally:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    @classmethod
    def _parse_feed(cls, xml_text: str, feed_url: str) -> list[Item]:
        root = ElementTree.fromstring(xml_text)
        entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
        return [
            item
            for item in (cls._to_item(entry, feed_url) for entry in entries)
            if item is not None
        ]

    @staticmethod
    def _to_item(entry: ElementTree.Element, feed_url: str) -> Item | None:
        def text_of(*tags: str) -> str | None:
            for tag in tags:
                node = entry.find(tag)
                if node is not None and (node.text or "").strip():
                    return node.text.strip()
            return None

        title = text_of("title", f"{_ATOM}title")
        if not title:
            return None

        link = text_of("link")
        if not link:  # Atom: <link href="..."/>
            node = entry.find(f"{_ATOM}link")
            link = node.get("href") if node is not None else None

        body = text_of(
            "description",
            "{http://purl.org/rss/1.0/modules/content/}encoded",
            f"{_ATOM}summary",
            f"{_ATOM}content",
        )
        guid = text_of("guid", f"{_ATOM}id") or link or title
        published = text_of("pubDate", f"{_ATOM}published", f"{_ATOM}updated")
        author = text_of("author", "{http://purl.org/dc/elements/1.1/}creator")
        if author is None:
            node = entry.find(f"{_ATOM}author/{_ATOM}name")
            author = node.text.strip() if node is not None and node.text else None

        # I feed non danno engagement: l'id stabile è l'hash del guid.
        external_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16]
        return Item(
            source=SOURCE_NAME,
            external_id=external_id,
            title=_strip_html(title)[:300],
            url=link,
            text=_strip_html(body)[:2000] if body else None,
            author=author,
            engagement_json={},
            created_at=_parse_date(published),
            raw_json={"feed": feed_url, "guid": guid},
        )
