"""Video in tendenza sui temi del radar, via YouTube Data API v3.

Non è un collector: i video non entrano nella pipeline, non diventano idee e non
vengono scorati. Sono un pannello di *contesto* — chi sta parlando adesso di ciò
che il radar sta guardando — quindi vivono in un modulo a parte e si prendono
direttamente dall'API a ogni richiesta, con una cache in memoria.

Perché serve una chiave, a differenza di tutto il resto del progetto: i feed RSS
di YouTube danno gli ultimi video di un canale che conosci già, non una ricerca.
Per "in tendenza sui miei temi" serve ``search.list``, e quella vuole una API key
Google — gratuita, quota 10.000 unità al giorno. Una ricerca costa 100 unità,
quindi anche interrogando ogni profilo a ogni apertura della pagina si sta dentro
con ampio margine; la cache serve a non sprecarla comunque.

Senza ``YOUTUBE_API_KEY`` il pannello non è un errore: risponde "non
configurato" e la UI lo dice, come fa Product Hunt col suo token.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC

import httpx

from app.appconfig import AppConfig
from app.config import Settings
from app.sources.base import USER_AGENT, clean_html_text

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT = 20.0
# Una ricerca costa 100 unità su 10.000 al giorno: la cache tiene i conti bassi
# anche se la pagina viene riaperta continuamente.
CACHE_TTL_SECONDS = 900.0


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    channel: str
    published_at: str
    thumbnail: str
    live: bool
    profile: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def embed_url(self) -> str:
        return f"https://www.youtube-nocookie.com/embed/{self.video_id}"


@dataclass
class _CacheEntry:
    videos: list[Video]
    at: float = field(default_factory=time.monotonic)


_cache: dict[str, _CacheEntry] = {}


def cache_clear() -> None:
    """Svuota la cache (i test non devono vedersi i risultati l'uno dell'altro)."""
    _cache.clear()


def search_params(query: str, limit: int, live_only: bool) -> dict:
    """Parametri di ``search.list``.

    ``order=viewCount`` su una finestra recente è ciò che si avvicina di più a
    "in tendenza": l'API non ha un ordinamento per tendenza, e ``relevance``
    darebbe i video più visti di sempre — lo stesso errore che teneva la fonte
    GitHub ferma sui repo più stellati della storia.
    """
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max(limit, 1), 25),
        "order": "viewCount",
        "relevanceLanguage": "en",
        "safeSearch": "none",
    }
    if live_only:
        params["eventType"] = "live"
    else:
        # Solo l'ultima settimana: senza vincolo temporale "più visti" significa
        # i video più visti di sempre, che non è una tendenza.
        params["publishedAfter"] = _one_week_ago()
    return params


def _one_week_ago() -> str:
    from datetime import datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=7)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _to_video(entry: dict, profile: str | None) -> Video | None:
    video_id = ((entry.get("id") or {}).get("videoId")) or None
    snippet = entry.get("snippet") or {}
    title = clean_html_text(snippet.get("title") or "")
    if not video_id or not title:
        return None
    thumbs = snippet.get("thumbnails") or {}
    best = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
    return Video(
        video_id=str(video_id),
        title=title[:200],
        channel=clean_html_text(snippet.get("channelTitle") or "")[:120],
        published_at=str(snippet.get("publishedAt") or ""),
        thumbnail=str(best.get("url") or ""),
        live=snippet.get("liveBroadcastContent") == "live",
        profile=profile,
    )


def _filter_by_anchor(
    videos: list[Video],
    anchor_of: Callable[[Video], str | None],
    config: AppConfig,
    settings: Settings,
    embedder=None,
) -> tuple[list[Video], int]:
    """Tiene i video pertinenti al proprio ancoraggio. Ritorna (tenuti, scartati).

    ``anchor_of`` dice a quale testo va confrontato ciascun video: le keyword
    del suo tema per il pannello, il label dell'idea per la ricerca puntuale.
    Il resto è comune — blocklist prima (gratis), poi un solo batch di
    embedding per tutto — e comune deve restare: sono lo stesso giudizio.

    Se Ollama non c'è si tiene tutto: un pannello di contesto non pertinente al
    100% è meglio di un pannello morto perché il filtro non poteva giudicare.
    """
    cfg = config.videos
    blocked = [b.lower() for b in cfg.blocked_channels if b.strip()]
    kept = [
        v for v in videos if not any(b in v.channel.lower() for b in blocked)
    ]
    dropped = len(videos) - len(kept)
    if cfg.min_similarity <= 0 or not kept:
        return kept, dropped

    from app.embeddings import (
        EmbeddingError,
        OllamaEmbedder,
        cosine,
        text_for_embedding,
    )

    anchors = list({a for a in (anchor_of(v) for v in kept) if a})
    if not anchors:
        return kept, dropped
    texts = [text_for_embedding(a) for a in anchors] + [
        text_for_embedding(v.title) for v in kept
    ]
    embedder = embedder or OllamaEmbedder(settings)
    try:
        vectors = embedder.embed_many(texts)
    except EmbeddingError as exc:
        logger.warning("Filtro video senza embedding (%s): tengo tutto.", exc)
        return kept, dropped

    anchor_vec = dict(zip(anchors, vectors[: len(anchors)], strict=True))
    survivors: list[Video] = []
    judged: list[str] = []
    for video, vec in zip(kept, vectors[len(anchors) :], strict=True):
        anchor = anchor_vec.get(anchor_of(video) or "")
        if vec is None or anchor is None:
            survivors.append(video)  # non giudicabile ≠ colpevole
            continue
        similarity = cosine(anchor, vec)
        tenuto = similarity >= cfg.min_similarity
        # TUTTE le similarità nel log, non solo gli scarti: la soglia si tara
        # guardando i numeri dei sopravvissuti, non indovinando.
        judged.append(
            f"{'✓' if tenuto else '✗'} {similarity:.2f} {video.title[:48]!r} [{video.profile}]"
        )
        if tenuto:
            survivors.append(video)
        else:
            dropped += 1
    if judged:
        logger.info(
            "Pannello video, soglia %.2f:\n  %s",
            cfg.min_similarity,
            "\n  ".join(judged),
        )
    return survivors, dropped


def filter_relevant(
    videos: list[Video],
    config: AppConfig,
    settings: Settings,
    embedder=None,
) -> tuple[list[Video], int]:
    """Pertinenza al tema del video: il confronto è con le keyword del profilo."""
    themes = {
        p.name: ", ".join(p.keywords) for p in config.effective_profiles()
    }
    return _filter_by_anchor(
        videos, lambda v: themes.get(v.profile or ""), config, settings, embedder
    )


def _not_configured() -> dict:
    return {
        "configured": False,
        "videos": [],
        "detail": (
            "Serve YOUTUBE_API_KEY in backend/.env (chiave gratuita: "
            "console.cloud.google.com, API YouTube Data v3)."
        ),
    }


def _search(
    client: httpx.Client, query: str, settings: Settings, limit: int, live_only: bool
) -> list[dict]:
    """Una ricerca, con l'errore che non propaga: un tema fallito non è il pannello."""
    resp = client.get(
        SEARCH_URL,
        params={
            **search_params(query, limit, live_only),
            "key": settings.youtube_api_key,
        },
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def videos_for_idea(
    label: str,
    config: AppConfig,
    settings: Settings,
    *,
    limit: int = 4,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    embedder=None,
) -> dict:
    """Cosa dicono di QUESTA cosa: ricerca on demand sul label di un'idea.

    Il pannello generale chiede "chi parla dei miei temi"; qui la domanda è
    un'altra — "chi parla di questo" — e la risposta vale solo dentro il
    dossier dell'idea. Perché *on demand* e non a ogni apertura: una ricerca
    costa 100 unità delle 10.000 al giorno, e un dossier si apre molte più
    volte di quante interessi davvero il video. La cache (15 minuti, la stessa)
    fa il resto.

    Il filtro di pertinenza qui ha un ancoraggio solo — il label — perché è
    esattamente ciò che si è cercato: se il titolo del video non gli somiglia,
    YouTube ha risposto d'altro.
    """
    if not settings.youtube_api_key:
        return _not_configured()
    query = label.strip()
    if not query:
        return {"configured": True, "videos": [], "cached": False}

    cache_key = f"idea:{query}:{limit}"
    cached = _cache.get(cache_key) if use_cache else None
    if cached and time.monotonic() - cached.at < CACHE_TTL_SECONDS:
        return {"configured": True, "videos": cached.videos, "cached": True}

    owns_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
    )
    try:
        entries = _search(client, query, settings, limit, live_only=False)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("YouTube, idea %r: %s", query[:60], exc)
        return {"configured": True, "videos": [], "cached": False}
    finally:
        if owns_client:
            client.close()

    collected: list[Video] = []
    seen: set[str] = set()
    for entry in entries:
        video = _to_video(entry, None)
        if video is not None and video.video_id not in seen:
            seen.add(video.video_id)
            collected.append(video)

    collected, dropped = _filter_by_anchor(
        collected, lambda _v: query, config, settings, embedder
    )
    if dropped:
        logger.info("Video per «%s»: %d fuori tema scartati.", query[:48], dropped)
    if use_cache:
        _cache[cache_key] = _CacheEntry(collected)
    return {"configured": True, "videos": collected[:limit], "cached": False}


