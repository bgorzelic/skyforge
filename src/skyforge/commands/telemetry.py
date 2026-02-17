"""Telemetry command — extract and analyze drone flight data from SRT files."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from skyforge.core.telemetry import (
    export_csv,
    export_gpx,
    export_json,
    export_kml,
    parse_srt,
    summary,
)

app = typer.Typer()
console = Console()


@app.command("parse")
def parse(
    srt_file: Path = typer.Argument(..., help="SRT telemetry file from drone"),
    output: Path = typer.Option(
        None, "-o", "--output", help="Output file (auto-detect format from extension)"
    ),
    format: str = typer.Option("json", "-f", "--format", help="Output format: json, csv, gpx, kml"),
):
    """Parse a drone SRT telemetry file and export structured data.

    Example:
        skyforge telemetry parse 01_RAW/Drone/PTSC_0008.SRT
        skyforge telemetry parse PTSC_0008.SRT -f csv -o flight_data.csv
        skyforge telemetry parse PTSC_0008.SRT -f kml -o track.kml
    """
    if not srt_file.exists():
        console.print(f"[red]Error:[/red] File '{srt_file}' not found.")
        raise typer.Exit(1)

    frames = parse_srt(srt_file)
    if not frames:
        console.print("[yellow]No telemetry data found in SRT file.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold]Parsed:[/bold] {len(frames)} telemetry frames from {srt_file.name}")

    # Auto-determine output path
    if output is None:
        output = srt_file.with_suffix(f".{format}")

    # Detect format from output extension if not explicitly set
    ext = output.suffix.lower()
    if ext == ".csv":
        format = "csv"
    elif ext == ".gpx":
        format = "gpx"
    elif ext == ".kml":
        format = "kml"
    elif ext in (".json", ".geojson"):
        format = "json"

    if format == "csv":
        export_csv(frames, output)
    elif format == "gpx":
        export_gpx(frames, output, name=srt_file.stem)
    elif format == "kml":
        export_kml(frames, output, name=srt_file.stem)
    else:
        export_json(frames, output)

    console.print(f"[green]Exported:[/green] {output} ({format})")


@app.command("summary")
def show_summary(
    srt_file: Path = typer.Argument(..., help="SRT telemetry file from drone"),
):
    """Show a summary of flight telemetry data.

    Example:
        skyforge telemetry summary 01_RAW/Drone/PTSC_0008.SRT
    """
    if not srt_file.exists():
        console.print(f"[red]Error:[/red] File '{srt_file}' not found.")
        raise typer.Exit(1)

    frames = parse_srt(srt_file)
    if not frames:
        console.print("[yellow]No telemetry data found.[/yellow]")
        raise typer.Exit(1)

    stats = summary(frames)

    console.print(f"\n[bold]Flight Telemetry: {srt_file.name}[/bold]\n")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Duration", f"{stats['duration_s']:.0f}s ({stats['duration_s']/60:.1f} min)")
    table.add_row("Data Points", str(stats["total_frames"]))
    table.add_row("GPS Points", str(stats["gps_points"]))

    if stats.get("start_gps"):
        lat, lon = stats["start_gps"]
        table.add_row("Start GPS", f"{lat:.6f}, {lon:.6f}")
    if stats.get("end_gps"):
        lat, lon = stats["end_gps"]
        table.add_row("End GPS", f"{lat:.6f}, {lon:.6f}")

    if stats.get("max_height_m") is not None:
        h = f"{stats['max_height_m']:.1f}m ({stats['max_height_ft']:.0f}ft)"
        table.add_row("Max Height", h)
    if stats.get("max_speed_ms") is not None:
        s = f"{stats['max_speed_ms']:.1f}m/s ({stats['max_speed_mph']:.1f}mph)"
        table.add_row("Max Speed", s)
    if stats.get("max_distance_m") is not None:
        table.add_row("Max Distance", f"{stats['max_distance_m']:.1f}m")
    if stats.get("iso_range"):
        table.add_row("ISO Range", stats["iso_range"])

    console.print(table)
    console.print()


@app.command("parse-all")
def parse_all(
    project_dir: Path = typer.Argument(".", help="Project directory"),
    format: str = typer.Option("json", "-f", "--format", help="Output format: json, csv, gpx, kml"),
):
    """Parse all SRT files in a project and export telemetry data.

    Example:
        skyforge telemetry parse-all .
        skyforge telemetry parse-all . -f kml
    """
    raw_dir = project_dir / "01_RAW"
    if not raw_dir.exists():
        console.print("[red]Error:[/red] No 01_RAW/ directory found.")
        raise typer.Exit(1)

    srt_files = list(raw_dir.rglob("*.SRT")) + list(raw_dir.rglob("*.srt"))
    if not srt_files:
        console.print("[yellow]No SRT telemetry files found.[/yellow]")
        raise typer.Exit(0)

    telemetry_dir = project_dir / "05_TELEMETRY"
    telemetry_dir.mkdir(exist_ok=True)

    for srt in sorted(srt_files):
        frames = parse_srt(srt)
        if not frames:
            console.print(f"  [yellow]Skip:[/yellow] {srt.name} (no data)")
            continue

        base = srt.stem
        if format == "csv":
            out = telemetry_dir / f"{base}.csv"
            export_csv(frames, out)
        elif format == "gpx":
            out = telemetry_dir / f"{base}.gpx"
            export_gpx(frames, out, name=base)
        elif format == "kml":
            out = telemetry_dir / f"{base}.kml"
            export_kml(frames, out, name=base)
        else:
            out = telemetry_dir / f"{base}.json"
            export_json(frames, out)

        stats = summary(frames)
        height_str = f", max {stats['max_height_m']:.0f}m" if stats.get("max_height_m") else ""
        msg = f"  [green]{srt.name}[/green] → {out.name}"
        console.print(f"{msg} ({len(frames)} points{height_str})")

    console.print(f"\n[bold]Telemetry exported to:[/bold] {telemetry_dir}")
