"""CLI Typer di Idea Radar."""

from datetime import datetime

import typer
from sqlmodel import select

from app.appconfig import get_config
from app.clustering import dissolve_single_idea_topics, sweep_topic_thresholds
from app.config import get_settings
from app.db import DATA_DIR, get_session, init_db
from app.digest import last_digest_at, render_digest, write_digest
from app.healing import (
    ideas_to_reinsight,
    items_without_embedding,
    regenerate_insights,
)
from app.models import Idea, IdeaStatus, Topic, utcnow
from app.pipeline import (
    execute_heal,
    execute_rescore,
    execute_preview_rebuild,
    execute_rebuild_ideas,
    execute_recluster,
    execute_run,
)
from app.queries import monitor_stats, top_ideas, topic_trends, topics_overview
from app.runlock import RunLockBusy
from app.scheduling import is_fresh, ollama_preflight

app = typer.Typer(help="Idea Radar CLI")


@app.command()
def hello() -> None:
    """Comando placeholder: verifica che la CLI funzioni."""
    typer.echo("Idea Radar CLI — pronto.")


def _stamp() -> str:
    """Timestamp per le righe destinate al log del LaunchAgent."""
    return f"[{utcnow():%Y-%m-%d %H:%M}]"


def _show(msg: str) -> None:
    """Riscrive sempre la stessa riga: avanzamento senza allagare il terminale."""
    typer.echo(f"\r  {msg:<48}", nl=False)


def _scheduled_run() -> None:
    """Percorso non presidiato: guardie prima, poi pipeline senza progress.

    Ogni riga stampata finisce nel log del LaunchAgent, quindi: sintetica e
    con timestamp. Exit code parlanti per `schedule status`: 0 = lavoro fatto
    o salto legittimo, 1 = run fallito, 3 = Ollama non pronto.
    """
    init_db()
    config = get_config()
    settings = get_settings()
    with get_session() as session:
        fresh, why = is_fresh(session, config.scheduling.min_interval_hours)
    if fresh:
        typer.echo(f"{_stamp()} salto: {why}.")
        return
    if config.scheduling.require_ollama:
        ready, why = ollama_preflight(settings)
        if not ready:
            typer.echo(f"{_stamp()} salto: {why}.")
            raise typer.Exit(3)
    try:
        summary = execute_run(on_progress=None)
    except RunLockBusy:
        typer.echo(f"{_stamp()} salto: un altro run è già in corso.")
        return
    except Exception as exc:  # il traceback completo arriva nel log via logging
        typer.echo(f"{_stamp()} run fallito: {exc}")
        raise typer.Exit(1)
    typer.echo(
        f"{_stamp()} run #{summary['run_id']} completato — "
        f"{summary['n_items']} items, {summary['n_ideas_proposed']} proposed, "
        f"{summary['n_topics']} topic."
    )


@app.command()
def run(
    scheduled: bool = typer.Option(
        False,
        "--scheduled",
        help=(
            "Modalità non presidiata (usata dal LaunchAgent): salta se l'ultimo "
            "run è fresco o se Ollama non è pronto, nessuna riga di avanzamento."
        ),
    ),
) -> None:
    """Esegue la pipeline: raccolta, embedding, clustering e scoring."""
    if scheduled:
        _scheduled_run()
        return

    typer.echo("Avvio pipeline…")

    try:
        summary = execute_run(on_progress=_show)
    except RunLockBusy:
        typer.echo(
            "Un run è già in corso (altro terminale, API o scheduler). "
            "Riprova tra poco."
        )
        raise typer.Exit(1)
    typer.echo("")  # chiude la riga di avanzamento
    typer.echo(
        f"Run #{summary['run_id']} completato — "
        f"{summary['n_items']} items, "
        f"{summary['n_ideas_proposed']} proposed, "
        f"{summary['n_ideas_processed']} processed, "
        f"{summary['n_topics']} topic."
    )


