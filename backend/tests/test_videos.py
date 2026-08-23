"""Video: contesto, non segnali.

Non entrano nella pipeline, non diventano idee, non vengono scorati. E senza la
chiave il pannello deve spegnersi dicendo perché, non sembrare rotto.

Sulla pertinenza questi test coprono la scelta di fondo: si ORDINA, non si
filtra. Una soglia decide sì/no su un numero la cui distribuzione non conosci —
ed è esattamente così che il filtro precedente non ha mai scartato niente, con
un `min_similarity` a 0.40 sotto la similarità media di due testi qualsiasi
(0.614 con nomic-embed-text, misurata in questo repo). Un ordinamento decide
chi è più vicino, e quella domanda ha sempre una risposta.
"""

import httpx

from app.appconfig import AppConfig, ProfileConfig, ScoringConfig, VideosConfig
from app.config import Settings
from app.embeddings import EmbeddingError, text_for_embedding
from app.videos import (
    Video,
    cache_clear,
    probes_for,
    rank_by_anchor,
    search_params,
    trending_videos,
    videos_for_idea,
)


def _config(videos: VideosConfig | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        profiles=[
            ProfileConfig(name="agenti", label="Agenti AI", keywords=["ai agents"]),
            ProfileConfig(
                name="domotica", label="Domotica", keywords=["home assistant"]
            ),
        ],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        # Nei test del pannello l'ordinamento semantico resta fuori gioco: ha i
        # suoi test dedicati sotto, con un embedder finto.
        videos=videos or VideosConfig(),
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


def test_si_ordina_per_pertinenza_non_per_visualizzazioni() -> None:
    """`order=viewCount` è il `sort:stars` di YouTube.

    Il README passa tre paragrafi a spiegare perché ordinare GitHub per stelle
    restituisce mercati chiusi; ordinare YouTube per visualizzazioni ha lo
    stesso difetto e per giunta peggiore, perché in una settimana un video
    tecnico fa cinquemila visualizzazioni e un gadget virale due milioni. La
    tendenza la dà la finestra temporale, non l'ordinamento.
    """
    params = search_params("ai agents", 5, live_only=False)

    assert params["order"] == "relevance"
    assert "publishedAfter" in params  # la finestra è ciò che rende "tendenza"
    assert params["type"] == "video"


def test_live_only_asks_for_live_and_drops_the_time_window() -> None:
    """Una diretta è in corso ADESSO: filtrarla per data di pubblicazione no."""
    params = search_params("ai agents", 5, live_only=True)

    assert params["eventType"] == "live"
    assert "publishedAfter" not in params


def test_senza_recent_non_ci_si_limita_alla_settimana() -> None:
    """La ricerca per idea non ha una scadenza: la finestra è del pannello."""
    assert "publishedAfter" not in search_params("x", 5, False, recent=False)


def test_one_query_per_theme_with_its_keywords() -> None:
    """La QUERY resta di keyword: YouTube deve poterla cercare.

    Il label di un'idea è spesso `@scope/pacchetto` o "Show HN: …", che come
    query non esiste. Cambia l'ancoraggio, non ciò che si chiede.
    """
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


def test_si_chiedono_piu_candidati_di_quanti_se_ne_tengano() -> None:
    """Una ricerca costa 100 unità che se ne chiedano 2 o 12: senza candidati
    non c'è niente da ordinare, e risparmiare qui non risparmia nulla."""
    chiesti: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chiesti.append(int(request.url.params["maxResults"]))
        return httpx.Response(200, json={"items": []})

    cache_clear()
    config = _config(videos=VideosConfig(per_theme=2, candidates=12))
    trending_videos(
        config,
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert chiesti == [12, 12]


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


def test_un_ancoraggio_nuovo_non_riusa_la_cache_del_vecchio() -> None:
    """L'ancoraggio sono le idee in cima: cambia a ogni run, e con esso il
    giudizio. Servire dalla cache il pannello di ieri sarebbe servire una
    pertinenza misurata su idee che non sono più lì."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"items": [_entry("uno")]})

    cache_clear()
    settings = Settings(youtube_api_key="k")
    trending_videos(
        _config(), settings, anchors={"agenti": "runtime per agenti"},
        client=_client(handler),
    )
    trending_videos(
        _config(), settings, anchors={"agenti": "tutt'altra cosa"},
        client=_client(handler),
    )

    assert calls["n"] == 4  # due temi per due ancoraggi diversi


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


# ---- l'ancoraggio: contro cosa si misura la pertinenza -----------------------


def test_l_ancoraggio_sono_le_idee_trovate_non_le_keyword() -> None:
    """La domanda del pannello è "chi parla di ciò che ho trovato"."""
    probes = probes_for(
        _config(), {"agenti": "Runtime self-hosted per agenti. Gira in locale."}
    )
    per_tema = {p.profile: p for p in probes}

    assert per_tema["agenti"].query == "ai agents"  # cercabile
    assert "Runtime self-hosted" in per_tema["agenti"].anchor  # misurabile


def test_senza_idee_si_ripiega_sulle_keyword() -> None:
    """Al primo avvio non c'è nessun run: un pannello che funziona subito vale
    più di uno perfetto che pretende uno storico."""
    probes = probes_for(_config(), {})

    assert [p.anchor for p in probes] == ["ai agents", "home assistant"]


# ---- pertinenza per ordinamento ---------------------------------------------


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
        video_id=title[:12],
        title=title,
        channel=channel,
        published_at="2026-07-26T10:00:00Z",
        thumbnail="",
        live=False,
        profile=profile,
    )


# Un asse per tema; i titoli si allontanano dal proprio asse quanto basta a
# ordinarli. Le similarità che ne escono: vicinissimo 1.00, vicino 0.89,
# lontano 0.32.
_MAPPING = {
    text_for_embedding("agenti"): [1.0, 0.0],
    text_for_embedding("domotica"): [0.0, 1.0],
    text_for_embedding("vicinissimo"): [1.0, 0.02],
    text_for_embedding("vicino"): [1.0, 0.5],
    text_for_embedding("lontano"): [1.0, 3.0],
    text_for_embedding("domotico"): [0.1, 1.0],
}


def _anchor_of(video: Video) -> str | None:
    return video.profile


def test_si_tengono_i_piu_vicini_non_quelli_sopra_una_soglia() -> None:
    videos = [_video("lontano"), _video("vicinissimo"), _video("vicino")]

    kept, dropped = rank_by_anchor(
        videos, _anchor_of, 2, _config(), Settings(), _FakeEmbedder(_MAPPING)
    )

    assert [v.title for v in kept] == ["vicinissimo", "vicino"]
    assert dropped == 1


def test_ogni_tema_tiene_i_suoi() -> None:
    """Il meglio DI OGNI tema, non il meglio in assoluto: altrimenti il tema
    più video-genico della settimana si prende tutto il pannello e gli altri
    spariscono — e un pannello che copre un tema solo non risponde più alla
    domanda che pone."""
    videos = [
        _video("vicinissimo"),
        _video("vicino"),
        _video("domotico", profile="domotica"),
    ]

    kept, _ = rank_by_anchor(
        videos, _anchor_of, 1, _config(), Settings(), _FakeEmbedder(_MAPPING)
    )

    assert {v.profile for v in kept} == {"agenti", "domotica"}


def test_il_pavimento_e_spento_di_default() -> None:
    """Una costante si accende dopo aver letto i numeri veri, non prima."""
    videos = [_video("vicinissimo"), _video("lontano")]

    kept, dropped = rank_by_anchor(
        videos, _anchor_of, 5, _config(), Settings(), _FakeEmbedder(_MAPPING)
    )

    assert len(kept) == 2 and dropped == 0


def test_il_pavimento_acceso_taglia_anche_se_ci_sarebbe_posto() -> None:
    config = _config(videos=VideosConfig(min_similarity=0.5))
    videos = [_video("vicinissimo"), _video("lontano")]  # 1.00 e 0.32

    kept, dropped = rank_by_anchor(
        videos, _anchor_of, 5, config, Settings(), _FakeEmbedder(_MAPPING)
    )

    assert [v.title for v in kept] == ["vicinissimo"]
    assert dropped == 1


def test_senza_ollama_si_tiene_l_ordine_di_youtube() -> None:
    """Un pannello ordinato peggio è meglio di un pannello morto perché il
    giudice non poteva giudicare."""
    videos = [_video("primo"), _video("secondo"), _video("terzo")]

    kept, _ = rank_by_anchor(
        videos, _anchor_of, 2, _config(), Settings(), _FakeEmbedder({}, fail=True)
    )

    assert [v.title for v in kept] == ["primo", "secondo"]


def test_non_giudicabile_non_e_colpevole_ma_chiude_la_fila() -> None:
    """Vettore mancante: il video resta se c'è posto, ma non scavalca chi è
    stato misurato — e il pavimento non lo tocca, perché non è stato giudicato."""
    config = _config(videos=VideosConfig(min_similarity=0.9))
    videos = [_video("mai visto prima"), _video("vicinissimo")]

    kept, _ = rank_by_anchor(
        videos, _anchor_of, 2, config, Settings(), _FakeEmbedder(_MAPPING)
    )

    assert [v.title for v in kept] == ["vicinissimo", "mai visto prima"]


def test_blocklist_canali_lavora_anche_senza_embedding() -> None:
    config = _config(videos=VideosConfig(blocked_channels=["peppa pig"]))
    videos = [
        _video("Un video qualsiasi", channel="Peppa Pig's Big Adventures"),
        _video("vicinissimo"),
    ]

    kept, dropped = rank_by_anchor(videos, _anchor_of, 5, config, Settings(), None)

    assert [v.channel for v in kept] == ["Canale Tech"]
    assert dropped == 1


# ---- Video per idea: "cosa dicono di QUESTA cosa" -----------------------------


def test_the_idea_search_uses_the_label_as_the_query() -> None:
    """Il pannello cerca per tema, il dossier per idea: due domande diverse."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(200, json={"items": [_entry("uno")]})

    cache_clear()
    result = videos_for_idea(
        "Runtime self-hosted per agenti",
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert seen == ["Runtime self-hosted per agenti"]  # una ricerca sola
    assert [v.video_id for v in result["videos"]] == ["uno"]
    assert result["videos"][0].profile is None  # non viene da un tema


def test_la_ricerca_per_idea_non_si_limita_alla_settimana() -> None:
    """La finestra a 7 giorni trasformava in «nessuno ne parla» ogni idea
    salita più di una settimana fa: il contrario dell'informazione che il
    dossier vuole dare."""
    finestre: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        finestre.append("publishedAfter" in request.url.params)
        return httpx.Response(200, json={"items": [_entry("uno")]})

    cache_clear()
    videos_for_idea(
        "Un'idea",
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert finestre == [False]


def test_the_idea_search_is_cached_like_the_panel() -> None:
    """100 unità di quota a ricerca: riaprire il dossier non deve ripagarle."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"items": [_entry("uno")]})

    cache_clear()
    settings = Settings(youtube_api_key="k")
    first = videos_for_idea("Un'idea", _config(), settings, client=_client(handler))
    second = videos_for_idea("Un'idea", _config(), settings, client=_client(handler))

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 1


def test_la_ricerca_per_idea_si_giudica_sul_suo_ancoraggio() -> None:
    """Si è cercato il label: se il titolo non gli somiglia, YouTube ha
    risposto d'altro. E l'ancoraggio può dire più del label — un nome di
    pacchetto non descrive niente, il sommario sì."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    _entry("fuori", title="lontano"),
                    _entry("buono", title="vicinissimo"),
                ]
            },
        )

    cache_clear()
    result = videos_for_idea(
        "@scope/pacchetto",
        _config(),
        Settings(youtube_api_key="k"),
        limit=1,
        anchor="agenti",
        client=_client(handler),
        use_cache=False,
        embedder=_FakeEmbedder(_MAPPING),
    )

    assert [v.video_id for v in result["videos"]] == ["buono"]


def test_a_failed_idea_search_is_empty_not_an_exception() -> None:
    """Un dossier che si apre a metà per colpa di YouTube sarebbe peggio del vuoto."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    cache_clear()
    result = videos_for_idea(
        "Un'idea",
        _config(),
        Settings(youtube_api_key="k"),
        client=_client(handler),
        use_cache=False,
    )

    assert result["configured"] is True
    assert result["videos"] == []


def test_without_the_key_the_idea_search_explains_itself_too() -> None:
    cache_clear()
    result = videos_for_idea("Un'idea", _config(), Settings(youtube_api_key=""))

    assert result["configured"] is False
    assert "YOUTUBE_API_KEY" in result["detail"]
