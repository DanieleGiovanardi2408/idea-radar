"""Pipeline: fonti -> items -> embedding -> idee -> topic -> scores, dentro un Run.

Il ``Run`` viene aggiornato *durante* l'esecuzione (fase, contatori): è ciò che
permette al monitor di mostrare l'avanzamento invece di una barra finta.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import NamedTuple

from sqlmodel import Session, select

from app.appconfig import AppConfig, get_config
from app.clustering import (
    IdeaIndex,
    assign_ideas_to_topics,
    attach_item_to_idea,
    group_items_by_similarity,
)
from app.config import Settings, get_settings
from app.db import get_session, init_db, upsert_item
from app.embeddings import OllamaEmbedder, centroid, embed_items
from app.healing import heal_ideas
from app.lifecycle import archive_stale_ideas
from app.llm import IdeaInsight, OllamaClient, generate_insight, heuristic_insight
from app.models import (
    Idea,
    IdeaItem,
    Item,
    ItemStat,
    Run,
    RunStatus,
    Score,
    Topic,
    TopicStat,
    utcnow,
)
from app.queries import latest_scores
from app.runlock import run_lock
from app.scoring import ScoreResult, absolute_engagement, keyword_fit, score_item
from app.sources import Source, create_source

logger = logging.getLogger(__name__)


def _progress(session: Session, run: Run, **fields) -> None:
    """Salva subito lo stato del run così il monitor lo vede in tempo reale."""
    for key, value in fields.items():
        setattr(run, key, value)
    session.add(run)
    session.commit()


class _ProgressThrottle:
    """Scrive l'avanzamento del ciclo, ma non più spesso del necessario.

    Il Monitor lo legge dal DB e lo interroga ogni 2s: scrivere una transazione
    per item è utile quando un item costa secondi (LLM) e puro spreco quando ne
    passano venti al secondo perché gli insight arrivano dalla cache. La
    scrittura finale di una fase passa da ``force`` e non salta mai, così l'ultimo
    stato visibile è quello vero.
    """

    def __init__(self, min_seconds: float) -> None:
        self._min_seconds = max(0.0, min_seconds)
        self._last = 0.0

    def maybe(self, session: Session, run: Run, **fields) -> None:
        now = monotonic()
        if self._min_seconds and now - self._last < self._min_seconds:
            return
        self._last = now
        _progress(session, run, **fields)

    def force(self, session: Session, run: Run, **fields) -> None:
        self._last = monotonic()
        _progress(session, run, **fields)


def _collect(
    session: Session,
    run: Run,
    config: AppConfig,
    settings: Settings,
    sources: list[Source] | None,
) -> list[Item]:
    if sources is None:
        sources = [
            create_source(sc, config, settings) for sc in config.enabled_sources()
        ]

    collected: list[Item] = []
    stats: dict[str, dict] = {}
    fetched_total = 0
    new_total = 0

    for source in sources:
        name = type(source).__name__
        _progress(session, run, phase=f"raccolta: {name}")
        try:
            fetched = source.fetch()
        except Exception as exc:  # una fonte down non deve uccidere il run
            logger.warning("Fonte %s fallita: %s", name, exc)
            stats[name] = {"fetched": 0, "new": 0, "error": str(exc)[:200]}
            # L'errore va scritto SUBITO, non alla prossima fonte che riesce:
            # una fonte che cade per ultima non arriverebbe mai nel Monitor.
            # È così che arXiv ha potuto fallire a ogni run restando invisibile.
            _progress(session, run, sources_json=stats)
            continue

        new_here = 0
        for raw_item in fetched:
            existed = (
                session.exec(
                    select(Item).where(
                        Item.source == raw_item.source,
                        Item.external_id == raw_item.external_id,
                    )
                ).first()
                is not None
            )
            stored = upsert_item(session, raw_item)
            # Fotografa l'engagement di QUESTO run: sull'item l'upsert lo
            # sovrascrive, qui se ne conserva la storia — è la serie su cui
            # la heat "a delta" misura la velocità tra osservazioni.
            if session.get(ItemStat, (stored.id, run.id)) is None:
                session.add(
                    ItemStat(
                        item_id=stored.id,
                        run_id=run.id,
                        engagement_json=stored.engagement_json,
                        engagement=absolute_engagement(stored),
                    )
                )
            if not existed:
                new_here += 1
            collected.append(stored)

        # Una fonte può raccontare com'è andata oltre al conteggio: quante
        # richieste, quante perse. Senza questo, le tre fasce che GitHub ha
        # rifiutato nel run 56 restavano solo in un log che nessuno legge.
        report = getattr(source, "last_report", None)

        fetched_total += len(fetched)
        new_total += new_here
        stats[name] = {"fetched": len(fetched), "new": new_here}
        if report:
            stats[name].update(report)
        _progress(
            session,
            run,
            n_items_fetched=fetched_total,
            n_items_new=new_total,
            sources_json=stats,
        )

    return collected


def _topic_namer(config: AppConfig, ollama: OllamaClient | None):
    if not config.clustering.llm_topic_labels or ollama is None:
        return None
    return lambda labels: ollama.topic_label(labels)


def _record_topic_stats(session: Session, run: Run) -> int:
    """Fotografa i topic in questo run: è la base della vista trend.

    ``avg_composite`` si calcola sull'ultimo punteggio NOTO di ogni idea, non
    solo su quelli nati in questo run. Un run scora solo le idee che hanno
    ricevuto un item nuovo: contando solo quelle, ogni topic non toccato veniva
    fotografato a 0.0 — cioè "qualità zero" invece di "nessuna novità" — e la
    vista Trend mostrava crolli inventati. Il caso estremo era il run a vuoto
    (Mac offline, 0 item raccolti): azzerava la serie di *tutti* i topic in un
    colpo. Con l'ultimo punteggio noto, un run senza novità disegna una linea
    piatta, che è la verità.
    """
    topics = session.exec(select(Topic)).all()
    latest = latest_scores(session)
    counted = 0
    for topic in topics:
        ideas = session.exec(select(Idea).where(Idea.topic_id == topic.id)).all()
        if not ideas:
            continue
        composites = [latest[i.id].composite for i in ideas if i.id in latest]
        # Rifotografare lo stesso run è legittimo (``rescore`` e ``recluster`` lo
        # fanno): la fotografia si sovrascrive invece di duplicarsi.
        stat = session.get(TopicStat, (topic.id, run.id)) or TopicStat(
            topic_id=topic.id, run_id=run.id
        )
        stat.n_ideas = len(ideas)
        stat.n_items = sum(len(i.items) for i in ideas)
        stat.avg_composite = (sum(composites) / len(composites)) if composites else 0.0
        session.add(stat)
        counted += 1
    session.commit()
    return counted


def _cached_insight(session: Session, idea: Idea) -> IdeaInsight | None:
    """Riusa l'insight LLM già calcolato per un'idea, per non ripagare il 7B.

    L'insight testuale (summary/why/difficulty) è stabile: una volta prodotto
    non serve rigenerarlo a ogni run né per ogni doppione che finisce nella
    stessa idea. ``summary`` vive sull'idea, ``why_text``/``difficulty``
    sull'ultimo score. Restituisce ``None`` se l'idea non è ancora stata
    analizzata (nuova): in quel caso il chiamante genera l'insight ex novo.
    Il punteggio numerico NON passa di qui: resta ricalcolato a ogni run.
    """
    if not idea.summary:
        return None
    last = session.exec(
        select(Score).where(Score.idea_id == idea.id).order_by(Score.run_id.desc())
    ).first()
    if last is None:
        return None
    return IdeaInsight(
        summary=idea.summary,
        why_text=last.why_text or "",
        difficulty=last.difficulty,
    )


def _embed_phase(
    session: Session,
    run: Run,
    collected: list[Item],
    embedder: OllamaEmbedder,
    on_progress: Callable[[str], None] | None,
) -> None:
    """Embedding di tutti gli item che non ce l'hanno, in un colpo e un commit.

    Gli item già in archivio con embedding non si ricalcolano: il prefisso di
    task è lo stesso, quindi il vettore vecchio è ancora confrontabile. Un
    fallimento non è fatale — chi resta senza vettore diventa un'idea a sé,
    esattamente come prima.
    """
    da_fare = [item for item in collected if item.embedding_json is None]
    if not da_fare:
        return
    _progress(session, run, phase=f"embedding ({len(da_fare)} item)")
    if on_progress is not None:
        on_progress(f"embedding di {len(da_fare)} item…")

    vectors = embed_items(da_fare, embedder)
    for item in da_fare:
        vector = vectors.get(item.id) if item.id is not None else None
        if vector is not None:
            item.embedding_json = vector
            session.add(item)
    session.commit()


def run_pipeline(
    session: Session,
    config: AppConfig,
    settings: Settings,
    *,
    sources: list[Source] | None = None,
    ollama: OllamaClient | None = None,
    embedder: OllamaEmbedder | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Run:
    """Esegue un run completo e restituisce l'entità :class:`Run` aggiornata.

    ``sources``, ``ollama`` ed ``embedder`` sono iniettabili per i test.
    ``on_progress`` (opzionale) riceve una stringa a ogni avanzamento: la CLI
    la usa per mostrare il progresso a terminale senza toccare il DB.
    """
    run = Run()
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        ollama = ollama or OllamaClient(settings)
        embedder = embedder or OllamaEmbedder(
            settings, batch_size=config.throughput.embed_batch_size
        )

        collected = _collect(session, run, config, settings, sources)

        # Gli embedding si chiedono tutti insieme, prima del ciclo: sono
        # indipendenti tra loro e da tutto il resto, quindi non c'è motivo di
        # pagare un round-trip (e una transazione) per item come faceva la
        # versione a un item per volta.
        _embed_phase(session, run, collected, embedder, on_progress)

        if on_progress is not None:
            on_progress(f"raccolti {len(collected)} item, genero gli insight…")

        # Indice dei centroidi costruito UNA volta per run: dentro il ciclo ogni
        # item lo riusa, invece di ricaricare e rinormalizzare tutte le idee.
        idea_index = IdeaIndex(session)

        avanzamento = _ProgressThrottle(config.throughput.progress_min_seconds)
        for index, item in enumerate(collected, start=1):
            avanzamento.maybe(
                session, run, phase=f"analisi idee ({index}/{len(collected)})"
            )
            if on_progress is not None:
                on_progress(f"insight {index}/{len(collected)}")
            idea = attach_item_to_idea(
                session,
                item,
                item.embedding_json,
                config.clustering.idea_threshold,
                cohesion_floor=config.clustering.cohesion_floor,
                index=idea_index,
            )
            cached = _cached_insight(session, idea)
            if cached is not None:
                insight = cached
            elif keyword_fit(item, config.keywords) <= config.scoring.insight_min_fit:
                # Item del tutto fuori tema: niente 7B, basta l'insight euristico.
                insight = heuristic_insight(item)
            else:
                insight = generate_insight(item, settings, ollama=ollama)
            # La storia engagement dell'item (osservazione di questo run
            # inclusa): dove ci sono >= 2 osservazioni la heat è misurata a
            # delta invece che stimata dall'età.
            observations = session.exec(
                select(ItemStat).where(ItemStat.item_id == item.id)
            ).all()
            result = score_item(item, insight, config, observations=observations)

            idea.last_seen = utcnow()

            existing_score = session.get(Score, (idea.id, run.id))
            # La faccia dell'idea (summary, status, punteggio) deve venire dal
            # MIGLIORE item del run, non dall'ultimo processato: un item peggiore
            # non ridefinisce l'idea. Con la dedup attiva più item cadono nella
            # stessa idea, quindi qui ci si passa spesso — ed è il motivo per cui
            # prima un'idea poteva mostrare composite alto ma status "processed".
            if existing_score is not None and result.composite <= existing_score.composite:
                session.add(idea)
                session.commit()
                continue

            idea.summary = insight.summary
            idea.status = result.status
            session.add(idea)

            if existing_score is not None:
                session.delete(existing_score)
            session.add(
                Score(
                    idea_id=idea.id,
                    run_id=run.id,
                    heat=result.heat,
                    credibility=result.credibility,
                    feasibility=result.feasibility,
                    opportunity=result.opportunity,
                    fit=result.fit,
                    composite=result.composite,
                    why_text=insight.why_text,
                    difficulty=insight.difficulty,
                    profile=result.profile,
                )
            )
            session.commit()

        if on_progress is not None:
            on_progress("raggruppo in topic…")
        # `force`: il cambio di fase non si salta mai, anche se l'ultimo item è
        # appena passato dal throttle.
        avanzamento.force(session, run, phase="raggruppamento in topic")
        assign_ideas_to_topics(
            session,
            config.clustering.topic_threshold,
            namer=_topic_namer(config, ollama),
            label_min_ideas=config.clustering.topic_label_min_ideas,
        )
        n_topics = _record_topic_stats(session, run)

        # Ciclo di vita in coda al run: chi non porta segnali da troppo tempo
        # esce dalle viste vive (e rientra da solo se un item la riattiva).
        _progress(session, run, phase="archivio idee stantie")
        archive_stale_ideas(session, config.lifecycle.archive_after_days)

        run.finished_at = utcnow()
        run.status = RunStatus.DONE
        run.phase = "completato"
        # Contatori del run calcolati sugli score effettivi di QUESTO run, per
        # coerenza col composite mostrato (e non dallo status dell'idea, che può
        # persistere da run precedenti o cambiare tra item della stessa idea).
        run_scores = session.exec(select(Score).where(Score.run_id == run.id)).all()
        n_proposed = sum(1 for s in run_scores if s.composite >= config.scoring.threshold)
        run.n_items = len(collected)
        run.n_ideas_processed = len(run_scores) - n_proposed
        run.n_ideas_proposed = n_proposed
        run.n_ideas_total = len(session.exec(select(Idea)).all())
        run.n_topics = n_topics
        session.add(run)
        session.commit()
        session.refresh(run)
        return run
    except Exception as exc:
        logger.exception("Run fallito")
        run.status = RunStatus.FAILED
        run.phase = "errore"
        run.error = str(exc)[:500]
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        session.refresh(run)
        raise


def execute_run(
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Wiring di default (DB, config, settings) usato da CLI e API.

    Tiene il lock cross-process per tutta la durata: se un altro processo
    (CLI, API o run schedulato) sta già lavorando alza ``RunLockBusy`` subito,
    invece di scrivere su SQLite in parallelo.
    """
    init_db()
    config = get_config()
    settings = get_settings()
    with run_lock(), get_session() as session:
        run = run_pipeline(session, config, settings, on_progress=on_progress)
        return {
            "run_id": run.id,
            "n_items": run.n_items,
            "n_ideas_processed": run.n_ideas_processed,
            "n_ideas_proposed": run.n_ideas_proposed,
            "n_topics": run.n_topics,
        }


