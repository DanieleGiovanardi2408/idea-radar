"""Doppi condivisi tra i test.

Ogni modulo di test si era costruito il suo finto embedder: nove classi con la
stessa forma. Quando la pipeline è passata a chiedere gli embedding in blocco,
tutte e nove hanno smesso di rispondere insieme. Il protocollo vive qui, così la
prossima volta si aggiorna un punto e non nove.
"""


class EmbedManyMixin:
    """Aggiunge ``embed_many`` a un doppio che sa già fare ``embed``.

    La pipeline chiede gli embedding in blocco (una richiesta per batch invece di
    una per item); i test restano liberi di descrivere il vettore di un singolo
    testo, che è ciò che li rende leggibili.
    """

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        return [self.embed(text) for text in texts]  # type: ignore[attr-defined]
