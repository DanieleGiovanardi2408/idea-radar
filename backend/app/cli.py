"""CLI Typer di Idea Radar."""

import typer

app = typer.Typer(help="Idea Radar CLI")


@app.command()
def hello() -> None:
    """Comando placeholder: verifica che la CLI funzioni."""
    typer.echo("Idea Radar CLI — pronto.")


if __name__ == "__main__":
    app()