def recluster_topics(
    session: Session,
    config: AppConfig,
    settings: Settings,
    *,
    ollama: OllamaClient | None = None,
    topic_threshold: float | None = None,
) -> dict[str, int]:
    """Ricostruisce SOLO i topic (idee→topic) dagli embedding già salvati.

    Non rifà fetch, embedding né insight: serve a ri-provare ``topic_threshold``
    in pochi secondi e vederne subito l'effetto. Azzera i topic e le loro
    statistiche, riassegna le idee e ri-fotografa i topic per il run più recente.
    L'unica eventuale chiamata LLM è il naming dei topic, se attivo.
    ``topic_threshold`` (se dato) vince sulla soglia di config.yaml: è il
    ``--threshold`` della CLI per provare una taratura al volo.
    """
    ollama = ollama or OllamaClient(settings)

    for stat in session.exec(select(TopicStat)).all():
        session.delete(stat)
    for idea in session.exec(select(Idea)).all():
        idea.topic_id = None
        session.add(idea)
    for topic in session.exec(select(Topic)).all():
        session.delete(topic)
    session.commit()

    effective_threshold = (
        topic_threshold
        if topic_threshold is not None
        else config.clustering.topic_threshold
    )
    assign_ideas_to_topics(
        session,
        effective_threshold,
        namer=_topic_namer(config, ollama),
        label_min_ideas=config.clustering.topic_label_min_ideas,
    )

    last_run = session.exec(select(Run).order_by(Run.id.desc())).first()
    if last_run is not None:
        _record_topic_stats(session, last_run)

    return {
        "n_ideas": len(session.exec(select(Idea)).all()),
        "n_topics": len(session.exec(select(Topic)).all()),
    }


