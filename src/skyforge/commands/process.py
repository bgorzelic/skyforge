"""Process command — AI/ML processing on aerial media."""

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer()
console = Console()


@app.command("analyze")
def analyze(
    file: Path = typer.Argument(..., help="Media file to analyze"),
    model: str = typer.Option("default", help="AI model to use for analysis"),
):
    """Run AI analysis on an aerial media file."""
    if not file.exists():
        console.print(f"[red]Error:[/red] File '{file}' not found.")
        raise typer.Exit(1)

    size_mb = file.stat().st_size / (1024 * 1024)
    console.print(f"[bold]Analyzing:[/bold] {file.name} ({size_mb:.1f} MB)")
    console.print(f"[bold]Model:[/bold] {model}")
    console.print("[dim]AI analysis pipeline coming soon.[/dim]")


@app.command("extract-frames")
def extract_frames(
    video: Path = typer.Argument(..., help="Video file to extract frames from"),
    output: Path = typer.Option("./frames", help="Output directory for frames"),
    interval: float = typer.Option(1.0, help="Extract a frame every N seconds"),
):
    """Extract frames from a video file at a specified interval."""
    if not video.exists():
        console.print(f"[red]Error:[/red] Video '{video}' not found.")
        raise typer.Exit(1)

    output.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Extracting frames[/bold] from {video.name}")
    console.print(f"[bold]Interval:[/bold] every {interval}s → {output}")
    console.print("[dim]Frame extraction coming soon.[/dim]")
