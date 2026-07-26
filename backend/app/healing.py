"""Riparazione dei singleton lasciati dai run degradati.

Il clustering è incrementale e irreversibile: un item assegnato a un'idea non
viene più rivalutato. È la scelta giusta nel flusso normale — costa poco e i
doppioni si trovano al momento giusto — ma lascia due sedimenti:

1. **Item senza embedding.** Se Ollama era giù quando l'item è arrivato, non
   c'era nessun vettore da confrontare e l'item è diventato un'idea a sé. Senza
   embedding resterà tale *per sempre*, anche quando Ollama torna: non c'è
   niente da misurare. Sono 9 in questo archivio, entrati da un run degradato.
2. **Singleton che oggi avrebbero un posto.** Il legame singolo dipende
   dall'ordine di arrivo: un item che non trovava nessuna idea abbastanza
   vicina ne ha aperta una propria, e se l'idea "giusta" è nata dopo — o è nata
   con i vettori mancanti — nessuno torna a rimetterli insieme.

``heal_ideas`` ripassa entrambi i casi: rifà gli embedding che mancano (l'unica
parte che tocca Ollama) e riprova ad assegnare le idee da un solo item con lo
stesso criterio del flusso normale (``clustering.best_idea_for``, così le due
strade non possono divergere). Non tocca le idee con più item: quelle un posto
l'hanno già trovato.

C'è un terzo sedimento, di natura diversa: **riassunti che parlano d'altro**.
L'insight LLM vive sull'idea, non sull'item, e quando un'idea era una calamita
da centinaia di item il suo riassunto descriveva soltanto il migliore di quelli.
Il ``rebuild-ideas`` ha spalmato quel testo su tutte le idee nate da quella
calamita — era il modo di non buttare mesi di lavoro del modello, ma su una
minoranza di idee il risultato è un riassunto che non c'entra niente. Non si
riesce a *riconoscerle* (vedi ``ideas_to_reinsight``), quindi
``regenerate_insights`` le rifà per priorità: è l'unica parte che chiede al 7B
di lavorare.
"""

import logging
from collections.abc import Callable

from sqlalchemy import JSON, or_
from sqlmodel import Session, select

from app.appconfig import AppConfig
from app.clustering import IdeaIndex, _refresh_centroid, best_idea_for
from app.config import Settings
from app.embeddings import OllamaEmbedder, embed_item
from app.llm import OllamaClient, generate_insight
from app.models import Idea, IdeaStatus, Item, Score, utcnow
from app.queries import latest_scores

logger = logging.getLogger(__name__)


def items_without_embedding(session: Session) -> list[Item]:
    """Item entrati da run degradati: senza vettore non sono aggregabili.

    Attenzione al confronto: su una colonna JSON un ``None`` di Python viene
    salvato come *null JSON*, non come NULL SQL, quindi ``is_(None)`` non trova
    niente. Servono entrambe le forme — la seconda per le righe scritte così,
    la prima per un eventuale NULL vero.
    """
    return list(
        session.exec(
            select(Item).where(
                or_(Item.embedding_json.is_(None), Item.embedding_json == JSON.NULL)
            )
        ).all()
    )


def singleton_ideas(session: Session) -> list[Idea]:
    """Idee da un solo item, con un centroide: le uniche da riconsiderare."""
    return [
        idea
        for idea in session.exec(select(Idea)).all()
        if idea.centroid_json and len(idea.items) == 1
    ]


def _merge_user_state(target: Idea, source: Idea) -> None:
    """Le azioni dell'utente seguono l'item, non l'idea che viene dissolta."""
    target.pinned = target.pinned or source.pinned
    dismissals = [d for d in (target.dismissed_at, source.dismissed_at) if d]
    target.dismissed_at = min(dismissals) if dismissals else None
    seen = [s for s in (target.seen_at, source.seen_at) if s]
    target.seen_at = max(seen) if seen else None
    notes = [n.strip() for n in (target.note, source.note) if n and n.strip()]
    target.note = "\n\n".join(dict.fromkeys(notes)) or None