class _IdeaSnapshot(NamedTuple):
    """Ciò che di un'idea deve sopravvivere alla ricostruzione."""

    anchor_item_id: int | None  # l'item che ha creato l'idea (ne dà l'etichetta)
    pinned: bool
    dismissed_at: datetime | None
    seen_at: datetime | None
    note: str | None

    @property
    def has_user_state(self) -> bool:
        return bool(self.pinned or self.dismissed_at or self.seen_at or self.note)


def _snapshot_ideas(
    session: Session,
) -> tuple[list[_IdeaSnapshot], dict[int, IdeaInsight]]:
    """Stato utente per idea + insight LLM per item, prima di cancellare tutto.

    L'insight (summary/why/difficulty) è appeso all'idea, non all'item: qui lo
    si "spalma" su tutti i suoi item così che dopo il rebuild ogni item porti
    con sé il testo già pagato al 7B. Senza questo passaggio la ricostruzione
    perderebbe tutte le analisi LLM di mesi di run.
    """
    snapshots: list[_IdeaSnapshot] = []
    insights: dict[int, IdeaInsight] = {}
    for idea in session.exec(select(Idea)).all():
        items = list(idea.items)
        anchor = next((i for i in items if i.title == idea.label), None) or (
            min(items, key=lambda i: i.id) if items else None
        )
        snapshots.append(
            _IdeaSnapshot(
                anchor_item_id=anchor.id if anchor else None,
                pinned=idea.pinned,
                dismissed_at=idea.dismissed_at,
                seen_at=idea.seen_at,
                note=idea.note,
            )
        )
        insight = _cached_insight(session, idea)
        if insight is not None:
            for item in items:
                insights[item.id] = insight
    return snapshots, insights


