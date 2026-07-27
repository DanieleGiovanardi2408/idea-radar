import httpx
import pytest

from app.config import Settings
from app.embeddings import (
    EmbeddingError,
    OllamaEmbedder,
    centroid,
    cosine,
    dot,
    embed_item,
    unit,
)
from app.models import Item


def _item() -> Item:
    return Item(source="hn", external_id="1", title="titolo", text="corpo")


def test_cosine_identical_orthogonal_and_degenerate() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # vettore nullo
    assert cosine([1.0], [1.0, 0.0]) == 0.0  # lunghezze diverse
    assert cosine([], []) == 0.0


def test_centroid_averages_and_handles_empty() -> None:
    assert centroid([[0.0, 2.0], [2.0, 0.0]]) == [1.0, 1.0]
    assert centroid([]) is None
    assert centroid([[]]) is None


def test_embedder_returns_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    vector = OllamaEmbedder(Settings(), client=client).embed("testo")
    assert vector == [0.1, 0.2, 0.3]


def test_embed_many_uses_one_request_per_batch() -> None:
    """Il guadagno è tutto qui: N testi, N/batch_size round-trip."""
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        requests.append(payload["input"])
        return httpx.Response(
            200, json={"embeddings": [[float(len(t))] for t in payload["input"]]}
        )

    embedder = OllamaEmbedder(
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        batch_size=3,
    )
    vectors = embedder.embed_many(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [len(r) for r in requests] == [3, 2]  # 5 testi, 2 richieste
    assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]


def test_embed_many_keeps_positions_when_a_vector_is_missing() -> None:
    """Un vettore vuoto non deve spostare gli altri: l'indice è il contratto."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0], [], [3.0]]})

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert embedder.embed_many(["a", "b", "c"]) == [[1.0], None, [3.0]]


def test_embed_many_rejects_a_mismatched_response() -> None:
    """Meno embedding che testi significa non sapere di chi sono: errore."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(EmbeddingError):
        embedder.embed_many(["a", "b"])


def test_falls_back_to_the_legacy_route_on_old_ollama() -> None:
    """Un Ollama senza /api/embed non deve far fallire il run, solo rallentarlo."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/embed":
            # Come risponde una rotta inesistente: 404 con corpo di testo.
            return httpx.Response(404, text="404 page not found")
        return httpx.Response(200, json={"embedding": [7.0]})

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert embedder.embed_many(["a", "b"]) == [[7.0], [7.0]]
    # La rotta moderna si prova una volta sola, non a ogni chunk.
    assert paths == ["/api/embed", "/api/embeddings", "/api/embeddings"]
    assert embedder.embed_many(["c"]) == [[7.0]]
    assert paths[-1] == "/api/embeddings"


def test_a_missing_model_is_not_mistaken_for_a_missing_route() -> None:
    """Ollama dà 404 anche per un modello assente: il corpo è ciò che distingue.

    Confonderli farebbe ripiegare sulla rotta storica (e leggere "aggiorna
    Ollama") quando in realtà manca solo un `ollama pull`.
    """
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": 'model "nomic-embed-text" not found'})

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(EmbeddingError):
        embedder.embed_many(["a"])
    assert paths == ["/api/embed"]  # nessun ripiego


def test_embedder_raises_on_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(EmbeddingError):
        OllamaEmbedder(Settings(), client=client).embed("testo")


def test_embed_item_degrades_to_none() -> None:
    """Senza embedding la pipeline deve proseguire, non esplodere."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    embedder = OllamaEmbedder(Settings(), client=client)
    assert embed_item(_item(), embedder) is None


def test_embedder_stops_hammering_a_missing_model() -> None:
    """Se il modello non c'è, non ha senso riprovare per ogni item della coda."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    for _ in range(20):
        assert embed_item(_item(), embedder) is None

    assert embedder.unavailable
    assert len(calls) == 3  # si arrende dopo 3 tentativi, non 20


def test_dot_on_unit_vectors_equals_cosine() -> None:
    """La scorciatoia usata nel clustering non deve cambiare i risultati."""
    pairs = [
        ([3.0, 4.0, 0.0], [0.0, 5.0, 12.0]),
        ([1.0, 0.0], [1.0, 0.0]),
        ([2.0, 2.0], [-1.0, 1.0]),
    ]
    for a, b in pairs:
        assert dot(unit(a), unit(b)) == pytest.approx(cosine(a, b))


def test_unit_survives_the_null_vector_and_normalizes() -> None:
    assert unit([0.0, 0.0]) == [0.0, 0.0]  # niente divisione per zero
    assert dot(unit([3.0, 4.0]), unit([3.0, 4.0])) == pytest.approx(1.0)
    assert dot([1.0, 0.0], [1.0]) == 0.0  # lunghezze diverse: nessun confronto


def test_transient_failure_does_not_disable_embeddings() -> None:
    """Un singolo errore non deve spegnere il clustering per tutto il run."""
    responses = [
        httpx.Response(500),
        httpx.Response(200, json={"embeddings": [[1.0]]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert embed_item(_item(), embedder) is None  # primo tentativo fallisce
    assert embed_item(_item(), embedder) == [1.0]  # il secondo riprova e riesce
    assert not embedder.unavailable
