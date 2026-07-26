from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.clustering import (
    IdeaIndex,
    _refresh_centroid,
    assign_ideas_to_topics,
    attach_item_to_idea,
    group_indices_by_similarity,
    sweep_topic_thresholds,
)
from app.db import init_db, upsert_item
from app.embeddings import cosine
from app.models import Idea, Item, Run, RunStatus, Topic, TopicStat


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _item(session: Session, external_id: str, title: str, embedding=None) -> Item:
    item = upsert_item(
        session,
        Item(source="hn", external_id=external_id, title=title, embedding_json=embedding),
    )
    return item


def test_similar_items_merge_into_one_idea(session: Session) -> None:
    a = _item(session, "1", "un agente AI per il codice", [1.0, 0.0, 0.0])
    b = _item(session, "2", "agente AI che scrive codice", [0.99, 0.1, 0.0])

    idea_a = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    idea_b = attach_item_to_idea(session, b, b.embedding_json, threshold=0.8)

    assert idea_a.id == idea_b.id  # deduplicati nella stessa idea
    assert len(session.exec(select(Idea)).all()) == 1
    assert len(idea_b.items) == 2


def test_dissimilar_items_stay_separate(session: Session) -> None:
    a = _item(session, "1", "agente AI", [1.0, 0.0])
    b = _item(session, "2", "ricetta della carbonara", [0.0, 1.0])

    attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    attach_item_to_idea(session, b, b.embedding_json, threshold=0.8)

    assert len(session.exec(select(Idea)).all()) == 2


def test_centroid_drift_does_not_turn_a_big_idea_into_a_magnet(session: Session) -> None:
    """Regressione: un'idea grande non deve attirare item che non c'entrano.

    Il centroide di un'idea è una media: con molti membri diversi scivola verso
    il centro dello spazio e diventa simile a tutto. Sul DB reale un'idea era
    arrivata a 740 item così. Qui la deriva è riprodotta in piccolo: nove
    membri ortogonali e una sonda che punta *esattamente* come il centroide ma
    è lontana da ogni singolo membro.
    """
    size = 9
    members = []
    for i in range(size):
        vector = [1.0 if j == i else 0.0 for j in range(size)]
        members.append(_item(session, f"m{i}", f"membro {i}", vector))

    idea = Idea(label="idea grande", centroid_json=None)
    idea.items = list(members)
    session.add(idea)
    session.commit()
    _refresh_centroid(session, idea)

    probe_vector = [1.0] * size  # stessa direzione del centroide, ortogonale a ognuno
    probe = _item(session, "probe", "item estraneo", probe_vector)

    # Il vecchio criterio guardava QUESTO valore e avrebbe fuso: è la trappola.
    assert cosine(probe_vector, idea.centroid_json) == pytest.approx(1.0)
    # Verso ogni singolo membro, invece, la sonda è lontanissima.
    assert cosine(probe_vector, members[0].embedding_json) == pytest.approx(1 / 3)

    result = attach_item_to_idea(session, probe, probe_vector, threshold=0.8)

    assert result.id != idea.id  # idea nuova, non risucchiata nella grande
    assert len(idea.items) == size


def test_cohesion_floor_blocks_the_chain(session: Session) -> None:
    """A e B si somigliano, C somiglia a B ma non ad A: C resta fuori.

    Senza coesione il legame singolo basterebbe (C~B è sopra soglia) e i gruppi
    crescerebbero per catene, allontanando i membri agli estremi.
    """
    a = _item(session, "a", "a", [1.0, 0.0, 0.0])
    b = _item(session, "b", "b", [0.87, 0.4931, 0.0])  # cos(a,b) = 0.87
    c = _item(session, "c", "c", [0.5, 0.866, 0.0])  # cos(b,c)=0.87, cos(a,c)=0.5

    idea_a = attach_item_to_idea(session, a, a.embedding_json, 0.85, cohesion_floor=0.8)
    idea_b = attach_item_to_idea(session, b, b.embedding_json, 0.85, cohesion_floor=0.8)
    assert idea_a.id == idea_b.id  # doppione legittimo: fuso

    idea_c = attach_item_to_idea(session, c, c.embedding_json, 0.85, cohesion_floor=0.8)
    assert idea_c.id != idea_a.id  # la catena si ferma
    assert len(idea_a.items) == 2


def test_single_link_alone_would_chain(session: Session) -> None:
    """Controprova del test sopra: senza coesione, C entra in A~B per catena.

    cos(b,c)=0.87 basta al legame singolo anche se cos(a,c)=0.5. Documenta
    perché il pavimento di coesione serve e non è una cintura di sicurezza in più.
    """
    a = _item(session, "a", "a", [1.0, 0.0, 0.0])
    b = _item(session, "b", "b", [0.87, 0.4931, 0.0])
    c = _item(session, "c", "c", [0.5, 0.866, 0.0])

    ideas = [
        attach_item_to_idea(session, item, item.embedding_json, 0.85)
        for item in (a, b, c)
    ]

    assert len({idea.id for idea in ideas}) == 1
    assert len(ideas[0].items) == 3


