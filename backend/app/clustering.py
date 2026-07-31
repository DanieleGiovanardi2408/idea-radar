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
from app.llm import is_plausible_label
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

    def forget(self, idea_id: int) -> None:
        """Toglie un'idea dall'indice (dissolta perché i suoi item sono migrati)."""
        self._units.pop(idea_id, None)

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


def best_idea_for(
    session: Session,
    embedding: Vector,
    threshold: float,
    *,
    cohesion_floor: float = 0.0,
    index: IdeaIndex | None = None,
    exclude: set[int] | None = None,
) -> Idea | None:
    """L'idea che può accogliere questo embedding, o ``None``.

    È il criterio di merge in un posto solo: lo usano sia il flusso incrementale
    di un run (``attach_item_to_idea``) sia la riparazione dei singleton
    (``app.healing``), così non possono divergere. ``exclude`` serve a chi sta
    valutando un item che già appartiene a un'idea e non deve ritrovare quella.
    """
    index = index if index is not None else IdeaIndex(session)
    excluded = exclude or set()
    probe = unit(embedding)

    best: Idea | None = None
    best_sim = -1.0
    # Il pre-filtro scarta in un prodotto scalare le idee lontane; sulle poche
    # che restano si decide guardando i membri a uno a uno.
    for idea_id in index.near(probe, threshold - _PREFILTER_MARGIN):
        if idea_id in excluded:
            continue
        idea = session.get(Idea, idea_id)
        if idea is None:
            continue
        members = [unit(vector) for vector in _member_vectors(idea)]
        nearest, farthest = _link_scores(probe, members)
        if nearest < threshold or farthest < cohesion_floor:
            continue
        if nearest > best_sim:
            best, best_sim = idea, nearest
    return best


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
    best = best_idea_for(
        session, embedding, threshold, cohesion_floor=cohesion_floor, index=index
    )

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

    Unica eccezione al secondo filtro: un'etichetta illeggibile (il 7B a volte
    risponde in cinese) va rifatta comunque, altrimenti resterebbe lì fino al
    prossimo cambio di composizione.
    """
    if n_members < min_ideas:
        return False
    if not is_plausible_label(topic.label):
        return True
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
    trend. Un'idea nuova entra in un topic esistente se abbastanza vicina.

    **Un'idea sola non fa un tema.** Prima ogni idea che non trovava compagni si
    apriva un topic col proprio titolo come nome: su 1002 topic, 784 avevano un
    solo membro, e il numero in copertina ("1002 temi") non descriveva niente.
    Non era una soglia da tarare — misurato sull'archivio, due idee *a caso*
    stanno a 0,614 di similarità con il 99° percentile a 0,750 e punte a 0,878,
    mentre il vicino più prossimo ha mediana 0,791: le due distribuzioni sono
    sovrapposte, e a legame singolo qualunque soglia produce o un blob da 886
    idee o polvere. Il raggruppamento resta quindi conservativo, ma chi non
    trova compagni tiene ``topic_id`` a ``None`` — è un'idea non raggruppata,
    non un tema da un elemento. Due orfane vicine ne aprono uno insieme, così un
    tema nuovo può ancora nascere.

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

    def piu_vicino(idea_unit: Vector) -> tuple[Topic | None, float]:
        best: Topic | None = None
        best_sim = -1.0
        for topic, topic_unit in zip(topics, topic_units, strict=True):
            if topic_unit is None:
                continue
            sim = dot(idea_unit, topic_unit)
            if sim > best_sim:
                best, best_sim = topic, sim
        return best, best_sim

    # Primo passaggio: chi trova un topic esistente ci entra. Chi non lo trova
    # NON si autoproclama tema: resta in panchina e ci riprova nel secondo giro.
    orfane: list[tuple[Idea, Vector]] = []
    for idea in ideas:
        idea_unit = unit(idea.centroid_json)
        best, best_sim = piu_vicino(idea_unit)
        if best is not None and best_sim >= threshold:
            idea.topic_id = best.id
            best.last_seen = utcnow()
            session.add(best)
            session.add(idea)
        else:
            idea.topic_id = None
            session.add(idea)
            orfane.append((idea, idea_unit))
    session.commit()

    # Secondo passaggio: due orfane abbastanza vicine aprono un tema INSIEME.
    #
    # Senza questo, "un'idea sola non fa un topic" diventerebbe "un tema nuovo
    # non può nascere": la prima idea resterebbe orfana per sempre e la seconda
    # non troverebbe nessun topic da cui essere accolta. Il confronto resta sul
    # centroide del topic appena creato, come nel primo giro — è quello che
    # impedisce la catena che a legame singolo incolla metà archivio in un blob.
    in_panchina: list[tuple[Idea, Vector]] = []
    for idea, idea_unit in orfane:
        best, best_sim = piu_vicino(idea_unit)
        if best is not None and best_sim >= threshold:
            idea.topic_id = best.id
            best.last_seen = utcnow()
            session.add(best)
            session.add(idea)
            continue

        compagna = None
        compagna_sim = -1.0
        for candidata in in_panchina:
            sim = dot(idea_unit, candidata[1])
            if sim > compagna_sim:
                compagna, compagna_sim = candidata, sim

        if compagna is not None and compagna_sim >= threshold:
            altra, altro_unit = compagna
            # L'etichetta provvisoria è il titolo dell'idea più forte delle due;
            # se il tema cresce oltre `label_min_ideas` il modello lo rinomina.
            topic = Topic(
                label=idea.label[:80],
                centroid_json=centroid([idea.centroid_json, altra.centroid_json]),
            )
            session.add(topic)
            session.commit()
            session.refresh(topic)
            topics.append(topic)
            topic_units.append(unit(topic.centroid_json or idea.centroid_json))
            idea.topic_id = topic.id
            altra.topic_id = topic.id
            session.add(idea)
            session.add(altra)
            in_panchina.remove(compagna)
        else:
            in_panchina.append((idea, idea_unit))
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
    merge_topics_with_the_same_label(session)
    dissolve_empty_topics(session)
    return [t for t in topics if session.get(Topic, t.id) is not None]


def dissolve_single_idea_topics(session: Session) -> dict:
    """Scioglie i topic che hanno una sola idea: manutenzione dell'archivio.

    Serve una volta, per i topic nati con la regola vecchia — 784 su 1002 nel DB
    del 27/07. Non basta un ``recluster``: il centroide di un topic da un membro
    *è* quella idea, quindi la ritroverebbe a similarità 1.0 e la rimetterebbe
    dentro. Vanno sciolti prima, così le idee tornano in circolo e possono
    accoppiarsi tra loro al prossimo raggruppamento.

    Le fotografie di quei topic se ne vanno con loro: una serie di trend costruita
    su un gruppo da un'idea non misura un tema, misura quell'idea — che ha già la
    sua storia negli Score.
    """
    per_topic: dict[int, list[Idea]] = {}
    for idea in session.exec(select(Idea)).all():
        if idea.topic_id is not None:
            per_topic.setdefault(idea.topic_id, []).append(idea)

    liberate = 0
    sciolti = 0
    fotografie = 0
    for topic_id, membri in per_topic.items():
        if len(membri) > 1:
            continue
        for idea in membri:
            idea.topic_id = None
            session.add(idea)
            liberate += 1
        for stat in session.exec(
            select(TopicStat).where(TopicStat.topic_id == topic_id)
        ).all():
            session.delete(stat)
            fotografie += 1
        topic = session.get(Topic, topic_id)
        if topic is not None:
            session.delete(topic)
            sciolti += 1
    session.commit()
    # I topic rimasti senza membri per altre vie se ne vanno con la stessa scopa.
    sciolti += dissolve_empty_topics(session)
    return {
        "n_dissolved": sciolti,
        "n_ideas_freed": liberate,
        "n_stats_removed": fotografie,
        "n_topics_left": len(session.exec(select(Topic)).all()),
    }


def dissolve_empty_topics(session: Session) -> int:
    """Cancella i topic che non hanno più nessuna idea, e le loro fotografie.

    Da quando un tema vuole almeno due idee, un topic può *svuotarsi*: se i suoi
    membri non si tengono più (soglia cambiata, idee fuse altrove) tornano non
    raggruppati e la riga resta lì, senza contenuto. `topics_overview` la
    nasconde già, ma ``/stats`` conta le righe: senza questa pulizia il numero
    dei temi ricomincerebbe a gonfiarsi, che è il difetto da cui siamo partiti.

    Le fotografie vanno con lui: una serie di trend che descrive un gruppo che
    non esiste più è un fantasma, e le idee che conteneva hanno la loro storia.
    """
    vivi = {
        idea.topic_id
        for idea in session.exec(select(Idea)).all()
        if idea.topic_id is not None
    }
    sciolti = 0
    for topic in list(session.exec(select(Topic)).all()):
        if topic.id in vivi:
            continue
        for stat in session.exec(
            select(TopicStat).where(TopicStat.topic_id == topic.id)
        ).all():
            session.delete(stat)
        session.delete(topic)
        sciolti += 1
    if sciolti:
        session.commit()
        logger.info("Sciolti %d topic senza più idee", sciolti)
    return sciolti


def _normalized_label(label: str) -> str:
    return " ".join(label.lower().split())


def merge_topics_with_the_same_label(session: Session) -> int:
    """Fonde i topic che il modello ha chiamato allo stesso modo.

    Sull'archivio reale "Agenti AI per il self-hosting" esisteva DUE volte, con
    19 idee per parte, e nella vista a due livelli comparivano due intestazioni
    identiche sotto lo stesso macro-tema. Il nome è il giudizio del modello su
    cosa sia quel gruppo: se lo ripete, per lui sono la stessa cosa, e tenerli
    separati è una distinzione che nessuno sa spiegare.

    Sopravvive il topic più vecchio (id minore): è quello che ha aperto il tema,
    e i suoi ``TopicStat`` sono la serie storica su cui poggia la vista Trend.
    """
    by_label: dict[str, list[Topic]] = {}
    for topic in session.exec(select(Topic).order_by(Topic.id)).all():
        by_label.setdefault(_normalized_label(topic.label), []).append(topic)

    merged = 0
    for duplicates in by_label.values():
        if len(duplicates) < 2:
            continue
        survivor, *absorbed = duplicates
        for victim in absorbed:
            for idea in session.exec(
                select(Idea).where(Idea.topic_id == victim.id)
            ).all():
                idea.topic_id = survivor.id
                session.add(idea)
            # Le fotografie del topic assorbito non hanno più un topic: via.
            for stat in session.exec(
                select(TopicStat).where(TopicStat.topic_id == victim.id)
            ).all():
                session.delete(stat)
            survivor.first_seen = min(survivor.first_seen, victim.first_seen)
            survivor.last_seen = max(survivor.last_seen, victim.last_seen)
            session.add(survivor)
            session.delete(victim)
            merged += 1
    if merged:
        session.commit()
        # Il centroide del sopravvissuto ora deve coprire anche i nuovi membri.
        for duplicates in by_label.values():
            if len(duplicates) < 2:
                continue
            survivor = session.get(Topic, duplicates[0].id)
            if survivor is None:
                continue
            members = session.exec(
                select(Idea).where(Idea.topic_id == survivor.id)
            ).all()
            new_centroid = centroid(
                [m.centroid_json for m in members if m.centroid_json]
            )
            if new_centroid is not None:
                survivor.centroid_json = new_centroid
                session.add(survivor)
        session.commit()
    return merged


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
