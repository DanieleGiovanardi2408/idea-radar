"""Embedding semantici locali via Ollama + utilità di similarità.

Servono a capire *di cosa parla* un item, così da fondere item diversi che
raccontano la stessa cosa in un'unica idea (aggregazione) e da raggruppare
idee affini in topic. Modello di default: ``nomic-embed-text`` (gratuito,
locale). Se gli embedding non sono disponibili la pipeline continua: senza
vettori si ricade sul comportamento 1 item = 1 idea.
"""

import logging
import math
from operator import mul as _mul

import httpx

from app.config import Settings
from app.models import Item

logger = logging.getLogger(__name__)

Vector = list[float]


class EmbeddingError(RuntimeError):
    """Ollama non raggiungibile o risposta di embedding non valida."""


# Dopo N fallimenti di fila smettiamo di provare: se il modello non c'è, insistere
# per ogni item significa centinaia di round-trip inutili. Non è 1 perché un
# singolo errore transitorio non deve disattivare il clustering per tutto il run.
_MAX_CONSECUTIVE_FAILURES = 3


class OllamaEmbedder:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._consecutive_failures = 0

    @property
    def unavailable(self) -> bool:
        return self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES

    def embed(self, text: str) -> Vector:
        if self.unavailable:
            raise EmbeddingError(
                f"modello '{self.settings.embedding_model}' non disponibile "
                f"(disattivato dopo {_MAX_CONSECUTIVE_FAILURES} tentativi falliti)"
            )
        client = self._client or httpx.Client(timeout=60.0)
        try:
            resp = client.post(
                f"{self.settings.ollama_host}/api/embeddings",
                json={"model": self.settings.embedding_model, "prompt": text},
            )
            resp.raise_for_status()
            vector = resp.json()["embedding"]
            if not vector:
                raise EmbeddingError("Ollama ha restituito un embedding vuoto")
            self._consecutive_failures = 0
            return [float(x) for x in vector]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            self._consecutive_failures += 1
            raise EmbeddingError(str(exc)) from exc
        finally:
            if self._owns_client:
                client.close()


# nomic-embed-text RICHIEDE un prefisso di task nel testo: senza, il modello
# lavora fuori modalità e gli item simili non si avvicinano abbastanza (è il
# motivo per cui la deduplicazione fondeva quasi nulla). Per raggruppare e
# rimuovere duplicati semantici la scheda del modello prescrive "clustering:".
# NB: cambiare questo prefisso rende gli embedding già in cache non confrontabili
# con i nuovi — dopo la modifica va rifatto l'embedding degli item esistenti.
_EMBED_TASK_PREFIX = "clustering: "


def item_text_for_embedding(item: Item) -> str:
    return f"{_EMBED_TASK_PREFIX}{item.title}\n{(item.text or '')[:1000]}"


def embed_item(item: Item, embedder: OllamaEmbedder) -> Vector | None:
    """Embedding dell'item; ``None`` se il modello non è disponibile.

    Il warning si ferma appena l'embedder si dichiara indisponibile: inutile
    ripetere lo stesso errore per ogni item della coda.
    """
    was_available = not embedder.unavailable
    try:
        return embedder.embed(item_text_for_embedding(item))
    except EmbeddingError as exc:
        if was_available:
            logger.warning(
                "Embedding non disponibile (%s). Senza embedding niente clustering: "
                "ogni segnale resta un'idea a sé. Prova `ollama pull %s`.",
                exc,
                embedder.settings.embedding_model,
            )
        return None


def cosine(a: Vector, b: Vector) -> float:
    """Similarità coseno in [-1, 1] (0 se un vettore è nullo o di lunghezza diversa)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def unit(vector: Vector) -> Vector:
    """Vettore riportato a norma 1 (invariato se è nullo).

    Su vettori unitari il coseno è il solo prodotto scalare: normalizzare una
    volta e poi usare ``dot`` costa ~5 volte meno di chiamare ``cosine``, che
    ricalcola entrambe le norme a ogni confronto. Conta dove i confronti sono
    quadratici (la ricostruzione delle idee su tutto l'archivio).
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return list(vector)
    return [x / norm for x in vector]


def dot(a: Vector, b: Vector) -> float:
    """Prodotto scalare. Su vettori unitari della stessa lunghezza è il coseno."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(map(_mul, a, b))


def centroid(vectors: list[Vector]) -> Vector | None:
    """Media componente per componente dei vettori (ignora quelli vuoti)."""
    valid = [v for v in vectors if v]
    if not valid:
        return None
    size = len(valid[0])
    valid = [v for v in valid if len(v) == size]
    if not valid:
        return None
    return [sum(v[i] for v in valid) / len(valid) for i in range(size)]