def _restore_user_state(session: Session, snapshots: list[_IdeaSnapshot]) -> int:
    """Riporta pin/dismiss/seen/note sull'idea che ha ereditato l'item d'origine.

    Un'idea vecchia può essere stata spezzata in molte: lo stato utente segue
    l'item che le dava il nome, cioè la cosa che l'utente aveva davanti quando
    ha messo il pin. Se due idee vecchie confluiscono nella stessa nuova, gli
    stati si UNISCONO invece di sovrascriversi (niente nota perduta).
    """
    restored = 0
    for snap in snapshots:
        if not snap.has_user_state or snap.anchor_item_id is None:
            continue
        anchor = session.get(Item, snap.anchor_item_id)
        if anchor is None or not anchor.ideas:
            continue
        idea = anchor.ideas[0]
        idea.pinned = idea.pinned or snap.pinned
        idea.dismissed_at = _earliest(idea.dismissed_at, snap.dismissed_at)
        idea.seen_at = _latest(idea.seen_at, snap.seen_at)
        idea.note = _join_notes(idea.note, snap.note)
        session.add(idea)
        restored += 1
    session.commit()
    return restored


def _earliest(a: datetime | None, b: datetime | None) -> datetime | None:
    return min([d for d in (a, b) if d is not None], default=None)


