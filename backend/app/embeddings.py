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

# `/api/embed` accetta una lista e restituisce gli embedding nello stesso ordine:
# un round-trip invece di uno per item. `/api/embeddings` (singolare) è la rotta
# storica, superata ma ancora servita: resta come ripiego per installazioni
# vecchie, una chiamata per testo.
_BATCH_PATH = "/api/embed"
_LEGACY_PATH = "/api/embeddings"

# Ollama risponde 404 in due casi diversi: rotta inesistente (installazione
# vecchia, corpo di testo "404 page not found") e modello mancante (corpo JSON
# con "error"). Lo status non li distingue, il corpo sì — e confonderli
# significherebbe leggere "aggiorna Ollama" quando manca solo un `ollama pull`.
_MISSING_ROUTE_MARKER = "page not found"


class OllamaEmbedder:
    """Embedder Ollama con richieste raggruppate.

    ``batch_size`` è il numero di testi per richiesta: alza il costo di un
    singolo round-trip ma ne elimina N-1. Il modello li elabora comunque uno per
    uno, quindi il guadagno è tutto nella latenza di rete e nell'overhead HTTP —
    su un run da 280 item sono 280 richieste che diventano 9.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        batch_size: int = 32,
    ) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._consecutive_failures = 0
        self.batch_size = max(1, batch_size)
        # Deciso al primo 404 di rotta e ricordato: non si riprova la rotta
        # moderna a ogni chunk solo per riscoprire che non c'è.
        self._batch_unsupported = False

    @property
    def unavailable(self) -> bool:
        return self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES

    def _guard_available(self) -> None:
        if self.unavailable:
            raise EmbeddingError(
                f"modello '{self.settings.embedding_model}' non disponibile "
                f"(disattivato dopo {_MAX_CONSECUTIVE_FAILURES} tentativi falliti)"
            )

    def embed(self, text: str) -> Vector:
        """Embedding di un solo testo. Alza ``EmbeddingError`` se non c'è."""
        vectors = self.embed_many([text])
        if not vectors or vectors[0] is None:
            raise EmbeddingError("Ollama ha restituito un embedding vuoto")
        return vectors[0]

    def embed_many(self, texts: list[str]) -> list[Vector | None]:
        """Embedding di più testi, in richieste da ``batch_size``.

        Restituisce una lista lunga come l'input: ``None`` dove il vettore manca,
        per non spostare gli indici e obbligare il chiamante a riallineare. Alza
        ``EmbeddingError`` solo se l'embedder era già dichiarato indisponibile;
        se lo diventa a metà, il resto della lista torna ``None`` senza altre
        richieste — un modello assente non si sblocca insistendo.
        """
        self._guard_available()
        out: list[Vector | None] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start : start + self.batch_size]
            if self.unavailable:
                out.extend([None] * len(chunk))
                continue
            out.extend(self._embed_chunk(chunk))
        return out

    def _embed_chunk(self, chunk: list[str]) -> list[Vector | None]:
        if self._batch_unsupported:
            return [self._embed_single(text) for text in chunk]
        client = self._client or httpx.Client(timeout=60.0)
        try:
            resp = client.post(
                f"{self.settings.ollama_host}{_BATCH_PATH}",
                json={"model": self.settings.embedding_model, "input": chunk},
            )
            if resp.status_code == 404 and _MISSING_ROUTE_MARKER in resp.text.lower():
                logger.info(
                    "Questo Ollama non espone %s: passo a %s, una richiesta per "
                    "testo. Aggiornarlo rende gli embedding più rapidi.",
                    _BATCH_PATH,
                    _LEGACY_PATH,
                )
                self._batch_unsupported = True
                return [self._embed_single(text) for text in chunk]
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
            if len(vectors) != len(chunk):
                raise EmbeddingError(
                    f"Ollama ha restituito {len(vectors)} embedding per "
                    f"{len(chunk)} testi"
                )
            self._consecutive_failures = 0
            return [[float(x) for x in v] if v else None for v in vectors]
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            self._consecutive_failures += 1
            raise EmbeddingError(str(exc)) from exc
        finally:
            if self._owns_client:
                client.close()

    def _embed_single(self, text: str) -> Vector | None:
        """Un testo sulla rotta storica. ``None`` se fallisce, senza propagare:
        in un batch il vicino di banco non deve pagare per lui."""
        if self.unavailable:
            return None
        client = self._client or httpx.Client(timeout=60.0)
        try:
            resp = client.post(
                f"{self.settings.ollama_host}{_LEGACY_PATH}",
                json={"model": self.settings.embedding_model, "prompt": text},
            )
            resp.raise_for_status()
            vector = resp.json()["embedding"]
            if not vector:
                return None
            self._consecutive_failures = 0
            return [float(x) for x in vector]
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            self._consecutive_failures += 1
            return None
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


def text_for_embedding(text: str) -> str:
    """Testo pronto per l'embedding, col prefisso di task che il modello esige.

    Qualunque cosa si voglia confrontare con gli embedding già in archivio deve
    passare da qui: senza lo stesso prefisso i vettori non sono comparabili.
    """
    return f"{_EMBED_TASK_PREFIX}{text}"


def item_text_for_embedding(item: Item) -> str:
    return text_for_embedding(f"{item.title}\n{(item.text or '')[:1000]}")


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


def embed_items(
    items: list[Item], embedder: OllamaEmbedder
) -> dict[int, Vector]:
    """Embedding di una lista di item in poche richieste.

    Restituisce solo gli item riusciti, indicizzati per id: chi manca resta
    senza embedding e la pipeline lo tratta come prima (un'idea a sé). Il
    warning è uno per chiamata, non uno per item: se il modello non c'è, il log
    non deve diventare la parte più lunga del run.
    """
    # Un doppione nello stesso fetch arriva come due oggetti sulla STESSA riga:
    # embeddarlo due volte è lavoro buttato, e contarlo due volte faceva
    # sembrare perso un embedding che invece c'era.
    unici: dict[int, Item] = {}
    for item in items:
        if item.id is not None:
            unici.setdefault(item.id, item)
    candidates = list(unici.values())
    if not candidates:
        return {}
    texts = [item_text_for_embedding(item) for item in candidates]
    try:
        vectors = embedder.embed_many(texts)
    except EmbeddingError as exc:
        logger.warning(
            "Embedding non disponibile (%s). Senza embedding niente clustering: "
            "ogni segnale resta un'idea a sé. Prova `ollama pull %s`.",
            exc,
            embedder.settings.embedding_model,
        )
        return {}
    done = {
        item.id: vector
        for item, vector in zip(candidates, vectors)
        if vector is not None and item.id is not None
    }
    if len(done) < len(candidates):
        logger.warning(
            "Embedding mancante per %d item su %d: restano idee a sé. "
            "Se sono tutti, prova `ollama pull %s`.",
            len(candidates) - len(done),
            len(candidates),
            embedder.settings.embedding_model,
        )
    return done


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
