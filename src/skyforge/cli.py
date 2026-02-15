"""Skyforge CLI — reusable aerial media pipeline for AI Aerial Solutions."""

import typer

from skyforge import __version__
from skyforge.commands import ingest, flights, process, init, telemetry

app = typer.Typer(
    name="skyforge",
    help="AI Aerial Solutions — manage footage, process with AI, and track flight data.",
    no_args_is_help=True,
)

app.add_typer(init.app, name="init", help="Create a new flight project")
app.add_typer(ingest.app, name="ingest", help="Import, scan, and normalize aerial footage")
app.add_typer(flights.app, name="flights", help="Track and manage flight sessions")
app.add_typer(process.app, name="process", help="AI/ML processing on aerial media")
app.add_typer(telemetry.app, name="telemetry", help="Extract and analyze drone flight telemetry")


@app.command()
def version():
    """Show skyforge version."""
    typer.echo(f"skyforge v{__version__}")


if __name__ == "__main__":
    app()
