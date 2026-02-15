"""Ingest command — scan, normalize, and create proxies for aerial footage."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from skyforge.core.media import scan_directory, probe_file, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from skyforge.core.pipeline import PipelineConfig, run_pipeline, generate_manifest
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
    """
    if not source.exists():
        console.print(f"[red]Error:[/red] Directory '{source}' not found.")
        raise typer.Exit(1)

    files = scan_directory(source, recursive)

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
            console.print(f"  [dim]{device}:[/dim] {len(other)} other files (telemetry/proxies/thumbnails)")

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
):
    """Run the full ingest pipeline: normalize → proxy → manifest.

    Automatically detects devices, handles HDR tonemapping, fixes VFR,
    and generates edit-ready proxies.

    Example:
        skyforge ingest run .
        skyforge ingest run "/path/to/2nd Flight" --fps 60
        skyforge ingest run . --dry-run
    """
    # Find project root
    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        console.print("[dim]Create one with: skyforge init new \"My Project\"[/dim]")
        raise typer.Exit(1)

    raw_dir = proj / "01_RAW"
    norm_dir = proj / "02_NORMALIZED"
    proxy_dir = proj / "02_PROXIES"

    config = PipelineConfig(
        target_fps=fps,
        crf=crf,
        skip_proxies=skip_proxies,
        dry_run=dry_run,
    )

    console.print(f"\n[bold]Skyforge Ingest Pipeline[/bold]")
    console.print(f"  Project:    {proj}")
    console.print(f"  Target FPS: {fps}")
    console.print(f"  CRF:        {crf}")
    console.print(f"  Proxies:    {'skip' if skip_proxies else 'yes'}")
    if dry_run:
        console.print(f"  [yellow]DRY RUN — no files will be written[/yellow]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # Count total files first
        total = sum(
            1 for d in raw_dir.iterdir() if d.is_dir()
            for f in d.iterdir()
            if f.is_file() and f.suffix.lower() in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)
        )
        task = progress.add_task("Processing...", total=total)

        def on_progress(file: Path, device: str):
            progress.update(task, advance=1, description=f"[cyan]{device}[/cyan] {file.name}")

        results = run_pipeline(raw_dir, norm_dir, proxy_dir, config, progress_callback=on_progress)

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
        console.print(f"[yellow]HDR → SDR tonemapped:[/yellow] {len(tonemapped)} files")
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
