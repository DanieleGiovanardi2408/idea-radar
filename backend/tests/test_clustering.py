from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.clustering import (
    IdeaIndex,
    _refresh_centroid,
    assign_ideas_to_topics,
    attach_item_to_idea,
    dissolve_single_idea_topics,
    group_indices_by_similarity,
    merge_topics_with_the_same_label,
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

    # UN topic, non due: le due idee tech si accoppiano, i pomodori restano
    # un'idea NON RAGGRUPPATA. Prima ogni orfana si apriva un tema col proprio
    # titolo, ed è così che 1002 topic ne contenevano 784 da un solo membro.
    assert len(session.exec(select(Topic)).all()) == 1
    assert topics

    ideas = session.exec(select(Idea)).all()
    tech = [i for i in ideas if "pomodori" not in i.label]
    orto = [i for i in ideas if "pomodori" in i.label]
    assert len({i.topic_id for i in tech}) == 1  # le due tech nello stesso topic
    assert tech[0].topic_id is not None
    assert orto[0].topic_id is None  # nessun tema inventato per lei


def test_two_orphans_can_still_open_a_new_theme(session: Session) -> None:
    """"Un'idea sola non fa un tema" non deve diventare "un tema non nasce".

    Se l'orfana potesse solo entrare in un topic esistente, la PRIMA idea di un
    tema nuovo resterebbe orfana per sempre e la seconda non troverebbe nessuno
    ad accoglierla: il radar smetterebbe di scoprire argomenti.
    """
    # Primo run: una sola idea, nessun compagno, nessun tema.
    prima = _item(session, "1", "agenti per il self-hosting", [1.0, 0.0])
    attach_item_to_idea(session, prima, prima.embedding_json, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.8)

    assert session.exec(select(Topic)).all() == []
    assert session.exec(select(Idea)).one().topic_id is None

    # Run dopo: arriva un'idea vicina. Ora il tema esiste, e contiene entrambe.
    seconda = _item(session, "2", "self-hosting di agenti AI", [0.97, 0.24])
    attach_item_to_idea(session, seconda, seconda.embedding_json, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.8)

    topic = session.exec(select(Topic)).one()
    membri = session.exec(select(Idea).where(Idea.topic_id == topic.id)).all()
    assert len(membri) == 2  # anche la prima, che era rimasta in panchina


def test_an_idea_that_loses_its_companions_goes_back_to_ungrouped(
    session: Session,
) -> None:
    """Un tema che si svuota non lascia in piedi un topic da un membro."""
    for i, vec in enumerate([[1.0, 0.0], [0.97, 0.24]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.8)
    assert len(session.exec(select(Topic)).all()) == 1

    # Con una soglia severa la coppia non si tiene più: entrambe tornano
    # non raggruppate invece di diventare due temi da un'idea.
    assign_ideas_to_topics(session, threshold=0.999)

    assert all(i.topic_id is None for i in session.exec(select(Idea)).all())
    # E il topic svuotato non resta come riga fantasma: `/stats` conta le righe,
    # quindi lasciarlo lì rigonfierebbe il numero dei temi.
    assert session.exec(select(Topic)).all() == []


def test_topic_namer_is_used_and_failures_are_survived(session: Session) -> None:
    # Due idee vicine ma distinte: un topic nasce da una coppia, non da una sola.
    for external_id, vec in (("1", [1.0, 0.0]), ("2", [0.98, 0.15])):
        item = _item(session, external_id, f"agente AI {external_id}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)

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
    # La terza idea non ha aperto un tema col proprio titolo: è non raggruppata.
    assert list(by_size) == ["Nome dal modello"]
    orfana = session.exec(
        select(Idea).where(Idea.label == "idea 2", Idea.topic_id == None)  # noqa: E711
    ).one()
    assert orfana.topic_id is None


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


def test_topics_named_the_same_are_merged(session: Session) -> None:
    """Sull'archivio reale "Agenti AI per il self-hosting" esisteva due volte.

    Con 19 idee per parte, e nella vista a due livelli due intestazioni identiche
    sotto lo stesso macro-tema. Il nome è il giudizio del modello su cosa sia quel
    gruppo: se lo ripete, per lui sono la stessa cosa.
    """
    names = iter(["Agenti AI", "agenti  ai", "Domotica"])

    def namer(labels: list[str]) -> str:
        return next(names)

    # Tre COPPIE, non tre idee sole: da quando un'idea sola non apre un tema,
    # per avere tre topic distinti servono tre coppie distinte. Dentro la coppia
    # la similarità è 0.9999 (sopra la soglia dei topic, sotto quella delle idee,
    # quindi restano idee separate che condividono un tema); tra coppie è ~0.014.
    coppie = [
        [[1.0, 0.0, 0.0], [0.9999, 0.0141, 0.0]],
        [[0.0, 1.0, 0.0], [0.0141, 0.9999, 0.0]],
        [[0.0, 0.0, 1.0], [0.0, 0.0141, 0.9999]],
    ]
    for i, vec in enumerate([v for coppia in coppie for v in coppia]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.99999)

    # Soglia altissima: tre topic distinti, che il namer battezza in modo doppio.
    assign_ideas_to_topics(session, threshold=0.999, namer=namer, label_min_ideas=1)

    topics = session.exec(select(Topic)).all()
    labels = sorted(t.label for t in topics)
    assert labels == ["Agenti AI", "Domotica"]  # i due omonimi sono uno
    survivor = next(t for t in topics if t.label == "Agenti AI")
    members = session.exec(select(Idea).where(Idea.topic_id == survivor.id)).all()
    assert len(members) == 4  # le due coppie fuse, nessuna idea rimasta orfana


def test_merging_keeps_the_older_topic_and_its_history(session: Session) -> None:
    """Sopravvive chi ha aperto il tema: i suoi TopicStat sono la serie del Trend."""
    old = Topic(label="Agenti AI", centroid_json=[1.0, 0.0])
    session.add(old)
    session.commit()
    session.refresh(old)
    young = Topic(label="agenti ai", centroid_json=[0.9, 0.1])
    session.add(young)
    session.commit()
    session.refresh(young)

    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    session.add(TopicStat(topic_id=old.id, run_id=run.id, n_ideas=3, n_items=3))
    session.add(TopicStat(topic_id=young.id, run_id=run.id, n_ideas=1, n_items=1))
    session.commit()

    merged = merge_topics_with_the_same_label(session)

    assert merged == 1
    assert session.get(Topic, old.id) is not None
    assert session.get(Topic, young.id) is None
    stats = session.exec(select(TopicStat)).all()
    assert [s.topic_id for s in stats] == [old.id]  # niente statistiche orfane


def test_different_labels_are_left_alone(session: Session) -> None:
    for label in ("Agenti AI", "Domotica", "Dev infra"):
        session.add(Topic(label=label, centroid_json=[1.0, 0.0]))
    session.commit()

    assert merge_topics_with_the_same_label(session) == 0
    assert len(session.exec(select(Topic)).all()) == 3


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


def test_single_idea_topics_are_dissolved(session: Session) -> None:
    """Manutenzione dell'archivio nato con la regola vecchia.

    Non basta un `recluster`: il centroide di un topic da un membro *è* quella
    idea, quindi la ritrova a similarità 1.0 e la rimette dentro. Vanno sciolti.
    """
    # Una coppia (tema vero) e due idee sole con il loro topic finto, come li
    # creava la regola vecchia.
    coppia = []
    for i, vec in enumerate([[1.0, 0.0], [0.97, 0.24]]):
        item = _item(session, f"c{i}", f"coppia {i}", vec)
        coppia.append(attach_item_to_idea(session, item, vec, threshold=0.999))
    tema = Topic(label="tema vero", centroid_json=[0.99, 0.12])
    session.add(tema)
    session.commit()
    session.refresh(tema)
    for idea in coppia:
        idea.topic_id = tema.id
        session.add(idea)

    finti = []
    for i, vec in enumerate([[0.0, 1.0], [-1.0, 0.0]]):
        item = _item(session, f"s{i}", f"sola {i}", vec)
        idea = attach_item_to_idea(session, item, vec, threshold=0.999)
        topic = Topic(label=f"sola {i}", centroid_json=vec)
        session.add(topic)
        session.commit()
        session.refresh(topic)
        idea.topic_id = topic.id
        session.add(idea)
        finti.append(topic)
    run = Run(status=RunStatus.DONE)
    session.add(run)
    session.commit()
    for topic in [tema, *finti]:
        session.add(TopicStat(topic_id=topic.id, run_id=run.id, n_ideas=1, n_items=1))
    session.commit()

    summary = dissolve_single_idea_topics(session)

    assert summary["n_dissolved"] == 2
    assert summary["n_ideas_freed"] == 2
    assert summary["n_stats_removed"] == 2  # le fotografie dei finti, non del vero
    assert summary["n_topics_left"] == 1

    # Il tema vero è intatto, con la sua storia.
    assert session.exec(select(Topic)).one().label == "tema vero"
    assert len(session.exec(select(TopicStat)).all()) == 1
    # Le due idee sole sono tornate in circolo, non cancellate.
    orfane = [i for i in session.exec(select(Idea)).all() if i.topic_id is None]
    assert len(orfane) == 2


def test_dissolving_is_idempotent(session: Session) -> None:
    """Rilanciarlo su un archivio già pulito non deve fare danni."""
    for i, vec in enumerate([[1.0, 0.0], [0.97, 0.24]]):
        item = _item(session, str(i), f"idea {i}", vec)
        attach_item_to_idea(session, item, vec, threshold=0.999)
    assign_ideas_to_topics(session, threshold=0.8)

    primo = dissolve_single_idea_topics(session)
    secondo = dissolve_single_idea_topics(session)

    assert primo["n_dissolved"] == 0  # la coppia non è un singleton
    assert secondo == primo
    assert len(session.exec(select(Topic)).all()) == 1
