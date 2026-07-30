"""Collector Hugging Face: contatori vivi, e non i modelli più scaricati di sempre."""

from datetime import datetime

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.base import create_source
from app.sources.huggingface import PROFILE, HuggingFaceSource
from app.sources.profiles import profile_for


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "self-hosted"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="hf", type="huggingface", limit=10, hf_kinds=["models"])
    base.update(overrides)
    return SourceConfig(**base)


def _model(name: str, likes: int, downloads: int = 0, created: str | None = None) -> dict:
    entry = {
        "id": name,
        "author": name.split("/")[0],
        "likes": likes,
        "downloads": downloads,
        "pipeline_tag": "text-generation",
        "tags": ["agent", "llm"],
    }
    if created:
        entry["createdAt"] = created
    return entry


def _source(handler, **cfg_overrides) -> HuggingFaceSource:
    return HuggingFaceSource(
        _cfg(**cfg_overrides),
        _app_config(),
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_sorted_by_last_modified_not_by_downloads() -> None:
    """Ordinare per download darebbe Llama e BERT: i più scaricati del mondo.

    È lo stesso errore che teneva la fonte GitHub ferma su freeCodeCamp: i più
    popolari di sempre sono per definizione mercati chiusi.
    """
    params = _source(lambda r: httpx.Response(200, json=[])).search_params("agent", 10)
    assert params["sort"] == "lastModified"
    assert params["direction"] == -1
    assert params["search"] == "agent"


def test_engagement_is_a_live_counter_so_heat_can_be_measured() -> None:
    """Il motivo per cui questa fonte è stata aggiunta.

    Con arXiv e i feed RSS senza engagement, metà del corpus aveva heat a zero.
    Le likes di HF crescono nel tempo, quindi il delta tra osservazioni misura
    crescita reale come per GitHub e Hacker News.
    """
    assert profile_for("huggingface") is PROFILE
    assert PROFILE.live_counter is True
    # `downloads` è già una finestra a 30 giorni: pesato come le likes dominerebbe.
    assert PROFILE.engagement_weights["downloads"] < PROFILE.engagement_weights["likes"]
    assert PROFILE.engagement({"likes": 100, "downloads": 10_000}) == 200.0


def test_one_request_per_keyword_and_kind() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params["search"]))
        return httpx.Response(200, json=[])

    _source(handler, hf_kinds=["models", "datasets"]).fetch()

    assert len(seen) == 4  # 2 keyword x 2 tipi
    assert {path for path, _ in seen} == {"/api/models", "/api/datasets"}


def test_the_quota_is_split_between_models_and_datasets() -> None:
    """I dataset non devono essere schiacciati dai modelli, che hanno più likes."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("models"):
            return httpx.Response(
                200, json=[_model(f"org/modello{n}", 900 - n) for n in range(6)]
            )
        return httpx.Response(200, json=[_model("org/dataset-piccolo", 3)])

    items = _source(handler, limit=4, hf_kinds=["models", "datasets"]).fetch()
    titles = [i.title for i in items]

    assert "org/dataset-piccolo" in titles
    assert sum(1 for t in titles if t.startswith("org/modello")) == 2


def test_kind_is_part_of_the_id_and_of_the_url() -> None:
    """Un modello e un dataset omonimi sono cose diverse e hanno URL diversi."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_model("tizio/cosa", 10)])

    models = _source(handler, hf_kinds=["models"]).fetch()[0]
    datasets = _source(handler, hf_kinds=["datasets"]).fetch()[0]

    assert models.external_id != datasets.external_id
    assert models.url == "https://huggingface.co/tizio/cosa"
    assert datasets.url == "https://huggingface.co/datasets/tizio/cosa"


def test_a_missing_creation_date_stays_none() -> None:
    """Senza data di nascita lo scoring ripiega sull'euristica: non si inventa."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _model("con/data", 5, created="2026-05-04T10:00:00.000Z"),
                _model("senza/data", 4),
            ],
        )

    items = {i.title: i for i in _source(handler).fetch()}

    assert items["con/data"].created_at == datetime(2026, 5, 4, 10, 0)
    assert items["con/data"].created_at.tzinfo is None
    assert items["senza/data"].created_at is None


def test_a_broken_response_does_not_kill_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "ai agents" in request.url.params["search"]:
            return httpx.Response(500)
        return httpx.Response(200, json=[_model("ok/uno", 7)])

    assert [i.title for i in _source(handler).fetch()] == ["ok/uno"]


def test_a_payload_that_is_not_a_list_is_ignored() -> None:
    """L'API risponde una lista; un dict d'errore non deve far esplodere il run."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "qualcosa è andato storto"})

    assert _source(handler).fetch() == []


def test_registered_in_the_source_registry() -> None:
    src = create_source(_cfg(), _app_config(), Settings())
    assert isinstance(src, HuggingFaceSource)


def test_a_multi_license_model_is_collected() -> None:
    """`cardData.license` è una lista sui modelli multi-licenza.

    Il caso reale che rompeva il run #66: il join sul description moriva con
    `sequence item 1: expected str instance, list found` e la fonte perdeva
    tutti i segnali, non solo quello malformato.
    """
    entry = _model("multi/licenza", 9)
    entry["cardData"] = {"license": ["apache-2.0", "other"]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[entry])

    items = _source(handler).fetch()

    assert [i.title for i in items] == ["multi/licenza"]
    assert "apache-2.0" in items[0].text
    assert "other" in items[0].text


def test_a_single_license_string_still_lands_in_the_text() -> None:
    entry = _model("singola/licenza", 3)
    entry["cardData"] = {"license": "mit"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[entry])

    items = _source(handler).fetch()

    assert items[0].text is not None
    assert "mit" in items[0].text
    assert "text-generation" in items[0].text


def test_card_data_of_the_wrong_shape_is_ignored() -> None:
    """`cardData` senza schema garantito: se non è un dict si tira avanti."""
    entry = _model("card/strana", 4)
    entry["cardData"] = ["apache-2.0"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[entry])

    assert [i.title for i in _source(handler).fetch()] == ["card/strana"]


def test_one_malformed_entry_does_not_drop_the_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": ["non", "una", "stringa"]}, _model("ok/due", 6)])

    assert "ok/due" in [i.title for i in _source(handler).fetch()]