@app.command()
def digest(
    stdout: bool = typer.Option(
        False, "--stdout", help="Stampa il digest invece di salvarlo su file."
    ),
    since: str = typer.Option(
        None,
        "--since",
        help="Finestra da questa data (ISO, es. 2026-07-20). Default: ultimo digest.",
    ),
    limit: int = typer.Option(10, help="Quante idee al massimo nel digest."),
) -> None:
    """Report markdown di cosa è emerso da quando l'hai guardato l'ultima volta.

    Le "nuove" sono le idee il cui PRIMO punteggio sopra soglia cade nella
    finestra — non quelle viste per la prima volta: un'idea può essere in
    archivio da settimane e salire adesso, ed è quella la notizia. La finestra
    parte dall'ultimo digest scritto in `data/digests/`.
    """
    init_db()
    config = get_config()

    cutoff = None
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError:
            typer.echo(f"Data non valida: {since!r}. Usa il formato ISO, es. 2026-07-20.")
            raise typer.Exit(2)
    else:
        cutoff = last_digest_at(DATA_DIR)

    with get_session() as session:
        content = render_digest(session, config, since=cutoff, max_ideas=limit)

    if stdout:
        typer.echo(content)
        return

    path = write_digest(DATA_DIR, content)
    # Una riga di riepilogo: il file lo si apre solo se c'è qualcosa dentro.
    headline = next(
        (line[4:] for line in content.splitlines() if line.startswith("### ")), None
    )
    typer.echo(f"Digest scritto in {path}")
    if headline:
        typer.echo(f"  in cima: {headline}")
    else:
        typer.echo("  nessuna idea nuova sopra soglia in questa finestra.")


@app.command()
def rescore() -> None:
    """Ricalcola i punteggi di tutte le idee con la configurazione attuale.

    Da lanciare dopo aver cambiato pesi, soglie o le keyword dei profili: un run
    normale scora solo le idee che hanno ricevuto un item nuovo, quindi il resto
    dell'archivio resterebbe con una classifica calcolata su regole che non
    esistono più. Non tocca idee e topic e non chiama il modello.
    """
    typer.echo("Ricalcolo dei punteggi…")
    try:
        summary = execute_rescore(on_progress=_show)
    except RunLockBusy:
        typer.echo("Un run è in corso: riprova a run finito.")
        raise typer.Exit(1)
    typer.echo("")
    if not summary["n_scored"]:
        typer.echo("Nessun run completato in archivio: serve prima un run.")
        return
    typer.echo(
        f"Fatto — {summary['n_scored']} idee riscorate sul run "
        f"#{summary['scored_on_run']}, {summary['n_profiled']} con un tema."
    )


@app.command()
def heal(
    skip_embeddings: bool = typer.Option(
        False,
        "--skip-embeddings",
        help="Non chiamare Ollama: ripassa solo i singleton già vettorizzati.",
    ),
) -> None:
    """Ripara i singleton lasciati dai run degradati.

    Due sedimenti che il flusso normale non recupera: gli item entrati con
    Ollama giù (senza embedding non sono aggregabili, e restano tali per sempre)
    e le idee da un solo item che oggi avrebbero un posto — il legame singolo
    dipende dall'ordine di arrivo. Non tocca le idee con più item.
    """
    init_db()
    settings = get_settings()

    embed_missing = not skip_embeddings
    if embed_missing:
        with get_session() as session:
            pending = len(items_without_embedding(session))
        if pending:
            ready, why = ollama_preflight(settings)
            if not ready:
                typer.echo(
                    f"{pending} item senza embedding, ma {why}. "
                    "Ripasso solo i singleton già vettorizzati."
                )
                embed_missing = False
            else:
                typer.echo(f"{pending} item senza embedding: li rifaccio.")

    typer.echo("Riparazione in corso…")
    try:
        summary = execute_heal(embed_missing=embed_missing, on_progress=_show)
    except RunLockBusy:
        typer.echo(
            "Un run è in corso e la riparazione toccherebbe le stesse idee. "
            "Riprova a run finito."
        )
        raise typer.Exit(1)
    typer.echo("")  # chiude la riga di avanzamento

    if not summary["n_merged"] and not summary["n_embedded"]:
        typer.echo(
            f"Niente da riparare — {summary['n_singleton_checked']} idee da un "
            "solo item ripassate, nessuna ha un posto migliore."
        )
    else:
        typer.echo(
            f"Fatto — {summary['n_embedded']} embedding rifatti, "
            f"{summary['n_merged']} singleton riassorbiti "
            f"({summary['n_ideas']} idee, {summary['n_topics']} topic)."
        )
    if summary["n_without_embedding_left"]:
        typer.echo(
            f"  {summary['n_without_embedding_left']} item restano senza "
            "embedding: riprova con Ollama attivo."
        )


