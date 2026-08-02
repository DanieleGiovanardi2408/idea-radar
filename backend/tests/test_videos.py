"""Video in tendenza: contesto, non segnali.

Non entrano nella pipeline, non diventano idee, non vengono scorati. E senza la
chiave il pannello deve spegnersi dicendo perché, non sembrare rotto.
"""

import httpx

from app.appconfig import AppConfig, ProfileConfig, ScoringConfig, VideosConfig
from app.config import Settings
from app.embeddings import EmbeddingError, text_for_embedding
from app.videos import (
    Video,
    cache_clear,
    filter_relevant,
    search_params,
    trending_videos,
)


def _config(videos: VideosConfig | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        profiles=[
            ProfileConfig(name="agenti", label="Agenti AI", keywords=["ai agents"]),
            ProfileConfig(name="domotica", label="Domotica", keywords=["home assistant"]),
        ],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        # Nei test del pannello il filtro embedding resta spento: ha i suoi
        # test dedicati sotto, con un embedder finto.
        videos=videos or VideosConfig(min_similarity=0.0),
    )


def _entry(video_id: str, title: str = "Un video", live: bool = False) -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "title": title,
            "channelTitle": "Canale Tech",
            "publishedAt": "2026-07-26T10:00:00Z",
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/{video_id}.jpg"}},
            "liveBroadcastContent": "live" if live else "none",
        },
    }


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_without_the_key_the_panel_explains_itself() -> None:
    """Non un errore e non un pannello vuoto: un messaggio che dice cosa fare."""
    cache_clear()
    result = trending_videos(_config(), Settings(youtube_api_key=""))

    assert result["configured"] is False
    assert result["videos"] == []
    assert "YOUTUBE_API_KEY" in result["detail"]


def test_recent_and_most_watched_not_most_watched_ever() -> None:
    """`order=viewCount` senza finestra darebbe i video più visti della storia.

    È lo stesso errore che teneva la fonte GitHub sui repo più stellati di sempre:
    i più popolari in assoluto non sono una tendenza.
    """
    params = search_params("ai agents", 5, live_only=False)

    assert params["order"] == "viewCount"
    assert "publishedAfter" in params  # la finestra è ciò che rende "tendenza"
    assert params["type"] == "video"


def test_live_only_asks_for_live_and_drops_the_time_window() -> None:
    """Una diretta è in corso ADESSO: filtrarla per data di pubblicazione no."""
    params = search_params("ai agents", 5, live_only=True)

    assert params["eventType"] == "live"
    assert "publishedAfter" not in params


