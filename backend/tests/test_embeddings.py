import httpx
import pytest

from app.config import Settings
from app.embeddings import (
    EmbeddingError,
    OllamaEmbedder,
    centroid,
    cosine,
    embed_item,
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
        assert request.url.path == "/api/embeddings"
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    vector = OllamaEmbedder(Settings(), client=client).embed("testo")
    assert vector == [0.1, 0.2, 0.3]


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


def test_transient_failure_does_not_disable_embeddings() -> None:
    """Un singolo errore non deve spegnere il clustering per tutto il run."""
    responses = [httpx.Response(500), httpx.Response(200, json={"embedding": [1.0]})]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    embedder = OllamaEmbedder(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert embed_item(_item(), embedder) is None  # primo tentativo fallisce
    assert embed_item(_item(), embedder) == [1.0]  # il secondo riprova e riesce
    assert not embedder.unavailable