def trending_videos(
    config: AppConfig,
    settings: Settings,
    *,
    limit: int = 8,
    live_only: bool = False,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    embedder=None,
) -> dict:
    """Video per i temi del radar, un gruppo di risultati per profilo.

    Restituisce sempre un dizionario con ``configured``: la UI deve poter dire
    "manca la chiave" invece di mostrare un pannello vuoto senza spiegazione.
    """
    if not settings.youtube_api_key:
        return _not_configured()

    profiles = config.effective_profiles()
    per_profile = max(1, limit // max(len(profiles), 1))
    cache_key = f"{live_only}:{limit}:{','.join(p.name for p in profiles)}"
    cached = _cache.get(cache_key) if use_cache else None
    if cached and time.monotonic() - cached.at < CACHE_TTL_SECONDS:
        return {"configured": True, "videos": cached.videos, "cached": True}

    owns_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
    )
    collected: list[Video] = []
    seen: set[str] = set()
    try:
        for profile in profiles:
            # Le keyword del profilo in OR: è la stessa scelta del collector
            # GitHub, e per la stessa ragione — un tema per query, non tutto
            # insieme, altrimenti vince il termine più popolare.
            query = " | ".join(profile.keywords[:4])
            try:
                entries = _search(client, query, settings, per_profile, live_only)
            except (httpx.HTTPError, ValueError) as exc:
                # Un tema fallito non svuota il pannello: gli altri restano.
                logger.warning("YouTube, tema %r: %s", profile.name, exc)
                continue
            for entry in entries:
                video = _to_video(entry, profile.name)
                if video is not None and video.video_id not in seen:
                    seen.add(video.video_id)
                    collected.append(video)
    finally:
        if owns_client:
            client.close()

    # Il filtro di pertinenza lavora PRIMA della cache: un fuori tema scartato
    # non deve ripresentarsi gratis per i prossimi 15 minuti.
    collected, dropped = filter_relevant(collected, config, settings, embedder)
    if dropped:
        logger.info("Pannello video: %d fuori tema scartati.", dropped)

    if use_cache:
        _cache[cache_key] = _CacheEntry(collected)
    return {"configured": True, "videos": collected[:limit], "cached": False}