def ideas_to_reinsight(
    session: Session,
    *,
    only_proposed: bool = True,
    limit: int = 0,
) -> list[Idea]:
    """Idee di cui rifare l'insight, dalla più in vista alla meno.

    Non c'è modo di *riconoscere* un riassunto ereditato sbagliato, e ci ho
    provato due volte: contando le parole in comune coi propri item (misurava la
    lingua — gli insight sono in italiano, gli item in inglese) e confrontando
    gli embedding (non distingue "stesso dominio, oggetto diverso", ed è proprio
    quello il caso: la calamita era piena di roba AI/dev-tools e le idee nate da
    lei parlano anche loro di AI/dev-tools). Sull'archivio reale il secondo
    segnalava 19 idee di cui la maggior parte con riassunti giusti, e mancava i
    due casi rotti visti nel digest.

    Quindi si smette di indovinare e si sceglie per **priorità**: prima le idee
    sopra soglia, che sono quelle che finiscono nel digest e in cima al radar.
    Rigenerare è deterministico e sempre corretto — costa solo tempo di 7B
    locale, quindi il vero parametro è quanto ne vuoi spendere.
    """
    query = select(Idea).where(Idea.status != IdeaStatus.ARCHIVED)
    if only_proposed:
        query = query.where(Idea.status == IdeaStatus.PROPOSED)
    ideas = [idea for idea in session.exec(query).all() if idea.items]
    latest = latest_scores(session)
    ideas.sort(key=lambda i: latest[i.id].composite if i.id in latest else 0.0, reverse=True)
    return ideas[:limit] if limit > 0 else ideas


def _anchor_item(idea: Idea) -> Item | None:
    """L'item che dà il nome all'idea: quello da cui rigenerare l'insight."""
    if not idea.items:
        return None
    return next(
        (item for item in idea.items if item.title == idea.label),
        min(idea.items, key=lambda i: i.id or 0),
    )


