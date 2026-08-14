from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import IdeaInsight
from app.models import Idea, Item, RunStatus, Score, Topic, TopicStat
from app.pipeline import run_pipeline


class FakeSource:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def fetch(self) -> list[Item]:
        return self._items


class FakeOllama:
    """Sostituisce OllamaClient nei test: nessuna rete."""

    def insight(self, item: Item) -> IdeaInsight:
        return IdeaInsight(
            summary=f"riassunto di {item.title}", why_text="perché sì", difficulty=None
        )

    def topic_label(self, labels: list[str]) -> str:
        return "topic di prova"

    def moves(
        self, label: str, summary: str, why: str, signals: str, **kwargs
    ) -> list[str]:
        # **kwargs: la validazione (generic_patterns, embedder, soglia) arriva
        # come argomenti a parola chiave. Un doppio che li elenca uno per uno
        # va aggiornato ogni volta che se ne aggiunge uno; questo no.
        return [f"sfrutta {label}"]

    def business_angle(
        self, label: str, summary: str, why: str, signals: str, **kwargs
    ) -> str:
        return f"angolo per {label}"


class FakeEmbedder:
    """Embedding deterministici: item con lo stesso prefisso finiscono vicini."""

    unavailable = False

    def __init__(self) -> None:
        # Quante richieste ha ricevuto: la pipeline ne deve fare una per batch,
        # non una per item.
        self.chiamate = 0

    def embed(self, text: str) -> list[float]:
        testo = text.lower()
        # "agent" sta vicino ad "ai" (coseno 0.98) ma NON identico: due item così
        # restano idee distinte sotto `idea_threshold` 0.99 e formano una coppia
        # sopra `topic_threshold` 0.8. Serve perché un topic ora nasce da due
        # idee: con vettori identici i due item si fonderebbero in una sola.
        if "agent" in testo:
            return [0.98, 0.2]
        # "ai" ovunque nel testo (dopo il prefisso "clustering:" degli embedding).
        return [1.0, 0.0] if "ai" in testo else [0.0, 1.0]

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        self.chiamate += 1
        return [self.embed(text) for text in texts]


class ExplodingSource:
    def fetch(self) -> list[Item]:
        raise RuntimeError("fonte down")


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _config(idea_threshold: float = 0.99) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        # `opportunity` non è più un peso: è un moltiplicatore. Qui serve una
        # metrica sempre positiva (la credibilità ha una base per fonte) perché
        # il composite non sia zero per costruzione.
        scoring=ScoringConfig(
            weights={"heat": 0.5, "credibility": 0.5}, threshold=0.25
        ),
        clustering=ClusteringConfig(
            idea_threshold=idea_threshold, topic_threshold=0.8, llm_topic_labels=True
        ),
    )


def _run(session: Session, items: list[Item], **kwargs):
    return run_pipeline(
        session,
        kwargs.pop("config", _config()),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
        **kwargs,
    )


def test_pipeline_creates_items_ideas_scores_and_topics(session: Session) -> None:
    # "ai tool" e "ai agent" stanno a 0.98: idee distinte (soglia 0.99) che
    # formano una coppia, quindi un topic. "repo" è ortogonale e resta un'idea
    # non raggruppata — un'idea sola non fa un tema.
    items = [
        Item(source="hn", external_id="1", title="ai tool", engagement_json={"score": 100}),
        Item(source="hn", external_id="3", title="ai agent", engagement_json={"score": 90}),
        Item(source="github", external_id="2", title="repo", engagement_json={"stars": 500}),
    ]
    run = _run(session, items)

    assert run.status == RunStatus.DONE
    assert run.n_items == 3
    assert run.finished_at is not None
    assert len(session.exec(select(Idea)).all()) == 3
    assert len(session.exec(select(Score)).all()) == 3
    assert run.n_ideas_proposed + run.n_ideas_processed == 3
    assert session.exec(select(Topic)).all()  # topic creati
    assert session.exec(select(TopicStat)).all()  # fotografia per i trend
    senza_topic = [i for i in session.exec(select(Idea)).all() if i.topic_id is None]
    assert len(senza_topic) == 1  # "repo": nessun compagno, nessun tema finto


def test_embeddings_are_asked_in_one_batch(session: Session) -> None:
    """La fase di embedding è una richiesta, non una per item.

    Era il costo fisso per item: un round-trip HTTP e un commit ciascuno, pagati
    anche quando il resto del lavoro veniva dalla cache.
    """
    embedder = FakeEmbedder()
    items = [
        Item(source="hn", external_id=str(n), title=f"ai tool {n}") for n in range(12)
    ]
    run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource(items)],
        ollama=FakeOllama(),
        embedder=embedder,
    )

    assert embedder.chiamate == 1
    assert all(item.embedding_json for item in session.exec(select(Item)).all())


def test_items_already_embedded_are_not_asked_again(session: Session) -> None:
    """Un secondo run non ripaga gli embedding degli item che ce l'hanno già."""
    items = [Item(source="hn", external_id="1", title="ai tool")]
    _run(session, items)

    secondo = FakeEmbedder()
    run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[FakeSource([Item(source="hn", external_id="1", title="ai tool")])],
        ollama=FakeOllama(),
        embedder=secondo,
    )
    # Nessun item nuovo da embeddare: nessuna richiesta.
    assert secondo.chiamate == 0


def test_the_run_survives_an_embedder_that_gives_nothing(session: Session) -> None:
    """Senza vettori il run continua: ogni item resta un'idea a sé, come prima."""

    class MutoEmbedder:
        unavailable = False
        settings = Settings()

        def embed_many(self, texts: list[str]) -> list[list[float] | None]:
            return [None] * len(texts)

    run = run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[
            FakeSource(
                [
                    Item(source="hn", external_id="1", title="ai tool"),
                    Item(source="hn", external_id="2", title="ai tool"),
                ]
            )
        ],
        ollama=FakeOllama(),
        embedder=MutoEmbedder(),
    )

    assert run.status == RunStatus.DONE
    assert len(session.exec(select(Idea)).all()) == 2  # nessuna fusione, nessun crash


