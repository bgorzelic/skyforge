"""Status command — check FlightDeck processing job status."""

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from skyforge.client import FlightDeckClient, FlightDeckError, FlightDeckUnavailableError, JobStatus
from skyforge.config import load_config

app = typer.Typer()
console = Console()


@app.command("job")
def job_status(
    job_id: str = typer.Argument(help="FlightDeck job ID to check"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Poll until the job completes"),
) -> None:
    """Check the status of a FlightDeck processing job.

    Example:
        skyforge status job abc123
        skyforge status job abc123 --watch
    """
    config = load_config()

    try:
        with FlightDeckClient(config) as client:
            if watch:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task(f"Watching job {job_id}...", total=None)

                    def on_update(status: JobStatus) -> None:
                        desc = f"[cyan]{status.status}[/cyan] ({status.progress:.0f}%)"
                        if status.message:
                            desc += f" — {status.message}"
                        progress.update(task, description=desc)

                    final = client.poll_job(job_id, callback=on_update)

                _print_status(final)
            else:
                status = client.get_job_status(job_id)
                _print_status(status)
    except FlightDeckUnavailableError:
        console.print(f"[red]Error:[/red] FlightDeck not available at {config.api_url}")
        raise typer.Exit(1) from None
    except FlightDeckError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None


@app.command("health")
def health() -> None:
    """Check whether the FlightDeck API is reachable.

    Example:
        skyforge status health
    """
    config = load_config()

    with FlightDeckClient(config) as client:
        ok = client.health_check()

    if ok:
        console.print(f"[green]FlightDeck is healthy[/green] at {config.api_url}")
    else:
        console.print(f"[red]FlightDeck is unreachable[/red] at {config.api_url}")
        raise typer.Exit(1)


def _print_status(status: JobStatus) -> None:
    """Print a formatted job status block."""
    color = {
        "pending": "yellow",
        "processing": "cyan",
        "completed": "green",
        "failed": "red",
    }.get(status.status, "white")

    console.print(f"\nJob:      [bold]{status.job_id}[/bold]")
    console.print(f"Status:   [{color}]{status.status}[/{color}]")
    console.print(f"Progress: {status.progress:.0f}%")
    if status.message:
        console.print(f"Message:  {status.message}")
    if status.result_url:
        console.print(f"Result:   [cyan]{status.result_url}[/cyan]")
