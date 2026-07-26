"""Aggregazione semantica: item -> idee, idee -> topic.

Sostituisce il vecchio 1 item = 1 idea. Due item che raccontano la stessa cosa
(lo stesso progetto su HN e su GitHub, o due articoli sullo stesso annuncio)
finiscono nella *stessa* idea se i loro embedding sono abbastanza vicini.
Lo stesso meccanismo, con soglia più permissiva, raggruppa le idee in topic —
che sono poi l'unità su cui misuriamo i trend nel tempo.
"""

import logging
from collections.abc import Callable

from sqlalchemy import func
from sqlmodel import Session, select

from app.embeddings import Vector, centroid, dot, unit
from app.models import Idea, Item, Topic, TopicStat, utcnow

logger = logging.getLogger(__name__)

# Margine del pre-filtro sui centroidi. Il centroide non DECIDE più il merge
# (vedi ``attach_item_to_idea``), ma resta un indice economico: evita di
# caricare gli embedding di tutte le idee per ogni item. Il centroide di un
# gruppo piccolo e coeso — l'unico che il nuovo criterio produce — resta vicino
# ai suoi membri, quindi nessuna fusione legittima cade fuori dalla rete.
_PREFILTER_MARGIN = 0.15


class IdeaIndex:
    """Centroidi normalizzati di tutte le idee, tenuti in RAM per un run intero.

    Senza indice ``attach_item_to_idea`` riinterroga *tutte* le idee per ogni
    item e ne rinormalizza i centroidi ogni volta: con un archivio da qualche
    migliaio di idee un run passa il suo tempo a idratare oggetti ORM. L'indice
    si costruisce una volta, si aggiorna quando un'idea nasce o cambia, e riduce
    il costo per item a un prodotto scalare per idea.

    Non cambia *quali* idee vengono scelte: serve solo da pre-filtro, la
    decisione resta sui membri (c'è un test che verifica l'equivalenza).
    """

    def __init__(self, session: Session) -> None:
        self._units: dict[int, Vector] = {}
        for idea in session.exec(select(Idea)).all():
            self.remember(idea)

    def remember(self, idea: Idea) -> None:
        """Registra (o aggiorna) il centroide di un'idea appena creata o cambiata."""
        if idea.id is not None and idea.centroid_json:
            self._units[idea.id] = unit(idea.centroid_json)

    def near(self, probe: Vector, min_sim: float) -> list[int]:
        """Id delle idee il cui centroide è almeno ``min_sim`` vicino alla sonda."""
        return [
            idea_id
            for idea_id, centroid_unit in self._units.items()
            if dot(probe, centroid_unit) >= min_sim
        ]

    def __len__(self) -> int:
        return len(self._units)


def _member_vectors(idea: Idea) -> list[Vector]:
    return [item.embedding_json for item in idea.items if item.embedding_json]


def _link_scores(probe: Vector, members: list[Vector]) -> tuple[float, float]:
    """Similarità col membro più VICINO e col più LONTANO dell'idea.

    La prima è il legame singolo (l'item somiglia a *qualcosa* nel gruppo), la
    seconda la coesione (il gruppo resta omogeneo dopo l'aggiunta).

    Vuole vettori già normalizzati: su vettori unitari il prodotto scalare *è*
    il coseno, e qui si confronta un item contro tutti i membri di più idee.
    """
    sims = [dot(probe, member) for member in members]
    if not sims:
        return -1.0, -1.0
    return max(sims), min(sims)


def _refresh_centroid(session: Session, idea: Idea) -> None:
    vectors = [i.embedding_json for i in idea.items if i.embedding_json]
    new_centroid = centroid(vectors)
    if new_centroid is not None:
        idea.centroid_json = new_centroid
        session.add(idea)
        session.commit()


def _create_idea(session: Session, item: Item, embedding: Vector | None) -> Idea:
    idea = Idea(label=item.title, centroid_json=embedding)
    idea.items = [item]
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


