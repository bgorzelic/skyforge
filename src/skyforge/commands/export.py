"""Export command — request deliverable exports from FlightDeck."""

import typer
from rich.console import Console

from skyforge.client import FlightDeckClient, FlightDeckError, FlightDeckUnavailableError
from skyforge.config import load_config

app = typer.Typer()
console = Console()


@app.command("deliverable")
def export_deliverable(
    segment_id: str = typer.Argument(help="FlightDeck segment ID to export"),
    burn_timecode: bool = typer.Option(True, help="Burn timecode into video"),
    width: int = typer.Option(1920, help="Target width (pixels)"),
) -> None:
    """Export a report-ready deliverable from a FlightDeck segment.

    Creates a 1080p clip with optional timecode and filename burn-in.
    Requires an active FlightDeck API connection.

    Example:
        skyforge export deliverable seg_abc123
        skyforge export deliverable seg_abc123 --no-burn-timecode --width 1280
    """
    config = load_config()

    if config.local_mode:
        console.print("[red]Error:[/red] Export requires FlightDeck API (local mode is active).")
        console.print("[dim]Disable local mode: unset SKYFORGE_LOCAL_MODE[/dim]")
        raise typer.Exit(1)

    if not config.is_configured:
        console.print("[red]Error:[/red] FlightDeck API key is not set.")
        console.print("[dim]Run: skyforge auth login[/dim]")
        raise typer.Exit(1)

    try:
        with FlightDeckClient(config) as client:
            console.print(f"Requesting export for segment [cyan]{segment_id}[/cyan]...")
            job_id = client.export_deliverable(
                segment_id,
                options={"burn_timecode": burn_timecode, "target_width": width},
            )
            console.print(f"Export job started: [green]{job_id}[/green]")
            console.print(f"[dim]Check status: skyforge status job {job_id}[/dim]")
            console.print(f"[dim]Watch until done: skyforge status job {job_id} --watch[/dim]")
    except FlightDeckUnavailableError:
        console.print(f"[red]Error:[/red] FlightDeck not available at {config.api_url}")
        console.print(
            "[dim]Check the connection or run analysis locally:"
            " skyforge analyze run[/dim]"
        )
        raise typer.Exit(1) from None
    except FlightDeckError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None
