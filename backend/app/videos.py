"""Video sui temi del radar, via YouTube Data API v3.

Non è un collector: i video non entrano nella pipeline, non diventano idee e non
vengono scorati. Sono un pannello di *contesto* — chi sta parlando adesso di ciò
che il radar sta guardando — quindi vivono in un modulo a parte e si prendono
direttamente dall'API a ogni richiesta, con una cache in memoria.

Perché serve una chiave, a differenza di tutto il resto del progetto: i feed RSS
di YouTube danno gli ultimi video di un canale che conosci già, non una ricerca.
Per "chi ne parla sui miei temi" serve ``search.list``, e quella vuole una API
key Google — gratuita, quota 10.000 unità al giorno. Una ricerca costa 100 unità
QUALUNQUE sia il numero di risultati chiesti: chiederne 12 per poterli ordinare
costa esattamente quanto chiederne 2 e sperare.

Senza ``YOUTUBE_API_KEY`` il pannello non è un errore: risponde "non
configurato" e la UI lo dice, come fa Product Hunt col suo token.

## Perché una ricerca ha un ANCORAGGIO separato dalla query

La prima versione confrontava il titolo del video con le keyword del tema
("ai agents, agentic, mcp server") e teneva tutto ciò che superava una soglia.
Due difetti, tutti e due strutturali:

1. *la soglia era sotto il rumore del modello*. Con nomic-embed-text due testi
   PRESI A CASO stanno già a 0.614 di similarità media (misurato in questo
   repo, vedi i commenti di clustering in config.yaml): una soglia a 0.40 non
   scartava nulla, mai. L'unica difesa che ha funzionato è stata la blocklist
   dei canali — cioè il rattoppo, non il meccanismo.
2. *l'ancoraggio era un sacchetto di keyword*. Una lista di termini separati da
   virgole non è una frase, e come embedding vale poco: la similarità finiva per
   misurare "sono entrambi testi tecnici in inglese" invece del tema.

Ora una ricerca è una ``Probe``: la ``query`` è ciò che si chiede a YouTube
(keyword, perché YouTube deve poterle cercare — il label di un'idea è spesso
``@scope/pacchetto``, che come query non esiste), l'``anchor`` è ciò contro cui
si misura la pertinenza, e sono due cose diverse. L'ancoraggio del pannello
sono i label e i sommari delle idee che il radar ha davvero trovato su quel
tema: testo vero, e soprattutto la domanda giusta — "chi parla di ciò che ho
trovato" invece di "chi parla dei miei temi".

## Perché si ORDINA invece di filtrare

Una soglia decide sì/no su un numero la cui distribuzione non conosci: se la
sbagli di poco, o passa tutto o il pannello si svuota. Un ordinamento decide
*chi è più vicino*, che per un pannello di contesto è la domanda giusta: si
chiedono ``candidates`` video, si tengono i ``per_theme`` più pertinenti, e il
pannello non è mai vuoto per colpa di una costante. ``min_similarity`` resta
come *pavimento* — "sotto questo non lo voglio comunque" — ma parte spento: si
accende dopo aver letto le similarità vere nel log, che ci finiscono tutte.
"""

import hashlib
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
# Similarità di un video che non si è potuto giudicare (vettore mancante, Ollama
# giù). Sotto qualunque similarità vera, così i non giudicabili chiudono la fila
# senza essere accusati: restano se c'è posto, non scavalcano chi è stato
# misurato, e il pavimento non li tocca.
UNJUDGED = float("-inf")


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


@dataclass(frozen=True)
class Probe:
    """Una ricerca e il metro con cui se ne giudicano i risultati.

    ``query`` è ciò che YouTube deve poter cercare; ``anchor`` è il testo
    contro cui si misura la pertinenza di ciò che risponde. Tenerli separati è
    il punto: la query deve essere cercabile, l'ancoraggio deve essere
    *significativo*, e quasi mai sono la stessa stringa.
    """

    query: str
    anchor: str
    profile: str | None = None


@dataclass
class _CacheEntry:
    videos: list[Video]
    at: float = field(default_factory=time.monotonic)