def attach_item_to_idea(
    session: Session,
    item: Item,
    embedding: Vector | None,
    threshold: float,
    *,
    cohesion_floor: float = 0.0,
    index: IdeaIndex | None = None,
) -> Idea:
    """Collega l'item all'idea semanticamente più vicina, o ne crea una nuova.

    Il confronto è sempre item-contro-item, MAI contro il centroide dell'idea.
    Il centroide è una media: più un'idea assorbe membri diversi, più il suo
    centroide scivola verso il centro dello spazio degli embedding, dove è
    "abbastanza simile" a qualunque cosa. Decidere il merge sul centroide rende
    quindi le idee grandi delle calamite che crescono da sole — misurato sul DB
    reale: un'idea da 740 item la cui similarità media verso item casuali era
    0.78, sopra la soglia di 0.75. Da qui i due criteri, entrambi sui membri:

    - **legame singolo**: l'item deve somigliare almeno a UN membro (>= soglia);
    - **coesione**: deve somigliare a TUTTI i membri (>= ``cohesion_floor``).

    Il primo trova i doppioni, il secondo impedisce la catena A~B~C con A e C
    estranei: un gruppo cresce solo restando omogeneo, quindi non può degenerare.
    ``cohesion_floor=0.0`` disattiva il secondo criterio (legame singolo puro).
    ``index`` è l'indice dei centroidi da riusare per tutto un run: se non c'è,
    se ne costruisce uno usa-e-getta (comodo per un item singolo, sprecato in un
    ciclo).

    Senza embedding si degrada al vecchio comportamento 1 item = 1 idea.
    """
    if item.ideas:  # item già visto in un run precedente
        return item.ideas[0]

    if embedding is None:
        return _create_idea(session, item, None)

    index = index if index is not None else IdeaIndex(session)
    probe = unit(embedding)

    best: Idea | None = None
    best_sim = -1.0
    # Il pre-filtro scarta in un prodotto scalare le idee lontane; sulle poche
    # che restano si decide guardando i membri a uno a uno.
    for idea_id in index.near(probe, threshold - _PREFILTER_MARGIN):
        idea = session.get(Idea, idea_id)
        if idea is None:
            continue
        members = [unit(vector) for vector in _member_vectors(idea)]
        nearest, farthest = _link_scores(probe, members)
        if nearest < threshold or farthest < cohesion_floor:
            continue
        if nearest > best_sim:
            best, best_sim = idea, nearest

    if best is not None:
        best.items.append(item)
        best.last_seen = utcnow()
        session.add(best)
        session.commit()
        _refresh_centroid(session, best)
        index.remember(best)
        return best

    created = _create_idea(session, item, embedding)
    index.remember(created)
    return created


def _last_topic_sizes(session: Session) -> dict[int, int]:
    """Quante idee aveva ogni topic l'ultima volta che è stato fotografato."""
    subq = (
        select(TopicStat.topic_id, func.max(TopicStat.run_id).label("run_id"))
        .group_by(TopicStat.topic_id)
        .subquery()
    )
    stmt = select(TopicStat).join(
        subq,
        (TopicStat.topic_id == subq.c.topic_id) & (TopicStat.run_id == subq.c.run_id),
    )
    return {stat.topic_id: stat.n_ideas for stat in session.exec(stmt).all()}


def _needs_label(
    topic: Topic, n_members: int, previous_sizes: dict[int, int], min_ideas: int
) -> bool:
    """Se valga la pena spendere una chiamata al modello per (ri)nominare un topic.

    Due filtri, entrambi per non pagare il 7B a vuoto:

    - un topic con pochi membri non ha niente da sintetizzare: il titolo
      dell'idea che lo apre è già un'etichetta migliore di una parafrasi;
    - un topic la cui composizione non è cambiata dall'ultima fotografia ha
      ancora l'etichetta giusta. Senza questo controllo ogni run rinominerebbe
      *tutti* i topic: con 52 erano un paio di minuti, con ~900 sarebbe un'ora.

    La composizione è confrontata per numero di idee: uno scambio a saldo zero
    passa inosservato e lascia l'etichetta vecchia. È il compromesso che tiene
    il costo a zero nei run in cui non cambia nulla.
    """
    if n_members < min_ideas:
        return False
    return previous_sizes.get(topic.id) != n_members