@app.command()
def reinsight(
    all_ideas: bool = typer.Option(
        False,
        "--all",
        help="Tutte le idee vive, non solo quelle sopra soglia (lungo: ore).",
    ),
    limit: int = typer.Option(0, help="Fermati dopo N idee (0 = nessun tetto)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Mostra quali idee e quanto ci vuole, senza rifare."
    ),
) -> None:
    """Rigenera i riassunti, dalle idee più in vista alle meno.

    Serve perché l'insight LLM vive sull'idea, non sull'item: quando un'idea era
    una calamita da centinaia di item il riassunto descriveva solo il migliore, e
    il `rebuild-ideas` l'ha spalmato su tutte le idee nate da lì.

    Non c'è un filtro "trova quelli sbagliati" perché non funziona: riconoscerli
    dalle parole in comune misura la lingua (insight in italiano, item in
    inglese), e confrontare gli embedding non distingue "stesso dominio, oggetto
    diverso" — che è esattamente il caso. Quindi si rigenera per priorità: di
    default solo le idee sopra soglia, quelle che finiscono nel digest e in cima
    al radar. `--all` copre tutto, ma mettiti l'anima in pace.
    """
    init_db()
    settings = get_settings()
    ready, why = ollama_preflight(settings)
    if not ready:
        typer.echo(f"Serve Ollama per questo comando: {why}")
        raise typer.Exit(3)

    with get_session() as session:
        targets = ideas_to_reinsight(
            session, only_proposed=not all_ideas, limit=limit
        )
        if not targets:
            typer.echo("Nessuna idea da rigenerare.")
            return

        # Una stima onesta: il 7B locale sta sui pochi secondi per idea.
        minutes = len(targets) * 4 / 60
        scope = "tutte le idee vive" if all_ideas else "idee sopra soglia"
        typer.echo(
            f"{len(targets)} idee da rigenerare ({scope}) — "
            f"circa {minutes:.0f} minuti di 7B locale."
        )
        for idea in targets[:5]:
            typer.echo(f"  · {idea.label[:64]}")
            typer.echo(f"    ora: {(idea.summary or '(vuoto)')[:70]}")
        if dry_run:
            typer.echo("Anteprima senza scritture.")
            return

        typer.confirm("Procedo?", abort=True)
        done = regenerate_insights(session, settings, targets, on_progress=_show)
    typer.echo("")
    typer.echo(f"Fatto — {done} riassunti rigenerati.")


