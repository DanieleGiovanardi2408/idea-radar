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
from app.llm import IdeaInsight, OllamaClient, generate_insight
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score, TopicStat, utcnow
from app.scoring import score_item
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

        n_processed = 0
        n_proposed = 0
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
            insight = _cached_insight(session, idea) or generate_insight(
                item, settings, ollama=ollama
            )
            result = score_item(item, insight, config)

            idea.summary = insight.summary
            idea.status = result.status
            idea.last_seen = utcnow()
            session.add(idea)

            existing_score = session.get(Score, (idea.id, run.id))
            if existing_score is not None:
                # Più item nella stessa idea: tiene il punteggio migliore.
                if result.composite <= existing_score.composite:
                    continue
                session.delete(existing_score)
                session.commit()
            else:
                if result.status == IdeaStatus.PROPOSED:
                    n_proposed += 1
                else:
                    n_processed += 1

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

        run.finished_at = utcnow()
        run.status = RunStatus.DONE
        run.phase = "completato"
        run.n_items = len(collected)
        run.n_ideas_processed = n_processed
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
    """Wiring di default (DB, config, settings) usato da CLI e API."""
    init_db()
    config = get_config()
    settings = get_settings()
    with get_session() as session:
        run = run_pipeline(session, config, settings, on_progress=on_progress)
        return {
            "run_id": run.id,
            "n_items": run.n_items,
            "n_ideas_processed": run.n_ideas_processed,
            "n_ideas_proposed": run.n_ideas_proposed,
            "n_topics": run.n_topics,
        }