def test_without_embedding_falls_back_to_one_item_one_idea(session: Session) -> None:
    a = _item(session, "1", "primo")
    b = _item(session, "2", "secondo")

    attach_item_to_idea(session, a, None, threshold=0.8)
    attach_item_to_idea(session, b, None, threshold=0.8)

    assert len(session.exec(select(Idea)).all()) == 2


def test_existing_item_reuses_its_idea(session: Session) -> None:
    a = _item(session, "1", "agente AI", [1.0, 0.0])
    first = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    again = attach_item_to_idea(session, a, a.embedding_json, threshold=0.8)
    assert first.id == again.id
    assert len(session.exec(select(Idea)).all()) == 1


def test_ideas_group_into_topics(session: Session) -> None:
    for i, (external_id, title, emb) in enumerate(
        [
            ("1", "agente AI per il codice", [1.0, 0.0]),
            ("2", "copilota per sviluppatori", [0.95, 0.3]),
            ("3", "coltivare pomodori", [0.0, 1.0]),
        ]
    ):
        item = _item(session, external_id, title, emb)
        attach_item_to_idea(session, item, emb, threshold=0.99)  # niente merge di idee

    topics = assign_ideas_to_topics(session, threshold=0.8)
    assert len(session.exec(select(Topic)).all()) == 2  # tech + orto

    ideas = session.exec(select(Idea)).all()
    assert all(i.topic_id is not None for i in ideas)
    tech = [i for i in ideas if "pomodori" not in i.label]
    assert len({i.topic_id for i in tech}) == 1  # le due tech nello stesso topic
    assert topics


def test_topic_namer_is_used_and_failures_are_survived(session: Session) -> None:
    item = _item(session, "1", "agente AI", [1.0, 0.0])
    attach_item_to_idea(session, item, item.embedding_json, threshold=0.8)

    assign_ideas_to_topics(session, threshold=0.8, namer=lambda labels: "Agenti AI")
    assert session.exec(select(Topic)).one().label == "Agenti AI"

    def explode(labels):
        raise RuntimeError("LLM giù")

    assign_ideas_to_topics(session, threshold=0.8, namer=explode)  # non deve sollevare
    assert session.exec(select(Topic)).one().label == "Agenti AI"


def test_the_shared_index_decides_exactly_like_no_index(session: Session) -> None:
    """L'indice è solo un pre-filtro: gli accorpamenti devono essere identici.

    È l'unica garanzia che conta, perché l'indice esiste per ragioni di costo —
    riusarlo per un run intero evita di ricaricare tutte le idee a ogni item.
    """
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.1, 0.0],  # doppione del primo
        [0.5, 0.866, 0.0],
        [0.0, 1.0, 0.0],
        [0.1, 0.995, 0.0],  # doppione del quarto
    ]

    def grouping(shared: bool) -> list[list[int]]:
        engine = create_engine("sqlite://")  # in memoria: un DB per passaggio
        init_db(engine)
        with Session(engine) as s:
            index = IdeaIndex(s) if shared else None
            groups: dict[int, list[int]] = {}
            for position, vec in enumerate(vectors):
                item = upsert_item(
                    s,
                    Item(
                        source="hn",
                        external_id=str(position),
                        title=f"item {position}",
                        embedding_json=vec,
                    ),
                )
                idea = attach_item_to_idea(
                    s, item, vec, 0.85, cohesion_floor=0.8, index=index
                )
                groups.setdefault(idea.id, []).append(position)
            return sorted(map(sorted, groups.values()))

    assert grouping(shared=True) == grouping(shared=False)
    # 2, 3 e 4 puntano tutti "a nord" e sono vicini fra loro a due a due: la
    # coesione è rispettata, quindi è una fusione legittima, non una catena.
    assert grouping(shared=True) == [[0, 1], [2, 3, 4]]


def test_the_index_sees_ideas_born_during_the_same_run(session: Session) -> None:
    """Un'idea creata a metà ciclo deve poter accogliere l'item successivo."""
    index = IdeaIndex(session)
    assert len(index) == 0

    first = _item(session, "1", "primo", [1.0, 0.0])
    idea = attach_item_to_idea(session, first, [1.0, 0.0], 0.85, index=index)
    assert len(index) == 1

    second = _item(session, "2", "quasi identico", [0.99, 0.1])
    again = attach_item_to_idea(session, second, [0.99, 0.1], 0.85, index=index)

    assert again.id == idea.id  # l'indice conosceva già l'idea appena nata