@app.command(name="rebuild-ideas")
def rebuild_ideas_command(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Solo anteprima: mostra che idee uscirebbero, senza scrivere nulla.",
    ),
    threshold: float = typer.Option(
        None,
        "--threshold",
        help="Usa questa idea_threshold al posto di quella in config.yaml.",
    ),
    cohesion: float = typer.Option(
        None,
        "--cohesion",
        help="Usa questo cohesion_floor al posto di quello in config.yaml (0 = disattivato).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma."),
) -> None:
    """Ri-aggrega gli item già in archivio: rifà le idee, non il run.

    Da usare dopo un cambio delle soglie di clustering, per applicarle allo
    storico invece di aspettare che si riformi da solo. Item e storia
    dell'engagement restano intatti; pin, dismiss, note e insight LLM già
    prodotti vengono trasferiti alle idee ricostruite. Prima `--dry-run`.
    """
    preview = execute_preview_rebuild(idea_threshold=threshold, cohesion_floor=cohesion)
    if preview["n_items"] == 0:
        typer.echo("Nessun item con embedding in archivio: serve prima un run.")
        raise typer.Exit()

    typer.echo(
        f"Soglie: idea_threshold {preview['threshold']:.2f} · "
        f"cohesion_floor {preview['cohesion_floor']:.2f}"
    )
    typer.echo(
        f"{preview['n_items']} item → {preview['n_ideas']} idee "
        f"(ora sono {preview['n_ideas_now']}) · "
        f"la più grossa {preview['max_size']} item · "
        f"{preview['n_singleton']} singleton"
    )
    for title in preview["biggest_sample"]:
        typer.echo(f"        · {title}")
    if preview["n_items_without_embedding"]:
        typer.echo(
            f"  ({preview['n_items_without_embedding']} item senza embedding "
            "resteranno idee a sé)"
        )

    if dry_run:
        typer.echo("Anteprima senza scritture: rilancia senza --dry-run per applicare.")
        return

    if not yes:
        typer.confirm(
            "Idee, topic e score verranno ricostruiti (item, engagement, pin e "
            "note restano). Procedo?",
            abort=True,
        )

    try:
        summary = execute_rebuild_ideas(
            idea_threshold=threshold, cohesion_floor=cohesion, on_progress=_show
        )
    except RunLockBusy:
        typer.echo(
            "Un run è in corso e la ricostruzione toccherebbe gli stessi dati. "
            "Riprova a run finito."
        )
        raise typer.Exit(1)
    typer.echo("")  # chiude la riga di avanzamento
    typer.echo(
        f"Fatto — {summary['n_ideas_before']} idee → {summary['n_ideas']} "
        f"({summary['n_topics']} topic, {summary['n_scored']} riscorate sul run "
        f"#{summary['scored_on_run']}); la più grossa {summary['max_size']} item, "
        f"{summary['n_user_state_restored']} azioni utente trasferite."
    )


@app.command(name="prune-topics")
def prune_topics_command(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Dice quanti ne scioglierebbe, senza scrivere."
    ),
) -> None:
    """Scioglie i topic che contengono una sola idea.

    Manutenzione da fare una volta, per l'archivio nato con la regola vecchia:
    ogni idea che non trovava compagni si apriva un tema col proprio titolo, e
    così su 1002 topic 784 avevano un solo membro — un numero che non descriveva
    niente. Ora un tema vuole almeno due idee, quindi il problema non si
    riforma; questo comando ripulisce quello che c'è già.

    Le idee tornano "non raggruppate" e restano intatte: al prossimo run possono
    accoppiarsi tra loro. Con `--dry-run` si vede il conto prima di toccare.
    """
    init_db()
    with get_session() as session:
        if dry_run:
            per_topic: dict[int, int] = {}
            for idea in session.exec(select(Idea)).all():
                if idea.topic_id is not None:
                    per_topic[idea.topic_id] = per_topic.get(idea.topic_id, 0) + 1
            soli = [t for t, n in per_topic.items() if n == 1]
            totale = len(session.exec(select(Topic)).all())
            typer.echo(
                f"{len(soli)} topic su {totale} hanno una sola idea: "
                f"resterebbero {totale - len(soli)} temi veri."
            )
            typer.echo("Nessuna scrittura (--dry-run).")
            return

        summary = dissolve_single_idea_topics(session)

    if not summary["n_dissolved"]:
        typer.echo("Niente da sciogliere: nessun topic con una sola idea.")
        return
    typer.echo(
        f"Fatto — {summary['n_dissolved']} topic sciolti, "
        f"{summary['n_ideas_freed']} idee tornate non raggruppate, "
        f"{summary['n_stats_removed']} fotografie di trend rimosse. "
        f"Restano {summary['n_topics_left']} temi."
    )


