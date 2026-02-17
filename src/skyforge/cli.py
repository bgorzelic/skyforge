"""Skyforge CLI — companion tool for FlightDeck drone platform."""

import typer

from skyforge import __version__
from skyforge.commands import (
    analyze,
    auth,
    export,
    flights,
    ingest,
    init,
    process,
    status,
    telemetry,
)

app = typer.Typer(
    name="skyforge",
    help="AI Aerial Solutions — manage footage locally or via FlightDeck API.",
    no_args_is_help=True,
)

app.add_typer(init.app, name="init", help="Create a new flight project")
app.add_typer(ingest.app, name="ingest", help="Import, scan, and normalize aerial footage")
app.add_typer(flights.app, name="flights", help="Track and manage flight sessions")
app.add_typer(process.app, name="process", help="AI/ML processing on aerial media")
app.add_typer(telemetry.app, name="telemetry", help="Extract and analyze drone flight telemetry")
app.add_typer(analyze.app, name="analyze", help="Analyze footage, select segments, export clips")
app.add_typer(export.app, name="export", help="Export deliverables via FlightDeck")
app.add_typer(status.app, name="status", help="Check FlightDeck job status")
app.add_typer(auth.app, name="auth", help="Authenticate with FlightDeck API")


@app.command()
def version():
    """Show skyforge version."""
    typer.echo(f"skyforge v{__version__}")


if __name__ == "__main__":
    app()