def regenerate_insights(
    session: Session,
    settings: Settings,
    ideas: list[Idea],
    *,
    ollama: OllamaClient | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> int:
    """Rigenera riassunto e "perché conta" per le idee passate.

    Aggiorna anche l'ultimo ``Score``, dove vivono ``why_text`` e ``difficulty``:
    lasciarlo indietro significherebbe un riassunto nuovo con la vecchia
    motivazione accanto, che è peggio del problema di partenza.
    """
    ollama = ollama or OllamaClient(settings)
    done = 0
    for position, idea in enumerate(ideas, start=1):
        if on_progress is not None:
            on_progress(f"riassunti {position}/{len(ideas)}")
        item = _anchor_item(idea)
        if item is None:
            continue
        try:
            insight = generate_insight(item, settings, ollama=ollama)
        except Exception as exc:  # un'idea fallita non ferma le altre
            logger.warning("Insight non rigenerato per l'idea %s: %s", idea.id, exc)
            continue
        idea.summary = insight.summary
        session.add(idea)
        last = session.exec(
            select(Score)
            .where(Score.idea_id == idea.id)
            .order_by(Score.run_id.desc())
        ).first()
        if last is not None:
            last.why_text = insight.why_text
            last.difficulty = insight.difficulty
            session.add(last)
        session.commit()
        done += 1
    return done


def _pick_survivor(idea: Idea, target: Idea) -> tuple[Idea, Idea]:
    """Chi delle due idee sopravvive alla fusione, e chi viene assorbita.

    Un'idea con più item non si dissolve mai: ha già un'identità (etichetta,
    riassunto pagato al modello, storia) costruita su più segnali. Tra due
    singleton vince la più VECCHIA: la seconda è il doppione della prima, non il
    contrario. Senza questa regola l'esito dipenderebbe dall'ordine con cui il
    ciclo incontra le idee, e un item riparato poteva far sparire l'idea sana
    che lo stava aspettando.
    """
    if len(target.items) > 1:
        return target, idea
    if len(idea.items) > 1:
        return idea, target
    if (target.first_seen, target.id or 0) <= (idea.first_seen, idea.id or 0):
        return target, idea
    return idea, target


def _absorb(
    session: Session, survivor: Idea, absorbed: Idea, index: IdeaIndex
) -> None:
    """Sposta gli item di ``absorbed`` in ``survivor`` e cancella la prima."""
    for item in list(absorbed.items):
        survivor.items.append(item)
    survivor.first_seen = min(survivor.first_seen, absorbed.first_seen)
    survivor.last_seen = max(survivor.last_seen, absorbed.last_seen)
    _merge_user_state(survivor, absorbed)
    session.add(survivor)
    # I punteggi sono per (idea, run): quelli dell'idea che sparisce non devono
    # restare appesi a una riga cancellata.
    for score in session.exec(
        select(Score).where(Score.idea_id == absorbed.id)
    ).all():
        session.delete(score)
    absorbed_id = absorbed.id
    absorbed.items = []
    session.delete(absorbed)
    session.commit()
    index.forget(absorbed_id)
    _refresh_centroid(session, survivor)
    index.remember(survivor)


def heal_ideas(
    session: Session,
    config: AppConfig,
    settings: Settings,
    *,
    embedder: OllamaEmbedder | None = None,
    on_progress: Callable[[str], None] | None = None,
    embed_missing: bool = True,
) -> dict:
    """Rifà gli embedding mancanti e ri-aggrega i singleton che ora hanno un posto.

    Restituisce il conto di cosa è stato riparato. Non ricalcola i punteggi e non
    tocca i topic: chi chiama decide se rifarli (la CLI lo fa solo se qualcosa è
    cambiato davvero, per non pagare il naming dei topic per niente).

    ``embed_missing=False`` salta la parte che richiede Ollama: utile quando il
    preflight dice che non è pronto, o per ripassare i soli singleton.
    """

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    embedded = 0
    if embed_missing:
        missing = items_without_embedding(session)
        if missing:
            embedder = embedder or OllamaEmbedder(settings)
            for position, item in enumerate(missing, start=1):
                report(f"embedding mancanti {position}/{len(missing)}")
                vector = embed_item(item, embedder)
                if vector is None:
                    # L'embedder si arrende dopo N errori di fila: inutile
                    # continuare a chiedere per ogni item della coda.
                    break
                item.embedding_json = vector
                session.add(item)
                # Un'idea nata senza vettore ora ne ha uno: da qui in poi è
                # confrontabile come tutte le altre.
                for idea in item.ideas:
                    if len(idea.items) == 1:
                        idea.centroid_json = vector
                        session.add(idea)
                embedded += 1
            session.commit()

    candidates = singleton_ideas(session)
    report(f"ripasso {len(candidates)} idee da un solo item")
    index = IdeaIndex(session)
    merged = 0
    for position, idea in enumerate(candidates, start=1):
        if position % 50 == 0:
            report(f"ripasso singleton {position}/{len(candidates)}")
        if not idea.items:  # dissolta da un passaggio precedente
            continue
        item = idea.items[0]
        if item.embedding_json is None:
            continue
        target = best_idea_for(
            session,
            item.embedding_json,
            config.clustering.idea_threshold,
            cohesion_floor=config.clustering.cohesion_floor,
            index=index,
            exclude={idea.id},
        )
        if target is None:
            continue

        survivor, absorbed = _pick_survivor(idea, target)
        _absorb(session, survivor, absorbed, index)
        merged += 1

    return {
        "n_embedded": embedded,
        "n_singleton_checked": len(candidates),
        "n_merged": merged,
        "n_without_embedding_left": len(items_without_embedding(session)),
        "healed_at": utcnow(),
    }