_cache: dict[str, _CacheEntry] = {}


def cache_clear() -> None:
    """Svuota la cache (i test non devono vedersi i risultati l'uno dell'altro)."""
    _cache.clear()


def search_params(
    query: str, limit: int, live_only: bool, *, recent: bool = True
) -> dict:
    """Parametri di ``search.list``.

    ``order=relevance`` è l'ordinamento di YouTube per aderenza alla query.
    Prima qui c'era ``viewCount``, ed era lo stesso errore che il README passa
    tre paragrafi a spiegare per GitHub: ordinare per popolarità assoluta
    restituisce i più popolari, non i più pertinenti. Su GitHub la fascia d'età
    salva la query perché le stelle si accumulano lentamente; su YouTube no —
    in una settimana un video seria su "ai agents" fa 5.000 visualizzazioni e
    un gadget per la smart home ne fa due milioni, quindi ``viewCount``
    *garantisce* il contenuto virale. La tendenza la dà la finestra temporale;
    la pertinenza la danno relevance e poi il nostro ordinamento semantico.

    ``recent=False`` toglie la finestra: serve alla ricerca per idea, dove la
    domanda è "cosa dicono di questa cosa" e non "questa settimana" — un'idea
    salita tre settimane fa ha i suoi video di tre settimane fa.
    """
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max(limit, 1), 25),
        "order": "relevance",
        "relevanceLanguage": "en",
        "safeSearch": "none",
    }
    if live_only:
        params["eventType"] = "live"
    elif recent:
        params["publishedAfter"] = _one_week_ago()
    return params


def _one_week_ago() -> str:
    from datetime import datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def probes_for(config: AppConfig, anchors: dict[str, str] | None = None) -> list[Probe]:
    """Una ricerca per tema: keyword come query, ciò che il radar ha trovato come metro.

    ``anchors`` arriva dal chiamante (l'API, che ha la sessione) ed è
    ``profilo -> testo delle idee in cima``. Senza — archivio vuoto, primo
    avvio, run mai fatto — si ripiega sulle keyword: peggio come ancoraggio, ma
    un pannello che funziona al primo avvio vale più di un pannello perfetto
    che pretende uno storico.
    """
    anchors = anchors or {}
    probes: list[Probe] = []
    for profile in config.effective_profiles():
        # Le keyword del profilo in OR: è la stessa scelta del collector
        # GitHub, e per la stessa ragione — un tema per query, non tutto
        # insieme, altrimenti vince il termine più popolare.
        query = " | ".join(profile.keywords[:4])
        anchor = (anchors.get(profile.name) or "").strip() or ", ".join(
            profile.keywords
        )
        probes.append(Probe(query=query, anchor=anchor, profile=profile.name))
    return probes


