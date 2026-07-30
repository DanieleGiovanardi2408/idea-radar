"""Doppi condivisi tra i test.

Ogni modulo di test si era costruito il suo finto embedder: nove classi con la
stessa forma. Quando la pipeline è passata a chiedere gli embedding in blocco,
tutte e nove hanno smesso di rispondere insieme. Il protocollo vive qui, così la
prossima volta si aggiorna un punto e non nove.
"""


from app.llm import IdeaInsight
from app.models import Item


class FakeOllama:
    """OllamaClient finto con TUTTO il protocollo che la pipeline usa.

    La lezione è del 30/07: la fase delle mosse è nata chiamando
    ``ollama.moves()``, e i FakeOllama locali dei test — che conoscevano solo
    ``insight`` e ``topic_label`` — sono esplosi con AttributeError. Un metodo
    nuovo sulla pipeline si aggiunge QUI, una volta.
    """

    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(
            summary=f"riassunto di {item.title}", why_text="perché sì", difficulty=None
        )

    def topic_label(self, labels: list[str]) -> str:
        return "topic di prova"

    def moves(self, label: str, summary: str, why: str, signals: str) -> list[str]:
        return [f"sfrutta {label}"]

    def business_angle(self, label: str, summary: str, why: str, signals: str) -> str:
        return f"angolo per {label}"


class EmbedManyMixin:
    """Aggiunge ``embed_many`` a un doppio che sa già fare ``embed``.

    La pipeline chiede gli embedding in blocco (una richiesta per batch invece di
    una per item); i test restano liberi di descrivere il vettore di un singolo
    testo, che è ciò che li rende leggibili.
    """

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        return [self.embed(text) for text in texts]  # type: ignore[attr-defined]