def assign_ideas_to_topics(
    session: Session,
    threshold: float,
    namer: Callable[[list[str]], str] | None = None,
    label_min_ideas: int = 1,
    on_progress: Callable[[str], None] | None = None,
) -> list[Topic]:
    """Raggruppa le idee in topic per similarità dei centroidi.

    I topic *persistono* tra un run e l'altro: è ciò che rende misurabile un
    trend. Un'idea nuova entra in un topic esistente se abbastanza vicina,
    altrimenti ne apre uno.

    ``label_min_ideas`` è la soglia sotto la quale un topic non viene nominato
    dall'LLM (vedi ``_needs_label``).
    """
    topics = list(session.exec(select(Topic)).all())
    ideas = [
        idea
        for idea in session.exec(select(Idea)).all()
        if idea.centroid_json
    ]
    # Ogni idea viene confrontata con ogni topic: normalizzare una volta sola
    # rende il confronto un prodotto scalare (~5x più veloce di ``cosine``).
    # Il valore normalizzato NON viene salvato: il coseno non cambia.
    topic_units = [unit(t.centroid_json) if t.centroid_json else None for t in topics]

    for idea in ideas:
        idea_unit = unit(idea.centroid_json)
        best: Topic | None = None
        best_sim = -1.0
        for topic, topic_unit in zip(topics, topic_units):
            if topic_unit is None:
                continue
            sim = dot(idea_unit, topic_unit)
            if sim > best_sim:
                best, best_sim = topic, sim

        if best is not None and best_sim >= threshold:
            idea.topic_id = best.id
            best.last_seen = utcnow()
            session.add(best)
        else:
            topic = Topic(label=idea.label[:80], centroid_json=idea.centroid_json)
            session.add(topic)
            session.commit()
            session.refresh(topic)
            topics.append(topic)
            topic_units.append(idea_unit)
            idea.topic_id = topic.id
        session.add(idea)
    session.commit()

    # Ricalcola centroidi ed etichette dei topic sui membri effettivi.
    previous_sizes = _last_topic_sizes(session)
    members_by_topic: dict[int, list[Idea]] = {}
    for topic in topics:
        members = list(session.exec(select(Idea).where(Idea.topic_id == topic.id)).all())
        if not members:
            continue
        members_by_topic[topic.id] = members
        new_centroid = centroid([m.centroid_json for m in members if m.centroid_json])
        if new_centroid is not None:
            topic.centroid_json = new_centroid
        session.add(topic)

    # Il naming è l'unica parte lenta (una chiamata al modello per topic): si
    # contano prima i topic da nominare, così l'avanzamento sa dove sta andando.
    to_name = (
        [
            topic
            for topic in topics
            if topic.id in members_by_topic
            and _needs_label(
                topic, len(members_by_topic[topic.id]), previous_sizes, label_min_ideas
            )
        ]
        if namer is not None
        else []
    )
    for done, topic in enumerate(to_name, start=1):
        if on_progress is not None:
            on_progress(f"nomi topic {done}/{len(to_name)}")
        try:
            topic.label = namer([m.label for m in members_by_topic[topic.id]])[:80]
        except Exception as exc:  # un naming fallito non deve fermare il run
            logger.warning("Naming del topic fallito: %s", exc)
        session.add(topic)
    session.commit()
    return topics