@app.command()
def recluster(
    threshold: float = typer.Option(
        None,
        "--threshold",
        help="Usa questa topic_threshold al posto di quella in config.yaml (scrive davvero).",
    ),
    sweep: str = typer.Option(
        None,
        "--sweep",
        help=(
            "Solo anteprima, nessuna scrittura: confronta più soglie separate "
            "da virgola (es. 0.62,0.68,0.74) sugli embedding salvati."
        ),
    ),
) -> None:
    """Ri-raggruppa le idee in topic dagli embedding salvati, senza rifare il run.

    Per tarare `topic_threshold`: prima `--sweep` per vedere l'effetto di più
    soglie in un colpo solo, poi la soglia scelta va in config.yaml (o si
    prova al volo con `--threshold`).
    """
    if sweep:
        try:
            values = [float(v) for v in sweep.split(",") if v.strip()]
        except ValueError:
            values = []
        if not values:
            typer.echo(
                "Formato --sweep non valido: numeri separati da virgola, "
                "es. 0.62,0.68,0.74"
            )
            raise typer.Exit(2)
        init_db()
        with get_session() as session:
            rows = sweep_topic_thresholds(session, values)
        if all(r["n_topics"] == 0 for r in rows):
            typer.echo("Nessuna idea con embedding in archivio: serve prima un run.")
            raise typer.Exit()
        for r in rows:
            typer.echo(
                f"{r['threshold']:.2f} → {r['n_topics']:>3} topic · "
                f"il più grosso {r['max_size']} idee · {r['n_singleton']} singleton"
            )
            for label in r["biggest_sample"]:
                typer.echo(f"        · {label}")
        typer.echo(
            "Anteprima senza scritture: scegli la soglia, mettila in "
            "config.yaml (o usa --threshold) e rilancia recluster."
        )
        return

    typer.echo("Ricostruzione dei topic dagli embedding salvati…")
    try:
        summary = execute_recluster(threshold_override=threshold)
    except RunLockBusy:
        typer.echo(
            "Un run è in corso e il recluster toccherebbe gli stessi topic. "
            "Riprova a run finito."
        )
        raise typer.Exit(1)
    typer.echo(f"Fatto — {summary['n_ideas']} idee raggruppate in {summary['n_topics']} topic.")


@app.command()
def ideas(
    limit: int = typer.Option(10, help="Numero massimo di idee da mostrare."),
    proposed: bool = typer.Option(
        False, "--proposed", help="Mostra solo le idee sopra soglia (proposed)."
    ),
) -> None:
    """Elenca le migliori idee per composite score."""
    init_db()
    status = IdeaStatus.PROPOSED if proposed else None
    with get_session() as session:
        rows = top_ideas(session, limit=limit, status=status)
        if not rows:
            typer.echo("Nessuna idea in archivio. Lancia prima `idea-radar run`.")
            raise typer.Exit()
        for idea, score in rows:
            composite = f"{score.composite:.2f}" if score else "—"
            topic = f" · {idea.topic.label}" if idea.topic else ""
            typer.echo(f"[{composite}] {idea.label}  ({idea.status.value}{topic})")
            if score and score.why_text:
                typer.echo(f"    {score.why_text}")


@app.command()
def topics() -> None:
    """Elenca i topic con quante idee contengono."""
    init_db()
    with get_session() as session:
        rows = topics_overview(session)
        if not rows:
            typer.echo("Nessun topic. Serve un run con gli embedding attivi.")
            raise typer.Exit()
        for t in rows:
            typer.echo(
                f"[{t['top_composite']:.2f}] {t['label']} — "
                f"{t['n_ideas']} idee, {t['n_proposed']} proposed"
            )


