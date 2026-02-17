"""Ingest command — scan, normalize, and create proxies for aerial footage."""

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from skyforge.core.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, scan_directory
from skyforge.core.project import detect_project_dir

app = typer.Typer()
console = Console()


@app.command("scan")
def scan(
    source: Path = typer.Argument(".", help="Directory to scan for media files"),
    recursive: bool = typer.Option(True, help="Scan subdirectories"),
):
    """Scan a directory for aerial footage and display a summary.

    Shows file details including codec, resolution, HDR status, and VFR detection.
    Always runs locally (no FlightDeck required).
    """
    if not source.exists():
        console.print(f"[red]Error:[/red] Directory '{source}' not found.")
        raise typer.Exit(1)

    # If this is a project dir, scan only 01_RAW to avoid duplicates
    proj = detect_project_dir(source)
    scan_root = (proj / "01_RAW") if proj and (proj / "01_RAW").exists() else source
    files = scan_directory(scan_root, recursive)

    if not files:
        console.print("[yellow]No media files found.[/yellow]")
        raise typer.Exit(0)

    # Group by device
    devices: dict[str, list] = {}
    for f in files:
        devices.setdefault(f.device, []).append(f)

    for device, device_files in sorted(devices.items()):
        videos = [f for f in device_files if f.media_type == "video"]
        images = [f for f in device_files if f.media_type == "image"]
        other = [f for f in device_files if f.media_type not in ("video", "image")]

        if videos:
            table = Table(title=f"[bold]{device}[/bold] — Video")
            table.add_column("File", style="cyan", max_width=40)
            table.add_column("Codec", style="green")
            table.add_column("Resolution")
            table.add_column("FPS", justify="right")
            table.add_column("Duration", justify="right")
            table.add_column("Size", justify="right", style="magenta")
            table.add_column("Flags", style="yellow")

            for v in sorted(videos, key=lambda x: x.path.name):
                flags = []
                if v.is_hdr:
                    flags.append("HDR")
                if v.is_vfr:
                    flags.append("VFR")
                if not v.has_audio:
                    flags.append("NO-AUDIO")
                if v.is_portrait:
                    flags.append("PORTRAIT")

                dur = f"{v.duration:.0f}s" if v.duration else "?"
                fps = f"{v.fps:.1f}" if v.fps else "?"
                size = f"{v.size_mb:.0f}MB" if v.size_mb < 1024 else f"{v.size_gb:.1f}GB"

                table.add_row(
                    v.path.name, v.codec, v.resolution,
                    fps, dur, size, " ".join(flags) or "—",
                )

            console.print(table)

        if images:
            console.print(f"  [dim]{device}:[/dim] {len(images)} images")
        if other:
            console.print(
                f"  [dim]{device}:[/dim] {len(other)} other files (telemetry/proxies/thumbnails)"
            )

    console.print()
    total_video = sum(1 for f in files if f.media_type == "video")
    total_image = sum(1 for f in files if f.media_type == "image")
    total_size = sum(f.size_bytes for f in files)
    total_gb = total_size / (1024 * 1024 * 1024)
    console.print(
        f"[bold]Total:[/bold] {total_video} videos, {total_image} images, "
        f"{total_gb:.1f} GB across {len(devices)} device(s)"
    )


@app.command("run")
def run(
    project_dir: Path = typer.Argument(".", help="Project directory (must contain 01_RAW/)"),
    fps: int = typer.Option(30, help="Target frame rate"),
    crf: int = typer.Option(18, help="Video quality (CRF, lower = better, 18 = visually lossless)"),
    skip_proxies: bool = typer.Option(False, "--skip-proxies", help="Skip proxy generation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without processing"),
    local: bool = typer.Option(False, "--local", help="Force local processing (skip FlightDeck)"),
):
    """Run the full ingest pipeline: normalize -> proxy -> manifest.

    By default, tries FlightDeck API for processing. Use --local for offline mode.
    Automatically detects devices, handles HDR tonemapping, fixes VFR,
    and generates edit-ready proxies.
    """
    from skyforge.config import load_config

    config = load_config()

    # Try FlightDeck API unless local mode
    if not local and not config.local_mode and config.is_configured:
        _run_remote(project_dir, config)
        return

    if not local and not config.local_mode and not config.is_configured:
        console.print("[dim]FlightDeck not configured. Running locally.[/dim]")
        console.print("[dim]Configure with: skyforge auth login[/dim]\n")

    _run_local(project_dir, fps, crf, skip_proxies, dry_run)


