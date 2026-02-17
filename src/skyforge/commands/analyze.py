"""Analyze command — automated video analysis, segment selection, and export."""

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from skyforge.core.media import VIDEO_EXTENSIONS
from skyforge.core.project import detect_project_dir

app = typer.Typer()
console = Console()


@app.command("run")
def run(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
    min_segment: float = typer.Option(5.0, help="Minimum segment duration (seconds)"),
    max_segment: float = typer.Option(25.0, help="Maximum segment duration (seconds)"),
    min_confidence: float = typer.Option(0.3, help="Minimum confidence score (0-1)"),
    sample_interval: float = typer.Option(1.0, help="Frame sampling interval (seconds)"),
    skip_export: bool = typer.Option(False, "--skip-export", help="Only analyze, don't export"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Analyze only, show what would be selected"
    ),
    local: bool = typer.Option(False, "--local", help="Force local processing (skip FlightDeck)"),
):
    """Run the full analysis -> select -> export pipeline.

    By default, tries FlightDeck API. Use --local for offline mode.
    Analyzes normalized footage, identifies usable segments,
    trims selects, and creates report-ready deliverables.
    """
    from skyforge.config import load_config

    config = load_config()

    if not local and not config.local_mode and config.is_configured:
        _run_remote(project_dir, config)
        return

    if not local and not config.local_mode and not config.is_configured:
        console.print("[dim]FlightDeck not configured. Running locally.[/dim]")
        console.print("[dim]Configure with: skyforge auth login[/dim]\n")

    _run_local(
        project_dir, min_segment, max_segment, min_confidence,
        sample_interval, skip_export, dry_run,
    )


def _run_remote(project_dir: Path, config) -> None:
    """Run analysis via FlightDeck API."""
    from skyforge.client import FlightDeckClient, FlightDeckError, FlightDeckUnavailableError

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project.")
        raise typer.Exit(1)

    console.print("\n[bold]Skyforge Analysis via FlightDeck[/bold]")
    console.print(f"  Project: {proj}")
    console.print(f"  API:     {config.api_url}\n")

    try:
        with FlightDeckClient(config) as client:
            if not client.health_check():
                console.print(
                    "[yellow]FlightDeck unreachable. Falling back to local mode.[/yellow]\n"
                )
                _run_local(project_dir, 5.0, 25.0, 0.3, 1.0, False, False)
                return

            console.print(
                "[dim]Remote analysis via FlightDeck API.[/dim]"
            )
            console.print(
                "[dim]Upload footage first with: skyforge ingest run[/dim]"
            )
            console.print(
                "[dim]Then check results with: skyforge status job <job_id>[/dim]"
            )

    except FlightDeckUnavailableError:
        console.print("[yellow]FlightDeck unreachable. Falling back to local mode.[/yellow]\n")
        _run_local(project_dir, 5.0, 25.0, 0.3, 1.0, False, False)
    except FlightDeckError as e:
        console.print(f"[red]FlightDeck error:[/red] {e}")
        console.print("[yellow]Falling back to local mode.[/yellow]\n")
        _run_local(project_dir, 5.0, 25.0, 0.3, 1.0, False, False)