@app.command()
def trends() -> None:
    """Mostra quali topic stanno crescendo tra un run e l'altro."""
    init_db()
    with get_session() as session:
        rows = topic_trends(session)
        if not rows:
            typer.echo("Nessun trend. Servono almeno due run.")
            raise typer.Exit()
        for t in rows:
            arrow = "↑" if t["delta_ideas"] > 0 else ("↓" if t["delta_ideas"] < 0 else "=")
            typer.echo(
                f"{arrow} {t['label']} — {t['n_ideas']} idee "
                f"({t['delta_ideas']:+d}), composite {t['avg_composite']:.2f}"
            )


@app.command()
def stats() -> None:
    """Riepilogo dell'imbuto di ingestione."""
    init_db()
    with get_session() as session:
        data = monitor_stats(session)
        typer.echo(
            f"{data['n_items']} items → {data['n_ideas']} idee "
            f"({data['n_proposed']} proposed, {data['n_archived']} archiviate) "
            f"in {data['n_topics']} topic, su {data['n_runs']} run."
        )
        for source, count in sorted(data["items_by_source"].items()):
            typer.echo(f"  {source}: {count} items")


schedule_app = typer.Typer(help="Run automatici su macOS via launchd.")
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("install")
def schedule_install() -> None:
    """Installa (o reinstalla) il LaunchAgent dei run automatici.

    Il trigger spara al login e ogni mezz'ora; la cadenza vera dei run è
    `scheduling.min_interval_hours` in config.yaml — si cambia lì, senza
    reinstallare. Se sposti il repo (o uv), rilancia questo comando: nel
    plist finiscono path assoluti.
    """
    from app import schedule_launchd

    try:
        path = schedule_launchd.install()
    except RuntimeError as exc:
        typer.echo(f"Errore: {exc}")
        raise typer.Exit(1)
    config = get_config()
    typer.echo(f"LaunchAgent installato e caricato: {path}")
    typer.echo(
        f"Tick al login e ogni {schedule_launchd.FIRE_INTERVAL_SECONDS // 60} min; "
        f"run effettivo se l'ultimo completato ha più di "
        f"{config.scheduling.min_interval_hours:g} ore."
    )
    typer.echo(f"Log: {schedule_launchd.LOG_PATH}")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Scarica e rimuove il LaunchAgent."""
    from app import schedule_launchd

    try:
        removed = schedule_launchd.uninstall()
    except RuntimeError as exc:
        typer.echo(f"Errore: {exc}")
        raise typer.Exit(1)
    typer.echo("LaunchAgent rimosso." if removed else "Nessun LaunchAgent da rimuovere.")


@schedule_app.command("status")
def schedule_status() -> None:
    """Il radar sta girando da solo? Agent, ultimo exit code, ultimi run."""
    from app import schedule_launchd

    info = schedule_launchd.status()
    typer.echo(
        f"Plist: {info['plist']} ({'presente' if info['installed'] else 'assente'})"
    )
    typer.echo(f"Caricato in launchd: {'sì' if info['loaded'] else 'no'}")
    if info["last_exit_code"] is not None:
        code = str(info["last_exit_code"])
        if "never exited" in code:
            # launchctl la riporta quando il processo del tick non è mai
            # uscito dall'ultimo load: quasi sempre significa che sta girando.
            meaning = "nessun tick ancora concluso: probabilmente sta girando adesso"
        else:
            meaning = {
                "0": "ok, o salto legittimo",
                "1": "run fallito",
                "3": "Ollama non pronto",
            }.get(code, "codice inatteso")
        typer.echo(f"Ultimo exit code: {code} ({meaning})")
    typer.echo(f"Log: {schedule_launchd.LOG_PATH}")

    init_db()
    with get_session() as session:
        recent = monitor_stats(session)["recent_runs"][-5:]
    if not recent:
        typer.echo("Nessun run in archivio.")
        return
    typer.echo("Ultimi run:")
    for r in recent:
        finished = f"{r.finished_at:%H:%M}" if r.finished_at else "…"
        typer.echo(
            f"  #{r.id} {r.started_at:%Y-%m-%d %H:%M}→{finished} "
            f"{r.status.value} — {r.phase}"
        )


if __name__ == "__main__":
    app()
