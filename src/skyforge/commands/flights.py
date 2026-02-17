"""Flights command — track and manage flight sessions."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from skyforge.core.media import scan_directory
from skyforge.core.project import load_project

app = typer.Typer()
console = Console()


@app.command("list")
def list_flights(
    data_dir: Path = typer.Option(".", help="Parent directory containing flight projects"),
):
    """List all flight projects in a directory."""
    candidates = [d for d in Path(data_dir).iterdir() if d.is_dir() and (d / "01_RAW").is_dir()]

    if not candidates:
        console.print("[yellow]No flight projects found.[/yellow]")
        console.print("[dim]Create one with: skyforge init new \"My Flight\"[/dim]")
        raise typer.Exit(0)

    table = Table(title="Flight Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Devices", style="green")
    table.add_column("Raw Files", justify="right")
    table.add_column("Normalized", justify="right")
    table.add_column("Status", style="yellow")

    for proj_dir in sorted(candidates):
        meta = load_project(proj_dir)
        raw_dir = proj_dir / "01_RAW"
        norm_dir = proj_dir / "02_NORMALIZED"

        devices = [d.name for d in raw_dir.iterdir() if d.is_dir()]
        raw_count = sum(1 for _ in raw_dir.rglob("*") if _.is_file())
        norm_count = sum(1 for _ in norm_dir.rglob("*.mp4")) if norm_dir.exists() else 0

        status = meta.get("status", "unknown")
        if norm_count > 0:
            status = "processed"
        elif raw_count > 0:
            status = "raw"

        table.add_row(
            proj_dir.name,
            ", ".join(devices) or "—",
            str(raw_count),
            str(norm_count),
            status,
        )

    console.print(table)


@app.command("info")
def flight_info(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
):
    """Show detailed information about a flight project."""
    raw_dir = project_dir / "01_RAW"
    if not raw_dir.exists():
        console.print(f"[red]Not a flight project:[/red] {project_dir}")
        raise typer.Exit(1)

    meta = load_project(project_dir)
    console.print(f"\n[bold]Project:[/bold] {meta.get('name', project_dir.name)}")
    console.print(f"[bold]Path:[/bold]    {project_dir.resolve()}")
    if "created" in meta:
        console.print(f"[bold]Created:[/bold] {meta['created']}")
    console.print()

    files = scan_directory(raw_dir)

    # Group by device
    devices: dict[str, list] = {}
    for f in files:
        devices.setdefault(f.device, []).append(f)

    for device, device_files in sorted(devices.items()):
        videos = [f for f in device_files if f.media_type == "video"]
        images = [f for f in device_files if f.media_type == "image"]
        total_mb = sum(f.size_bytes for f in device_files) / (1024 * 1024)
        total_dur = sum(f.duration for f in videos)

        console.print(f"  [cyan]{device}[/cyan]")
        console.print(f"    Videos: {len(videos)}  Images: {len(images)}")
        console.print(f"    Size: {total_mb:.0f} MB  Duration: {total_dur:.0f}s")
        if videos:
            sample = videos[0]
            flags = []
            if sample.is_hdr:
                flags.append("HDR")
            if sample.is_vfr:
                flags.append("VFR")
            fmt = f"{sample.codec} {sample.resolution} {sample.fps:.0f}fps"
            console.print(f"    Format: {fmt} {' '.join(flags)}")
        console.print()