def _latest(a: datetime | None, b: datetime | None) -> datetime | None:
    return max([d for d in (a, b) if d is not None], default=None)


def _join_notes(a: str | None, b: str | None) -> str | None:
    parts = [n.strip() for n in (a, b) if n and n.strip()]
    return "\n\n".join(dict.fromkeys(parts)) or None


def _rescore_ideas(
    session: Session,
    config: AppConfig,
    run: Run,
    insights: dict[int, IdeaInsight],
    on_progress: Callable[[str], None] | None = None,
) -> int:
    """Ri-assegna a ogni idea ricostruita lo score del suo MIGLIORE item.

    Nessuna chiamata a Ollama: il punteggio dipende dall'item e dalla sua storia
    di engagement, e l'unico contributo LLM (``difficulty`` → feasibility) arriva
    dagli insight recuperati prima della cancellazione. Gli score sono scritti
    sul run passato — l'ultimo completato — perché è quello che le viste leggono.
    """
    written = 0
    ideas = session.exec(select(Idea)).all()
    for position, idea in enumerate(ideas, start=1):
        if on_progress is not None and position % 50 == 0:
            on_progress(f"riscoro {position}/{len(ideas)}")
        best: tuple[float, Item, IdeaInsight, ScoreResult] | None = None
        for item in idea.items:
            insight = insights.get(item.id) or heuristic_insight(item)
            observations = session.exec(
                select(ItemStat).where(ItemStat.item_id == item.id)
            ).all()
            result = score_item(item, insight, config, observations=observations)
            if best is None or result.composite > best[0]:
                best = (result.composite, item, insight, result)
        if best is None:
            continue
        _, _, insight, result = best
        idea.summary = insight.summary
        idea.status = result.status
        # Le date vengono dagli item, non da "adesso": altrimenti ogni idea
        # ricostruita sembrerebbe nata oggi e il ciclo di vita ripartirebbe da zero.
        idea.first_seen = min(i.fetched_at for i in idea.items)
        idea.last_seen = max(i.fetched_at for i in idea.items)
        session.add(idea)
        # Lo score per (idea, run) può già esistere: ``rebuild-ideas`` cancella
        # tutto prima, ma ``rescore`` gira su un archivio intatto e lì un INSERT
        # cieco viola la chiave primaria composta. Si aggiorna in luogo.
        score = session.get(Score, (idea.id, run.id))
        if score is None:
            score = Score(idea_id=idea.id, run_id=run.id, composite=0.0, heat=0.0,
                          credibility=0.0, feasibility=0.0, opportunity=0.0, fit=0.0)
        score.heat = result.heat
        score.credibility = result.credibility
        score.feasibility = result.feasibility
        score.opportunity = result.opportunity
        score.fit = result.fit
        score.composite = result.composite
        score.why_text = insight.why_text
        score.difficulty = insight.difficulty
        score.profile = result.profile
        session.add(score)
        written += 1
    session.commit()
    return written