def test_one_query_per_theme_with_its_keywords() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(200, json={"items": []})

    cache_clear()
    trending_videos(
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert seen == ["ai agents", "home assistant"]  # un tema per query


def test_videos_carry_the_profile_that_found_them() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        which = request.url.params["q"]
        return httpx.Response(
            200, json={"items": [_entry(f"id-{which.replace(' ', '-')}")]}
        )

    cache_clear()
    result = trending_videos(
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )
    by_profile = {v.profile: v for v in result["videos"]}

    assert set(by_profile) == {"agenti", "domotica"}
    assert by_profile["agenti"].url.endswith("watch?v=id-ai-agents")
    # nocookie: il pannello non deve piazzare cookie di Google sul tuo radar
    assert "youtube-nocookie.com/embed/" in by_profile["agenti"].embed_url


def test_a_failing_theme_does_not_empty_the_panel() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "ai agents" in request.url.params["q"]:
            return httpx.Response(403, json={"error": "quota"})
        return httpx.Response(200, json={"items": [_entry("ok")]})

    cache_clear()
    result = trending_videos(
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert [v.video_id for v in result["videos"]] == ["ok"]


def test_duplicates_across_themes_appear_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_entry("condiviso")]})

    cache_clear()
    result = trending_videos(
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert [v.video_id for v in result["videos"]] == ["condiviso"]


def test_the_cache_spares_the_quota() -> None:
    """Una ricerca costa 100 unità su 10.000 al giorno: riaprire la pagina non
    deve consumarne una nuova."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"items": [_entry("uno")]})

    cache_clear()
    settings = Settings(youtube_api_key="k")
    first = trending_videos(_config(), settings, client=_client(handler))
    second = trending_videos(_config(), settings, client=_client(handler))

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 2  # due temi al primo giro, zero al secondo


def test_a_malformed_entry_is_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": {}, "snippet": {}}, _entry("buono")]},
        )

    cache_clear()
    result = trending_videos(
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert [v.video_id for v in result["videos"]] == ["buono"]


# ---- filtro di pertinenza ----------------------------------------------------


class _FakeEmbedder:
    """Embedder deterministico: vettori decisi dal test, per testo."""

    def __init__(self, mapping: dict[str, list[float]], fail: bool = False):
        self.mapping = mapping
        self.fail = fail

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        if self.fail:
            raise EmbeddingError("Ollama giù")
        return [self.mapping.get(t) for t in texts]


def _video(title: str, profile: str = "agenti", channel: str = "Canale Tech") -> Video:
    return Video(
        video_id=title[:8],
        title=title,
        channel=channel,
        published_at="2026-07-26T10:00:00Z",
        thumbnail="",
        live=False,
        profile=profile,
    )


def _filter_config() -> AppConfig:
    return _config(videos=VideosConfig(min_similarity=0.5))


def _mapping() -> dict[str, list[float]]:
    return {
        # I due temi: assi ortogonali.
        text_for_embedding("ai agents"): [1.0, 0.0],
        text_for_embedding("home assistant"): [0.0, 1.0],
        # Titoli: uno centrato sul suo tema, uno lontano dal suo (la domotica
        # è l'asse y: il vettore di Peppa Pig punta quasi tutto altrove).
        text_for_embedding("Building AI agents from scratch"): [0.95, 0.05],
        text_for_embedding("Peppa Pig Gets a BRAND NEW SMART TV"): [0.95, 0.1],
    }


def test_filtro_scarta_il_fuori_tema_e_tiene_il_pertinente() -> None:
    videos = [
        _video("Building AI agents from scratch"),
        _video("Peppa Pig Gets a BRAND NEW SMART TV", profile="domotica"),
    ]
    kept, dropped = filter_relevant(
        videos, _filter_config(), Settings(), _FakeEmbedder(_mapping())
    )
    assert [v.title for v in kept] == ["Building AI agents from scratch"]
    assert dropped == 1


def test_filtro_senza_ollama_tiene_tutto() -> None:
    """Un pannello di contesto imperfetto è meglio di un pannello morto."""
    videos = [_video("Peppa Pig Gets a BRAND NEW SMART TV", profile="domotica")]
    kept, dropped = filter_relevant(
        videos, _filter_config(), Settings(), _FakeEmbedder({}, fail=True)
    )
    assert len(kept) == 1
    assert dropped == 0


def test_filtro_non_giudicabile_non_e_colpevole() -> None:
    """Vettore mancante per un titolo (o tema ignoto): il video resta."""
    videos = [_video("Titolo mai embeddato"), _video("Altro", profile=None)]
    kept, _ = filter_relevant(
        videos, _filter_config(), Settings(), _FakeEmbedder(_mapping())
    )
    assert len(kept) == 2


def test_blocklist_canali_lavora_anche_senza_embedding() -> None:
    config = _config(
        videos=VideosConfig(min_similarity=0.0, blocked_channels=["peppa pig"])
    )
    videos = [
        _video("Un video qualsiasi", channel="Peppa Pig's Big Adventures"),
        _video("Building AI agents from scratch"),
    ]
    kept, dropped = filter_relevant(videos, config, Settings(), None)
    assert [v.channel for v in kept] == ["Canale Tech"]
    assert dropped == 1
