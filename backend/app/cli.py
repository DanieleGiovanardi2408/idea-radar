"""CLI Typer di Idea Radar."""

import typer

from app.db import get_session, init_db
from app.models import IdeaStatus
from app.pipeline import execute_recluster, execute_run
from app.queries import monitor_stats, top_ideas, topic_trends, topics_overview

app = typer.Typer(help="Idea Radar CLI")


@app.command()
def hello() -> None:
    """Comando placeholder: verifica che la CLI funzioni."""
    typer.echo("Idea Radar CLI — pronto.")


@app.command()
def run() -> None:
    """Esegue la pipeline: raccolta, embedding, clustering e scoring."""
    typer.echo("Avvio pipeline…")

    def _show(msg: str) -> None:
        # Riscrive sempre la stessa riga: avanzamento senza allagare il terminale.
        typer.echo(f"\r  {msg:<48}", nl=False)

    summary = execute_run(on_progress=_show)
    typer.echo("")  # chiude la riga di avanzamento
    typer.echo(
        f"Run #{summary['run_id']} completato — "
        f"{summary['n_items']} items, "
        f"{summary['n_ideas_proposed']} proposed, "
        f"{summary['n_ideas_processed']} processed, "
        f"{summary['n_topics']} topic."
    )


@app.command()
def recluster() -> None:
    """Ri-raggruppa le idee in topic dagli embedding salvati, senza rifare il run.

    Utile per provare in fretta `topic_threshold` in config.yaml e vedere subito
    l'effetto: niente re-fetch, niente embedding, niente insight LLM per item.
    """
    typer.echo("Ricostruzione dei topic dagli embedding salvati…")
    summary = execute_recluster()
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
            f"({data['n_proposed']} proposed) in {data['n_topics']} topic, "
            f"su {data['n_runs']} run."
        )
        for source, count in sorted(data["items_by_source"].items()):
            typer.echo(f"  {source}: {count} items")


if __name__ == "__main__":
    app()