def preview_rebuild_ideas(
    session: Session,
    config: AppConfig,
    *,
    idea_threshold: float | None = None,
    cohesion_floor: float | None = None,
) -> dict:
    """Che idee uscirebbero da un rebuild, SENZA scrivere niente.

    Stessa funzione di raggruppamento del rebuild vero (``group_items_by_similarity``),
    quindi l'anteprima non è una stima: è il risultato.
    """
    threshold = (
        idea_threshold if idea_threshold is not None else config.clustering.idea_threshold
    )
    floor = (
        cohesion_floor
        if cohesion_floor is not None
        else config.clustering.cohesion_floor
    )
    items = _items_in_arrival_order(session)
    vectors = [i.embedding_json for i in items if i.embedding_json]
    groups = group_items_by_similarity(vectors, threshold, floor)
    sizes = sorted((len(g) for g in groups), reverse=True)
    biggest = max(groups, key=len, default=[])
    return {
        "threshold": threshold,
        "cohesion_floor": floor,
        "n_items": len(vectors),
        "n_items_without_embedding": len(items) - len(vectors),
        "n_ideas_now": len(session.exec(select(Idea)).all()),
        "n_ideas": len(groups) + (len(items) - len(vectors)),
        "max_size": sizes[0] if sizes else 0,
        "n_singleton": sum(1 for s in sizes if s == 1),
        "biggest_sample": [items[i].title[:70] for i in biggest[:6]],
    }


def _items_in_arrival_order(session: Session) -> list[Item]:
    """Item nell'ordine in cui la pipeline li ha visti: il clustering è incrementale."""
    return list(session.exec(select(Item).order_by(Item.fetched_at, Item.id)).all())


def _materialize_ideas(
    session: Session, items: list[Item], threshold: float, cohesion_floor: float
) -> None:
    """Crea le idee dai gruppi calcolati in blocco, invece che un item per volta.

    ``attach_item_to_idea`` è pensata per il flusso incrementale di un run (pochi
    item nuovi contro l'archivio) e per ogni item riinterroga tutte le idee: su
    un intero archivio sarebbe un O(n²) di query. ``group_items_by_similarity``
    applica gli stessi criteri con i vettori in memoria — c'è un test che ne
    verifica l'equivalenza — quindi il rebuild passa da minuti a secondi.

    L'etichetta dell'idea resta quella del primo item del gruppo in ordine di
    arrivo: lo stesso che avrebbe "fondato" l'idea nel flusso incrementale.
    """
    embedded = [item for item in items if item.embedding_json]
    groups = group_items_by_similarity(
        [item.embedding_json for item in embedded], threshold, cohesion_floor
    )
    for group in groups:
        members = [embedded[index] for index in group]
        idea = Idea(
            label=members[0].title,
            centroid_json=centroid([m.embedding_json for m in members]),
        )
        idea.items = members
        session.add(idea)
    # Senza embedding non c'è similarità da misurare: un item, un'idea.
    for item in items:
        if item.embedding_json:
            continue
        idea = Idea(label=item.title)
        idea.items = [item]
        session.add(idea)
    session.commit()