def test_small_topics_are_not_named_by_the_llm(session: Session) -> None:
    """Un topic da poche idee eredita il titolo: niente chiamata al modello.

    Con le soglie tarate i topic passano da decine a centinaia e la quasi
    totalità ha una sola idea: nominarli tutti costerebbe un'ora di 7B per run.
    """
    calls: list[list[str]] = []

    def namer(labels: list[str]) -> str:
        calls.append(labels)
        return "Nome dal modello"

    for i, vec in enumerate([[1.0, 0.0], [0.99, 0.1], [0.0, 1.0]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)  # 3 idee distinte

    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=2)

    topics = session.exec(select(Topic)).all()
    by_size = {
        t.label: len(session.exec(select(Idea).where(Idea.topic_id == t.id)).all())
        for t in topics
    }
    assert len(calls) == 1  # solo il topic da 2 idee
    assert by_size["Nome dal modello"] == 2
    assert "idea 2" in by_size  # il singleton ha tenuto il titolo dell'idea


def test_unchanged_topics_are_not_renamed(session: Session) -> None:
    """Un topic la cui composizione non cambia non si ripaga a ogni run."""
    calls: list[list[str]] = []

    def namer(labels: list[str]) -> str:
        calls.append(labels)
        return f"nome {len(calls)}"

    for i, vec in enumerate([[1.0, 0.0], [0.99, 0.1]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)

    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)
    assert len(calls) == 1
    topic = session.exec(select(Topic)).one()
    assert topic.label == "nome 1"

    # La fotografia del run registra la composizione: dal run dopo, se non è
    # cambiata, il nome non si ricalcola.
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(TopicStat(topic_id=topic.id, run_id=run.id, n_ideas=2, n_items=2))
    session.commit()

    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)

    assert len(calls) == 1  # nessuna chiamata in più
    assert session.exec(select(Topic)).one().label == "nome 1"


def test_an_unreadable_label_is_redone_even_if_nothing_changed(
    session: Session,
) -> None:
    """Un'etichetta in un altro alfabeto non deve sopravvivere ai run.

    Il 7B a volte risponde in cinese ("AI开源与应用"): quelle già in archivio
    resterebbero lì fino al prossimo cambio di composizione, che potrebbe non
    arrivare mai.
    """
    calls: list[list[str]] = []

    def namer(labels: list[str]) -> str:
        calls.append(labels)
        return "agenti AI per il codice"

    for i, vec in enumerate([[1.0, 0.0], [0.99, 0.1]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)
    topic = session.exec(select(Topic)).one()
    topic.label = "AI开源与应用"
    session.add(topic)
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(TopicStat(topic_id=topic.id, run_id=run.id, n_ideas=2, n_items=2))
    session.commit()

    # Composizione identica: senza l'eccezione non ci sarebbe una seconda chiamata.
    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)

    assert len(calls) == 2
    assert session.exec(select(Topic)).one().label == "agenti AI per il codice"


def test_a_topic_that_grew_gets_renamed(session: Session) -> None:
    """Se invece la composizione cambia, l'etichetta va rifatta."""
    calls: list[list[str]] = []

    def namer(labels: list[str]) -> str:
        calls.append(labels)
        return f"nome {len(calls)}"

    for i, vec in enumerate([[1.0, 0.0], [0.99, 0.1]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)
    topic = session.exec(select(Topic)).one()

    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(TopicStat(topic_id=topic.id, run_id=run.id, n_ideas=2, n_items=2))
    session.commit()

    third = _item(session, "3", "idea 3", [0.98, 0.15])
    attach_item_to_idea(session, third, third.embedding_json, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.9, namer=namer, label_min_ideas=1)

    assert len(calls) == 2
    assert session.exec(select(Topic)).one().label == "nome 2"


def test_group_indices_by_similarity_threshold_effect() -> None:
    vectors = [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]  # cos(v0,v1)=0.8, cos(v0,v2)=0
    loose = group_indices_by_similarity(vectors, threshold=0.7)
    strict = group_indices_by_similarity(vectors, threshold=0.95)
    assert loose == [[0, 1], [2]]
    assert strict == [[0], [1], [2]]


def test_group_indices_predicts_assign_ideas_from_scratch(session: Session) -> None:
    """La simulazione dello sweep deve PREVEDERE esattamente il recluster reale."""
    vectors = [[1.0, 0.0], [0.95, 0.31225], [0.0, 1.0], [0.1, 0.995]]
    for i, vec in enumerate(vectors):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)  # 4 idee distinte

    predicted = group_indices_by_similarity(vectors, threshold=0.9)

    assign_ideas_to_topics(session, threshold=0.9)
    ideas = session.exec(select(Idea)).all()
    actual: dict[int, list[int]] = {}
    for index, idea in enumerate(ideas):
        actual.setdefault(idea.topic_id, []).append(index)

    assert sorted(map(sorted, predicted)) == sorted(map(sorted, actual.values()))


def test_sweep_topic_thresholds_reports_without_writing(session: Session) -> None:
    for i, vec in enumerate([[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)

    rows = sweep_topic_thresholds(session, [0.7, 0.95])

    assert [r["n_topics"] for r in rows] == [2, 3]
    assert rows[0]["max_size"] == 2
    assert rows[0]["n_singleton"] == 1
    assert rows[0]["biggest_sample"] == ["idea 0", "idea 1"]
    # Anteprima pura: la sweep non deve aver creato alcun topic.
    assert session.exec(select(Topic)).all() == []
