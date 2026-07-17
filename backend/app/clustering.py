"""Aggregazione semantica: item -> idee, idee -> topic.

Sostituisce il vecchio 1 item = 1 idea. Due item che raccontano la stessa cosa
(lo stesso progetto su HN e su GitHub, o due articoli sullo stesso annuncio)
finiscono nella *stessa* idea se i loro embedding sono abbastanza vicini.
Lo stesso meccanismo, con soglia più permissiva, raggruppa le idee in topic —
che sono poi l'unità su cui misuriamo i trend nel tempo.
"""

import logging
from collections.abc import Callable

from sqlmodel import Session, select

from app.embeddings import Vector, centroid, cosine
from app.models import Idea, Item, Topic, utcnow

logger = logging.getLogger(__name__)


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
) -> Idea:
    """Collega l'item all'idea semanticamente più vicina, o ne crea una nuova.

    Senza embedding si degrada al vecchio comportamento 1 item = 1 idea.
    """
    if item.ideas:  # item già visto in un run precedente
        return item.ideas[0]

    if embedding is None:
        return _create_idea(session, item, None)

    best: Idea | None = None
    best_sim = -1.0
    for idea in session.exec(select(Idea)).all():
        if not idea.centroid_json:
            continue
        sim = cosine(embedding, idea.centroid_json)
        if sim > best_sim:
            best, best_sim = idea, sim

    if best is not None and best_sim >= threshold:
        best.items.append(item)
        best.last_seen = utcnow()
        session.add(best)
        session.commit()
        _refresh_centroid(session, best)
        return best

    return _create_idea(session, item, embedding)


def assign_ideas_to_topics(
    session: Session,
    threshold: float,
    namer: Callable[[list[str]], str] | None = None,
) -> list[Topic]:
    """Raggruppa le idee in topic per similarità dei centroidi.

    I topic *persistono* tra un run e l'altro: è ciò che rende misurabile un
    trend. Un'idea nuova entra in un topic esistente se abbastanza vicina,
    altrimenti ne apre uno.
    """
    topics = list(session.exec(select(Topic)).all())
    ideas = [
        idea
        for idea in session.exec(select(Idea)).all()
        if idea.centroid_json
    ]

    for idea in ideas:
        best: Topic | None = None
        best_sim = -1.0
        for topic in topics:
            if not topic.centroid_json:
                continue
            sim = cosine(idea.centroid_json, topic.centroid_json)
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
            idea.topic_id = topic.id
        session.add(idea)
    session.commit()

    # Ricalcola centroidi ed etichette dei topic sui membri effettivi.
    for topic in topics:
        members = session.exec(select(Idea).where(Idea.topic_id == topic.id)).all()
        if not members:
            continue
        new_centroid = centroid([m.centroid_json for m in members if m.centroid_json])
        if new_centroid is not None:
            topic.centroid_json = new_centroid
        if namer is not None:
            try:
                topic.label = namer([m.label for m in members])[:80]
            except Exception as exc:  # un naming fallito non deve fermare il run
                logger.warning("Naming del topic fallito: %s", exc)
        session.add(topic)
    session.commit()
    return topics


def group_indices_by_similarity(
    vectors: list[Vector], threshold: float
) -> list[list[int]]:
    """Raggruppamento greedy IDENTICO a ``assign_ideas_to_topics`` da zero.

    Il rappresentante di un gruppo è il centroide del primo membro e non si
    aggiorna durante il passaggio — esattamente come i topic appena creati nel
    recluster reale (i centroidi si ricalcolano solo alla fine). Serve a
    PREVEDERE l'esito di una ``topic_threshold`` senza scrivere nulla.
    """
    reps: list[Vector] = []
    groups: list[list[int]] = []
    for index, vector in enumerate(vectors):
        best = -1
        best_sim = -1.0
        for g_index, rep in enumerate(reps):
            sim = cosine(vector, rep)
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