def rank_by_anchor(
    videos: list[Video],
    anchor_of: Callable[[Video], str | None],
    keep_per_group: int,
    config: AppConfig,
    settings: Settings,
    embedder=None,
) -> tuple[list[Video], int]:
    """Tiene i più pertinenti al proprio ancoraggio. Ritorna (tenuti, scartati).

    Il gruppo è il profilo del video: si tiene il meglio DI OGNI TEMA, non il
    meglio in assoluto, altrimenti il tema che questa settimana ha i video più
    somiglianti si prende tutto il pannello e gli altri spariscono — e un
    pannello che copre un tema solo non risponde più alla domanda che pone.

    Se Ollama non c'è si tengono i primi di ciascun gruppo nell'ordine di
    YouTube: un pannello di contesto ordinato peggio è meglio di un pannello
    morto perché il giudice non poteva giudicare.
    """
    cfg = config.videos
    blocked = [b.lower() for b in cfg.blocked_channels if b.strip()]
    kept = [v for v in videos if not any(b in v.channel.lower() for b in blocked)]
    dropped = len(videos) - len(kept)
    if not kept:
        return kept, dropped

    similarity = _similarities(kept, anchor_of, settings, embedder)

    # Ordine stabile: i gruppi nell'ordine in cui sono arrivati, e dentro
    # ciascuno per similarità decrescente. `sorted` è stabile, quindi a parità
    # (o fra non giudicabili) resta l'ordine di YouTube.
    groups: dict[str | None, list[Video]] = {}
    for video in kept:
        groups.setdefault(video.profile, []).append(video)

    survivors: list[Video] = []
    judged_log: list[str] = []
    for group in groups.values():
        ranked = sorted(group, key=lambda v: similarity[v.video_id], reverse=True)
        for position, video in enumerate(ranked):
            score = similarity[video.video_id]
            # Il pavimento non tocca i non giudicabili: non sono stati misurati,
            # e non si condanna chi non è stato processato.
            too_far = score is not UNJUDGED and score < cfg.min_similarity
            tenuto = position < keep_per_group and not too_far
            if score is not UNJUDGED:
                motivo = "" if tenuto else (" [pavimento]" if too_far else " [posto]")
                judged_log.append(
                    f"{'✓' if tenuto else '✗'} {score:.2f} {video.title[:48]!r}"
                    f" [{video.profile}]{motivo}"
                )
            if tenuto:
                survivors.append(video)
            else:
                dropped += 1
    if judged_log:
        # TUTTE le similarità nel log, non solo gli scarti: il pavimento si
        # accende guardando i numeri dei sopravvissuti, non indovinando.
        logger.info(
            "Video, %d per tema, pavimento %.2f:\n  %s",
            keep_per_group,
            cfg.min_similarity,
            "\n  ".join(judged_log),
        )
    return survivors, dropped


