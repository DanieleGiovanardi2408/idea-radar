"""CLI Typer di Idea Radar."""

import typer

from app.appconfig import get_config
from app.clustering import sweep_topic_thresholds
from app.config import get_settings
from app.db import get_session, init_db
from app.models import IdeaStatus, utcnow
from app.pipeline import execute_recluster, execute_run
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

    def _show(msg: str) -> None:
        # Riscrive sempre la stessa riga: avanzamento senza allagare il terminale.
        typer.echo(f"\r  {msg:<48}", nl=False)

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