def _run_local(
    project_dir: Path,
    min_segment: float,
    max_segment: float,
    min_confidence: float,
    sample_interval: float,
    skip_export: bool,
    dry_run: bool,
) -> None:
    """Run analysis locally using Skyforge core modules."""
    from skyforge.core.analyzer import VideoAnalysis, analyze_video
    from skyforge.core.exporter import export_report_ready, trim_segment
    from skyforge.core.selector import (
        SelectsResult,
        generate_master_timeline,
        save_selects,
        select_segments,
    )

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ found).")
        raise typer.Exit(1)

    norm_dir = proj / "02_NORMALIZED"
    analysis_dir = proj / "03_ANALYSIS"
    selects_dir = proj / "04_SELECTS"
    exports_dir = proj / "05_EXPORTS"

    if not norm_dir.exists():
        console.print(
            "[red]Error:[/red] No 02_NORMALIZED/ directory. Run `skyforge ingest run` first."
        )
        raise typer.Exit(1)

    # Collect all normalized videos
    videos = sorted([
        f for d in norm_dir.iterdir() if d.is_dir()
        for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ])

    if not videos:
        console.print("[yellow]No normalized videos found.[/yellow]")
        raise typer.Exit(0)

    console.print("\n[bold]Skyforge Video Analysis Pipeline (local)[/bold]")
    console.print(f"  Project:        {proj}")
    console.print(f"  Videos:         {len(videos)}")
    console.print(f"  Segment range:  {min_segment}-{max_segment}s")
    console.print(f"  Min confidence: {min_confidence}")
    console.print()

    # -- Phase 1: Analyze --
    console.print("[bold cyan]Phase 1: Analyzing footage...[/bold cyan]")
    all_analyses: list[VideoAnalysis] = []
    all_selects: list[SelectsResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(videos))

        for video in videos:
            device = video.parent.name
            progress.update(task, description=f"[cyan]{device}[/cyan] {video.name}")

            video_analysis_dir = analysis_dir / video.stem
            analysis = analyze_video(video, video_analysis_dir, sample_interval=sample_interval)
            all_analyses.append(analysis)

            progress.advance(task)

    # -- Phase 2: Select segments --
    console.print("\n[bold cyan]Phase 2: Selecting usable segments...[/bold cyan]")

    for analysis in all_analyses:
        selects = select_segments(
            analysis,
            min_segment=min_segment,
            max_segment=max_segment,
            min_confidence=min_confidence,
        )
        all_selects.append(selects)

        selects_json = analysis_dir / f"selects_{Path(analysis.source_file).stem}.json"
        save_selects(selects, selects_json)

    master_path = analysis_dir / "master_selects.json"
    generate_master_timeline(all_selects, master_path)

    _print_selection_summary(all_selects)

    if dry_run or skip_export:
        console.print(f"\n[bold]Analysis saved to:[/bold] {analysis_dir}")
        if dry_run:
            console.print("[yellow]Dry run — no clips exported.[/yellow]")
        return

    # -- Phase 3: Export selects --
    console.print("\n[bold cyan]Phase 3: Exporting selected clips...[/bold cyan]")

    total_segments = sum(len(s.segments) for s in all_selects)
    exported = 0
    report_exported = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Exporting...", total=total_segments * 2)

        for selects in all_selects:
            for segment in selects.segments:
                src_name = Path(segment.source_file).stem
                progress.update(task, description=f"Trimming {src_name} seg{segment.segment_id}")

                clip = trim_segment(segment, selects_dir)
                if clip:
                    exported += 1
                progress.advance(task)

                progress.update(task, description=f"Report {src_name} seg{segment.segment_id}")
                report = export_report_ready(segment, exports_dir)
                if report:
                    report_exported += 1
                progress.advance(task)

    # -- Summary --
    console.print()
    console.print("[bold green]Analysis complete![/bold green]")
    console.print(f"  Analysis:    {analysis_dir}")
    console.print(f"  Selects:     {selects_dir} ({exported} clips)")
    console.print(f"  Reports:     {exports_dir} ({report_exported} clips)")
    console.print(f"  Master JSON: {master_path}")
    console.print()


@app.command("summary")
def show_analysis_summary(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
):
    """Show a summary of existing analysis results."""
    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Not a skyforge project.[/red]")
        raise typer.Exit(1)

    master = proj / "03_ANALYSIS" / "master_selects.json"
    if not master.exists():
        console.print("[yellow]No analysis found. Run `skyforge analyze run` first.[/yellow]")
        raise typer.Exit(0)

    import json
    data = json.loads(master.read_text())

    console.print("\n[bold]Analysis Summary[/bold]")
    console.print(f"  Sources:  {data['total_sources']}")
    console.print(f"  Segments: {data['total_segments']}")
    console.print(
        f"  Duration: {data['total_selected_duration']:.0f}s "
        f"({data['total_selected_duration']/60:.1f} min)"
    )

    if data["segments"]:
        table = Table(title="Top Segments (by confidence)")
        table.add_column("Source", style="cyan", max_width=25)
        table.add_column("Seg", justify="right")
        table.add_column("Time", style="green")
        table.add_column("Dur", justify="right")
        table.add_column("Conf", justify="right", style="magenta")
        table.add_column("Tags", style="yellow", max_width=40)

        for seg in data["segments"][:20]:
            src = Path(seg["source_file"]).stem
            time_range = f"{seg['start_time']:.0f}-{seg['end_time']:.0f}s"
            table.add_row(
                src,
                str(seg["segment_id"]),
                time_range,
                f"{seg['duration']:.0f}s",
                f"{seg['confidence']:.2f}",
                ", ".join(seg["reason_tags"][:4]),
            )

        console.print(table)


def _print_selection_summary(all_selects) -> None:
    """Print a summary table of selections."""
    table = Table(title="Segment Selection Summary")
    table.add_column("Source", style="cyan", max_width=30)
    table.add_column("Total", justify="right")
    table.add_column("Selected", justify="right", style="green")
    table.add_column("Rejected", justify="right", style="red")
    table.add_column("Segments", justify="right", style="magenta")
    table.add_column("Best Conf", justify="right", style="yellow")

    for sel in all_selects:
        src = Path(sel.source_file).stem
        best_conf = max((s.confidence for s in sel.segments), default=0)
        table.add_row(
            src,
            f"{sel.total_duration:.0f}s",
            f"{sel.selected_duration:.0f}s",
            f"{sel.rejected_duration:.0f}s",
            str(len(sel.segments)),
            f"{best_conf:.2f}",
        )

    console.print(table)

    total_segs = sum(len(s.segments) for s in all_selects)
    total_selected = sum(s.selected_duration for s in all_selects)
    console.print(f"\n[bold]Total:[/bold] {total_segs} segments, {total_selected:.0f}s selected")
