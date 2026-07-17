"""Pipeline: fonti -> items -> embedding -> idee -> topic -> scores, dentro un Run.

Il ``Run`` viene aggiornato *durante* l'esecuzione (fase, contatori): è ciò che
permette al monitor di mostrare l'avanzamento invece di una barra finta.
"""

import logging
from collections.abc import Callable

from sqlmodel import Session, select

from app.appconfig import AppConfig, get_config
from app.clustering import assign_ideas_to_topics, attach_item_to_idea
from app.config import Settings, get_settings
from app.db import get_session, init_db, upsert_item
from app.embeddings import OllamaEmbedder, embed_item
from app.lifecycle import archive_stale_ideas
from app.llm import IdeaInsight, OllamaClient, generate_insight, heuristic_insight
from app.models import Idea, Item, ItemStat, Run, RunStatus, Score, TopicStat, utcnow
from app.runlock import run_lock
from app.scoring import absolute_engagement, keyword_fit, score_item
from app.sources import Source, create_source

logger = logging.getLogger(__name__)


def _progress(session: Session, run: Run, **fields) -> None:
    """Salva subito lo stato del run così il monitor lo vede in tempo reale."""
    for key, value in fields.items():
        setattr(run, key, value)
    session.add(run)
    session.commit()


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
            # sovrascrive, qui se ne conserva la storia (base della futura
            # heat "a delta" tra osservazioni consecutive).
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

        fetched_total += len(fetched)
        new_total += new_here
        stats[name] = {"fetched": len(fetched), "new": new_here}
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
    """Fotografa i topic in questo run: è la base della vista trend."""
    from app.models import Topic

    topics = session.exec(select(Topic)).all()
    latest = {
        score.idea_id: score
        for score in session.exec(select(Score).where(Score.run_id == run.id)).all()
    }
    counted = 0
    for topic in topics:
        ideas = session.exec(select(Idea).where(Idea.topic_id == topic.id)).all()
        if not ideas:
            continue
        composites = [latest[i.id].composite for i in ideas if i.id in latest]
        session.add(
            TopicStat(
                topic_id=topic.id,
                run_id=run.id,
                n_ideas=len(ideas),
                n_items=sum(len(i.items) for i in ideas),
                avg_composite=(sum(composites) / len(composites)) if composites else 0.0,
            )
        )
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
        embedder = embedder or OllamaEmbedder(settings)

        collected = _collect(session, run, config, settings, sources)
        if on_progress is not None:
            on_progress(f"raccolti {len(collected)} item, genero gli insight…")

        for index, item in enumerate(collected, start=1):
            _progress(
                session, run, phase=f"analisi idee ({index}/{len(collected)})"
            )
            if on_progress is not None:
                on_progress(f"insight {index}/{len(collected)}")
            if item.embedding_json is None:
                vector = embed_item(item, embedder)
                if vector is not None:
                    item.embedding_json = vector
                    session.add(item)
                    session.commit()

            idea = attach_item_to_idea(
                session, item, item.embedding_json, config.clustering.idea_threshold
            )
            cached = _cached_insight(session, idea)
            if cached is not None:
                insight = cached
            elif keyword_fit(item, config.keywords) <= config.scoring.insight_min_fit:
                # Item del tutto fuori tema: niente 7B, basta l'insight euristico.
                insight = heuristic_insight(item)
            else:
                insight = generate_insight(item, settings, ollama=ollama)
            result = score_item(item, insight, config)

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
                )
            )
            session.commit()

        if on_progress is not None:
            on_progress("raggruppo in topic…")
        _progress(session, run, phase="raggruppamento in topic")
        assign_ideas_to_topics(
            session,
            config.clustering.topic_threshold,
            namer=_topic_namer(config, ollama),
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
    from app.models import Topic

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
    )

    last_run = session.exec(select(Run).order_by(Run.id.desc())).first()
    if last_run is not None:
        _record_topic_stats(session, last_run)

    return {
        "n_ideas": len(session.exec(select(Idea)).all()),
        "n_topics": len(session.exec(select(Topic)).all()),
    }


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