def group_items_by_similarity(
    vectors: list[Vector], threshold: float, cohesion_floor: float = 0.0
) -> list[list[int]]:
    """Raggruppamento IDENTICO a ``attach_item_to_idea`` su una lista di vettori.

    Riproduce i tre passaggi reali — pre-filtro sul centroide corrente, legame
    singolo, coesione — perché serve a PREVEDERE l'esito di un rebuild (o di
    soglie diverse) senza scrivere nulla. I vettori vanno passati nell'ordine
    di arrivo: il clustering è incrementale, quindi l'ordine conta.

    Qui i confronti sono quadratici sull'intero archivio, quindi si lavora su
    vettori normalizzati una volta sola: il coseno diventa un prodotto scalare
    (``dot``) e il giro costa ~5 volte meno, a parità di risultato.
    """
    units = [unit(v) for v in vectors]
    groups: list[list[int]] = []
    centroids: list[Vector] = []  # normalizzati, come i membri
    for index, vector in enumerate(units):
        best = -1
        best_sim = -1.0
        for g_index, group_centroid in enumerate(centroids):
            if dot(vector, group_centroid) < threshold - _PREFILTER_MARGIN:
                continue
            sims = [dot(vector, units[i]) for i in groups[g_index]]
            if min(sims) < cohesion_floor:
                continue
            nearest = max(sims)
            if nearest < threshold or nearest <= best_sim:
                continue
            best, best_sim = g_index, nearest
        if best >= 0:
            groups[best].append(index)
            new_centroid = centroid([units[i] for i in groups[best]])
            if new_centroid is not None:
                centroids[best] = unit(new_centroid)
        else:
            groups.append([index])
            centroids.append(vector)
    return groups


def group_indices_by_similarity(
    vectors: list[Vector], threshold: float
) -> list[list[int]]:
    """Raggruppamento greedy IDENTICO a ``assign_ideas_to_topics`` da zero.

    Il rappresentante di un gruppo è il centroide del primo membro e non si
    aggiorna durante il passaggio — esattamente come i topic appena creati nel
    recluster reale (i centroidi si ricalcolano solo alla fine). Serve a
    PREVEDERE l'esito di una ``topic_threshold`` senza scrivere nulla.
    """
    units = [unit(v) for v in vectors]  # confronti quadratici: si normalizza una volta
    reps: list[Vector] = []
    groups: list[list[int]] = []
    for index, vector in enumerate(units):
        best = -1
        best_sim = -1.0
        for g_index, rep in enumerate(reps):
            sim = dot(vector, rep)
            if sim > best_sim:
                best, best_sim = g_index, sim
        if best >= 0 and best_sim >= threshold:
            groups[best].append(index)
        else:
            reps.append(vector)
            groups.append([index])
    return groups


def sweep_topic_thresholds(session: Session, thresholds: list[float]) -> list[dict]:
    """Anteprima: che topic uscirebbero a soglie diverse, SENZA scritture.

    Per ogni soglia: quanti topic, la taglia del più grosso, quanti singleton,
    e un assaggio di etichette del gruppo più grosso — per capire a colpo
    d'occhio se il "topicone" è coeso o un minestrone. È il cuore di
    ``recluster --sweep``: si guarda, si sceglie, e solo allora si riscrive.
    """
    ideas = [idea for idea in session.exec(select(Idea)).all() if idea.centroid_json]
    vectors = [idea.centroid_json for idea in ideas]
    results: list[dict] = []
    for threshold in thresholds:
        groups = group_indices_by_similarity(vectors, threshold)
        sizes = sorted((len(group) for group in groups), reverse=True)
        biggest = max(groups, key=len, default=[])
        results.append(
            {
                "threshold": threshold,
                "n_topics": len(groups),
                "max_size": sizes[0] if sizes else 0,
                "n_singleton": sum(1 for size in sizes if size == 1),
                "biggest_sample": [ideas[i].label[:60] for i in biggest[:3]],
            }
        )
    return results