def _run_remote(project_dir: Path, config) -> None:
    """Run ingest via FlightDeck API."""
    from skyforge.client import FlightDeckClient, FlightDeckError, FlightDeckUnavailableError

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        raise typer.Exit(1)

    raw_dir = proj / "01_RAW"
    videos = sorted([
        f for d in raw_dir.iterdir() if d.is_dir()
        for f in d.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ])

    if not videos:
        console.print("[yellow]No video files found in 01_RAW/.[/yellow]")
        raise typer.Exit(0)

    console.print("\n[bold]Skyforge Ingest via FlightDeck[/bold]")
    console.print(f"  Project: {proj}")
    console.print(f"  Videos:  {len(videos)}")
    console.print(f"  API:     {config.api_url}\n")

    try:
        with FlightDeckClient(config) as client:
            if not client.health_check():
                console.print(
                    "[yellow]FlightDeck unreachable."
                    " Falling back to local mode.[/yellow]"
                )
                _run_local(project_dir, 30, 18, False, False)
                return

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Uploading...", total=len(videos))

                for video in videos:
                    progress.update(task, description=f"Uploading [cyan]{video.name}[/cyan]")
                    asset_id = client.upload(video)
                    job_id = client.start_processing(asset_id)
                    progress.update(
                        task,
                        description=f"Processing [cyan]{video.name}[/cyan] (job {job_id})",
                    )
                    progress.advance(task)

            console.print("\n[bold green]Upload complete.[/bold green]")
            console.print("[dim]Check status: skyforge status job <job_id>[/dim]")

    except FlightDeckUnavailableError:
        console.print("[yellow]FlightDeck unreachable. Falling back to local mode.[/yellow]\n")
        _run_local(project_dir, 30, 18, False, False)
    except FlightDeckError as e:
        console.print(f"[red]FlightDeck error:[/red] {e}")
        console.print("[yellow]Falling back to local mode.[/yellow]\n")
        _run_local(project_dir, 30, 18, False, False)


def _run_local(
    project_dir: Path, fps: int, crf: int, skip_proxies: bool, dry_run: bool
) -> None:
    """Run ingest locally using Skyforge core modules."""
    from skyforge.core.pipeline import PipelineConfig, generate_manifest, run_pipeline

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        console.print("[dim]Create one with: skyforge init new \"My Project\"[/dim]")
        raise typer.Exit(1)

    raw_dir = proj / "01_RAW"
    norm_dir = proj / "02_NORMALIZED"
    proxy_dir = proj / "02_PROXIES"

    pipeline_config = PipelineConfig(
        target_fps=fps,
        crf=crf,
        skip_proxies=skip_proxies,
        dry_run=dry_run,
    )

    console.print("\n[bold]Skyforge Ingest Pipeline (local)[/bold]")
    console.print(f"  Project:    {proj}")
    console.print(f"  Target FPS: {fps}")
    console.print(f"  CRF:        {crf}")
    console.print(f"  Proxies:    {'skip' if skip_proxies else 'yes'}")
    if dry_run:
        console.print("  [yellow]DRY RUN — no files will be written[/yellow]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        total = sum(
            1 for d in raw_dir.iterdir() if d.is_dir()
            for f in d.rglob("*")
            if f.is_file() and f.suffix.lower() in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)
        )
        task = progress.add_task("Processing...", total=total)

        def on_progress(file: Path, device: str):
            progress.update(task, advance=1, description=f"[cyan]{device}[/cyan] {file.name}")

        results = run_pipeline(raw_dir, norm_dir, proxy_dir, pipeline_config, on_progress)

    # Summary
    console.print()
    processed = [r for r in results if not r.skipped and not r.error]
    skipped = [r for r in results if r.skipped]
    errors = [r for r in results if r.error]
    tonemapped = [r for r in results if r.hdr_tonemapped]

    console.print(f"[bold green]Processed:[/bold green] {len(processed)} files")
    if skipped:
        console.print(f"[dim]Skipped (already done):[/dim] {len(skipped)} files")
    if tonemapped:
        console.print(f"[yellow]HDR -> SDR tonemapped:[/yellow] {len(tonemapped)} files")
    if errors:
        console.print(f"[red]Errors:[/red] {len(errors)} files")
        for e in errors:
            console.print(f"  [red]{e.source.name}:[/red] {e.error}")

    # Generate manifest
    manifest_path = proj / "manifest.json"
    if not dry_run:
        generate_manifest(results, manifest_path)
        console.print(f"\n[bold]Manifest:[/bold] {manifest_path}")

    console.print(f"\n[bold]Normalized:[/bold] {norm_dir}")
    if not skip_proxies:
        console.print(f"[bold]Proxies:[/bold]    {proxy_dir}")
    console.print()