def test_pipeline_records_progress_and_source_stats(session: Session) -> None:
    run = _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert run.phase == "completato"
    assert run.n_items_fetched == 1
    assert run.n_items_new == 1
    assert run.sources_json == {"FakeSource": {"fetched": 1, "new": 1}}


def test_second_run_does_not_recount_existing_items(session: Session) -> None:
    items = [Item(source="hn", external_id="1", title="ai tool")]
    _run(session, items)
    second = _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert second.n_items_fetched == 1
    assert second.n_items_new == 0  # già visto
    assert len(session.exec(select(Idea)).all()) == 1


def test_similar_items_collapse_into_one_idea(session: Session) -> None:
    """Con soglia permissiva due segnali sullo stesso tema fanno UNA idea."""
    items = [
        Item(source="hn", external_id="1", title="ai agent per il codice"),
        Item(source="github", external_id="2", title="ai agent che scrive codice"),
    ]
    run = _run(session, items, config=_config(idea_threshold=0.8))

    ideas = session.exec(select(Idea)).all()
    assert len(ideas) == 1  # deduplicate
    assert len(ideas[0].items) == 2
    assert run.n_items == 2
    assert len(session.exec(select(Score)).all()) == 1  # uno score per idea per run


def test_score_per_run_is_kept_across_runs(session: Session) -> None:
    items = [Item(source="hn", external_id="1", title="ai tool")]
    _run(session, items)
    _run(session, [Item(source="hn", external_id="1", title="ai tool")])
    assert len(session.exec(select(Score)).all()) == 2  # uno per run


def test_pipeline_survives_failing_source(session: Session) -> None:
    run = run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[ExplodingSource(), FakeSource([Item(source="hn", external_id="9", title="ok")])],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )
    assert run.status == RunStatus.DONE
    assert run.n_items == 1
    assert run.sources_json["ExplodingSource"]["error"]


def test_a_source_that_fails_last_still_lands_in_the_monitor(session: Session) -> None:
    """Regressione: l'errore va scritto subito, non alla prossima fonte che riesce.

    Il test sopra mette la fonte rotta per PRIMA, seguita da una che funziona —
    ed è per questo che il difetto è passato: l'errore veniva salvato insieme
    alle statistiche della fonte successiva. arXiv, ultima della lista in
    config.yaml, falliva a ogni run senza comparire da nessuna parte.
    """
    run = run_pipeline(
        session,
        _config(),
        Settings(),
        sources=[
            FakeSource([Item(source="hn", external_id="9", title="ok")]),
            ExplodingSource(),
        ],
        ollama=FakeOllama(),
        embedder=FakeEmbedder(),
    )

    assert run.status == RunStatus.DONE
    assert run.sources_json["ExplodingSource"]["error"] == "fonte down"
    assert run.sources_json["FakeSource"]["fetched"] == 1


def test_an_empty_run_does_not_flatten_the_trends(session: Session) -> None:
    """Un run senza novità disegna una linea piatta, non un crollo a zero.

    ``avg_composite`` è la qualità *corrente* del topic: si misura sull'ultimo
    punteggio noto di ogni idea. Contando solo i punteggi nati nel run, ogni
    topic non toccato veniva fotografato a 0.0 — e un run a vuoto (Mac offline)
    azzerava in un colpo la serie di tutti i topic.
    """
    _run(
        session,
        [
            Item(source="hn", external_id="1", title="ai tool"),
            Item(source="hn", external_id="2", title="ai agent"),
        ],
    )
    first = session.exec(select(TopicStat)).one()
    assert first.avg_composite > 0

    empty = _run(session, [])  # nessun item raccolto: niente da scorare

    assert empty.status == RunStatus.DONE
    assert empty.n_items == 0
    after = session.exec(
        select(TopicStat).where(TopicStat.run_id == empty.id)
    ).one()
    assert after.avg_composite == pytest.approx(first.avg_composite)
    assert after.n_ideas == first.n_ideas


def test_topic_namer_rispetta_il_budget_di_tempo(monkeypatch) -> None:
    """Il cronometro è sull'INTERA fase: quando il tempo speso supera il
    budget, la chiamata successiva alza LabelBudgetExceeded."""
    from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
    from app.clustering import LabelBudgetExceeded
    from app.pipeline import _topic_namer

    class _FakeOllama:
        def topic_label(self, labels: list[str]) -> str:
            return "un tema"

    config = AppConfig(
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(label_budget_seconds=10.0),
    )
    namer = _topic_namer(config, _FakeOllama())
    assert namer is not None

    # Prima chiamata: budget intatto, passa. Poi il tempo "vola" oltre il
    # limite: la seconda deve fermare la fase.
    clock = iter([0.0, 11.0, 11.0])
    monkeypatch.setattr("app.pipeline.monotonic", lambda: next(clock))
    assert namer(["a"]) == "un tema"
    with pytest.raises(LabelBudgetExceeded):
        namer(["b"])


def test_topic_namer_budget_zero_significa_senza_limite() -> None:
    from app.appconfig import AppConfig, ClusteringConfig, ScoringConfig
    from app.pipeline import _topic_namer

    class _FakeOllama:
        def topic_label(self, labels: list[str]) -> str:
            return "x"

    config = AppConfig(
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        clustering=ClusteringConfig(label_budget_seconds=0.0),
    )
    namer = _topic_namer(config, _FakeOllama())
    for _ in range(5):
        assert namer(["a"]) == "x"