def _similarities(
    videos: list[Video],
    anchor_of: Callable[[Video], str | None],
    settings: Settings,
    embedder=None,
) -> dict[str, float]:
    """Similarità titolo↔ancoraggio per ogni video; ``UNJUDGED`` se non misurabile."""
    from app.embeddings import (
        EmbeddingError,
        OllamaEmbedder,
        cosine,
        text_for_embedding,
    )

    scores: dict[str, float] = {v.video_id: UNJUDGED for v in videos}
    anchors = list({a for a in (anchor_of(v) for v in videos) if a})
    if not anchors:
        return scores

    texts = [text_for_embedding(a) for a in anchors] + [
        text_for_embedding(v.title) for v in videos
    ]
    embedder = embedder or OllamaEmbedder(settings)
    try:
        vectors = embedder.embed_many(texts)
    except EmbeddingError as exc:
        logger.warning("Video senza embedding (%s): tengo l'ordine di YouTube.", exc)
        return scores

    anchor_vec = dict(zip(anchors, vectors[: len(anchors)], strict=True))
    for video, vec in zip(videos, vectors[len(anchors) :], strict=True):
        anchor = anchor_vec.get(anchor_of(video) or "")
        if vec is not None and anchor is not None:
            scores[video.video_id] = cosine(anchor, vec)
    return scores


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
    client: httpx.Client,
    query: str,
    settings: Settings,
    limit: int,
    live_only: bool,
    *,
    recent: bool = True,
) -> list[dict]:
    """Una ricerca, con l'errore che non propaga: un tema fallito non è il pannello."""
    resp = client.get(
        SEARCH_URL,
        params={
            **search_params(query, limit, live_only, recent=recent),
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
    anchor: str | None = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    embedder=None,
) -> dict:
    """Cosa dicono di QUESTA cosa: ricerca on demand sul label di un'idea.

    Il pannello chiede "chi parla dei temi che seguo"; qui la domanda è un'altra
    — "chi parla di questo" — e la risposta vale solo dentro il dossier
    dell'idea. Perché *on demand* e non a ogni apertura: una ricerca costa 100
    unità delle 10.000 al giorno, e un dossier si apre molte più volte di quante
    interessi davvero il video. La cache (15 minuti, la stessa) fa il resto.

    Niente finestra a 7 giorni, qui: il pannello guarda la settimana perché è
    una tendenza, ma "cosa dicono di questa idea" non ha una scadenza — e la
    finestra trasformava in "nessuno ne parla" ogni idea salita più di sette
    giorni fa. Che era il contrario dell'informazione che il dossier voleva
    dare.
    """
    if not settings.youtube_api_key:
        return _not_configured()
    query = label.strip()
    if not query:
        return {"configured": True, "videos": [], "cached": False}
    anchor_text = (anchor or "").strip() or query

    cache_key = f"idea:{query}:{limit}:{_digest(anchor_text)}"
    cached = _cache.get(cache_key) if use_cache else None
    if cached and time.monotonic() - cached.at < CACHE_TTL_SECONDS:
        return {"configured": True, "videos": cached.videos, "cached": True}

    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        entries = _search(
            client,
            query,
            settings,
            max(config.videos.candidates, limit),
            live_only=False,
            recent=False,
        )
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

    collected, dropped = rank_by_anchor(
        collected, lambda _v: anchor_text, limit, config, settings, embedder
    )
    if dropped:
        logger.info("Video per «%s»: %d meno pertinenti lasciati fuori.", query[:48], dropped)
    if use_cache:
        _cache[cache_key] = _CacheEntry(collected)
    return {"configured": True, "videos": collected[:limit], "cached": False}


def trending_videos(
    config: AppConfig,
    settings: Settings,
    *,
    anchors: dict[str, str] | None = None,
    limit: int = 8,
    live_only: bool = False,
    client: httpx.Client | None = None,
    use_cache: bool = True,
    embedder=None,
) -> dict:
    """Video per i temi del radar, un gruppo di risultati per tema.

    Restituisce sempre un dizionario con ``configured``: la UI deve poter dire
    "manca la chiave" invece di mostrare un pannello vuoto senza spiegazione.
    """
    if not settings.youtube_api_key:
        return _not_configured()

    cfg = config.videos
    probes = probes_for(config, anchors)
    per_theme = max(1, min(cfg.per_theme, limit))
    # L'ancoraggio entra nella chiave: cambia a ogni run (sono le idee in cima)
    # e due ancoraggi diversi sono due pannelli diversi, non lo stesso in cache.
    cache_key = (
        f"{live_only}:{limit}:{per_theme}:"
        f"{_digest('|'.join(p.query + '›' + p.anchor for p in probes))}"
    )
    cached = _cache.get(cache_key) if use_cache else None
    if cached and time.monotonic() - cached.at < CACHE_TTL_SECONDS:
        return {"configured": True, "videos": cached.videos, "cached": True}

    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    collected: list[Video] = []
    anchor_by_profile: dict[str | None, str] = {}
    seen: set[str] = set()
    try:
        for probe in probes:
            anchor_by_profile[probe.profile] = probe.anchor
            try:
                # Si chiedono più candidati di quanti se ne tengano: una ricerca
                # costa 100 unità comunque, che se ne chiedano 2 o 12, e senza
                # candidati non c'è niente da ordinare.
                entries = _search(
                    client, probe.query, settings, cfg.candidates, live_only
                )
            except (httpx.HTTPError, ValueError) as exc:
                # Un tema fallito non svuota il pannello: gli altri restano.
                logger.warning("YouTube, tema %r: %s", probe.profile, exc)
                continue
            for entry in entries:
                video = _to_video(entry, probe.profile)
                if video is not None and video.video_id not in seen:
                    seen.add(video.video_id)
                    collected.append(video)
    finally:
        if owns_client:
            client.close()

    # L'ordinamento lavora PRIMA della cache: un fuori tema scartato non deve
    # ripresentarsi gratis per i prossimi 15 minuti.
    collected, dropped = rank_by_anchor(
        collected,
        lambda v: anchor_by_profile.get(v.profile),
        per_theme,
        config,
        settings,
        embedder,
    )
    if dropped:
        logger.info("Pannello video: %d meno pertinenti lasciati fuori.", dropped)

    if use_cache:
        _cache[cache_key] = _CacheEntry(collected)
    return {"configured": True, "videos": collected[:limit], "cached": False}


def _digest(text: str) -> str:
    """Chiave di cache corta e stabile per un testo lungo (gli ancoraggi lo sono)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