def rebuild_ideas(
    session: Session,
    config: AppConfig,
    settings: Settings,
    *,
    ollama: OllamaClient | None = None,
    idea_threshold: float | None = None,
    cohesion_floor: float | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Ri-aggrega da zero gli item già in archivio con le soglie correnti.

    Serve dopo un cambio di criterio di clustering: gli item restano, le idee
    si rifanno. Niente fetch, niente embedding, niente insight nuovi — quindi
    nessuna chiamata a pagamento e nessun 7B da riaspettare (l'unica eventuale
    chiamata locale è il naming dei topic). Si conservano:

    - gli **item** e la loro storia di engagement (``item_stats``), intatti;
    - le **azioni utente** (pin, dismiss, seen, nota), che seguono l'item che
      dava il nome all'idea;
    - gli **insight LLM** già prodotti, riportati sulle idee ricostruite.

    Si rifanno da zero: idee, topic, score e ``topic_stats``. Gli score vengono
    riscritti sull'ultimo run completato, così le viste hanno subito dei numeri
    invece di aspettare il run successivo.
    """
    ollama = ollama or OllamaClient(settings)
    threshold = (
        idea_threshold if idea_threshold is not None else config.clustering.idea_threshold
    )
    floor = (
        cohesion_floor
        if cohesion_floor is not None
        else config.clustering.cohesion_floor
    )

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    snapshots, insights = _snapshot_ideas(session)
    items = _items_in_arrival_order(session)
    report(f"conservo lo stato di {len(snapshots)} idee")

    for model in (Score, TopicStat):
        for row in session.exec(select(model)).all():
            session.delete(row)
    # Cancellando l'idea l'ORM rimuove da sé i suoi link in idea_items: farlo
    # anche a mano lo farebbe inciampare su righe già sparite.
    for idea in session.exec(select(Idea)).all():
        session.delete(idea)
    for topic in session.exec(select(Topic)).all():
        session.delete(topic)
    session.commit()
    for orphan in session.exec(select(IdeaItem)).all():  # residui di run interrotti
        session.delete(orphan)
    session.commit()
    session.expire_all()  # le relazioni in memoria puntano a righe cancellate

    report(f"raggruppo {len(items)} item (decine di secondi, niente rete)")
    _materialize_ideas(session, items, threshold, floor)
    restored = _restore_user_state(session, snapshots)
    report("raggruppo in topic")
    assign_ideas_to_topics(
        session,
        config.clustering.topic_threshold,
        namer=_topic_namer(config, ollama),
        label_min_ideas=config.clustering.topic_label_min_ideas,
        on_progress=on_progress,
    )

    last_run = session.exec(
        select(Run).where(Run.status == RunStatus.DONE).order_by(Run.id.desc())
    ).first()
    n_scored = 0
    if last_run is not None:
        n_scored = _rescore_ideas(
            session, config, last_run, insights, on_progress=on_progress
        )
        _record_topic_stats(session, last_run)

    ideas = session.exec(select(Idea)).all()
    sizes = sorted((len(i.items) for i in ideas), reverse=True)
    return {
        "n_items": len(items),
        "n_ideas_before": len(snapshots),
        "n_ideas": len(ideas),
        "max_size": sizes[0] if sizes else 0,
        "n_singleton": sum(1 for s in sizes if s == 1),
        "n_topics": len(session.exec(select(Topic)).all()),
        "n_scored": n_scored,
        "n_user_state_restored": restored,
        "scored_on_run": last_run.id if last_run else None,
    }


def execute_rebuild_ideas(
    idea_threshold: float | None = None,
    cohesion_floor: float | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Wiring di default per il comando CLI ``rebuild-ideas``.

    Stesso lock dei run: la ricostruzione riscrive idee, topic e score, e farlo
    mentre un run è in corso sarebbe una corsa sugli stessi dati.
    """
    init_db()
    config = get_config()
    settings = get_settings()
    with run_lock(), get_session() as session:
        return rebuild_ideas(
            session,
            config,
            settings,
            idea_threshold=idea_threshold,
            cohesion_floor=cohesion_floor,
            on_progress=on_progress,
        )


def execute_rescore(on_progress: Callable[[str], None] | None = None) -> dict:
    """Ricalcola i punteggi di TUTTE le idee con la configurazione attuale.

    Serve ogni volta che cambia lo scoring — pesi, soglie, floor, o le keyword
    dei profili: un run normale scora solo le idee che hanno ricevuto un item
    nuovo, quindi tutto il resto dell'archivio resta coi numeri vecchi e il
    radar mostra una classifica calcolata con regole che non esistono più.

    Non tocca clustering né topic e non chiama il modello: gli insight già
    prodotti si riusano. È il fratello leggero di ``rebuild-ideas``, che invece
    rifà anche le idee.
    """
    init_db()
    config = get_config()
    with run_lock(), get_session() as session:
        _, insights = _snapshot_ideas(session)
        last_run = session.exec(
            select(Run).where(Run.status == RunStatus.DONE).order_by(Run.id.desc())
        ).first()
        if last_run is None:
            return {"n_scored": 0, "scored_on_run": None, "n_profiled": 0}
        n_scored = _rescore_ideas(
            session, config, last_run, insights, on_progress=on_progress
        )
        _record_topic_stats(session, last_run)
        profiled = session.exec(
            select(Score).where(
                Score.run_id == last_run.id,
                Score.profile.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
        return {
            "n_scored": n_scored,
            "scored_on_run": last_run.id,
            "n_profiled": len(profiled),
        }


def execute_heal(
    embed_missing: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Wiring di default per il comando CLI ``heal``.

    Stesso lock dei run: sposta item tra idee e cancella idee, quindi non può
    convivere con un run. Se qualcosa è stato riparato ricalcola topic e
    punteggi, altrimenti non tocca niente — così un ``heal`` a vuoto costa
    qualche secondo e zero chiamate al modello.
    """
    init_db()
    config = get_config()
    settings = get_settings()
    with run_lock(), get_session() as session:
        summary = heal_ideas(
            session,
            config,
            settings,
            on_progress=on_progress,
            embed_missing=embed_missing,
        )
        if summary["n_merged"] or summary["n_embedded"]:
            if on_progress is not None:
                on_progress("raggruppo in topic")
            assign_ideas_to_topics(
                session,
                config.clustering.topic_threshold,
                namer=_topic_namer(config, OllamaClient(settings)),
                label_min_ideas=config.clustering.topic_label_min_ideas,
                on_progress=on_progress,
            )
            last_run = session.exec(
                select(Run).where(Run.status == RunStatus.DONE).order_by(Run.id.desc())
            ).first()
            if last_run is not None:
                _, insights = _snapshot_ideas(session)
                summary["n_scored"] = _rescore_ideas(
                    session, config, last_run, insights, on_progress=on_progress
                )
                _record_topic_stats(session, last_run)
        summary["n_topics"] = len(session.exec(select(Topic)).all())
        summary["n_ideas"] = len(session.exec(select(Idea)).all())
        return summary


def execute_preview_rebuild(
    idea_threshold: float | None = None,
    cohesion_floor: float | None = None,
) -> dict:
    """Anteprima del rebuild: sola lettura, nessun lock necessario."""
    init_db()
    config = get_config()
    with get_session() as session:
        return preview_rebuild_ideas(
            session,
            config,
            idea_threshold=idea_threshold,
            cohesion_floor=cohesion_floor,
        )


def execute_recluster(threshold_override: float | None = None) -> dict[str, int]:
    """Wiring di default per il comando CLI ``recluster``.

    ``threshold_override`` (il ``--threshold`` della CLI) prova una
    ``topic_threshold`` diversa senza editare config.yaml. Stesso lock dei
    run: riscrive topic e ``TopicStat``, e farlo mentre un run è in corso
    sarebbe una corsa sugli stessi dati.
    """
    init_db()
    config = get_config()
    settings = get_settings()
    with run_lock(), get_session() as session:
        return recluster_topics(
            session, config, settings, topic_threshold=threshold_override
        )
