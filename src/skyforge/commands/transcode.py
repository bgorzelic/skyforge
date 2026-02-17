"""Transcode command — create shareable versions of normalized footage."""

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from skyforge.core.media import VIDEO_EXTENSIONS
from skyforge.core.project import detect_project_dir

app = typer.Typer()
console = Console()


@app.command("presets")
def list_presets() -> None:
    """List all available transcode presets with their settings.

    Shows codec, resolution, quality, and use case for each preset.

    Example:
        skyforge transcode presets
    """
    from skyforge.core.transcoder import BUILTIN_PRESETS

    table = Table(title="Transcode Presets")
    table.add_column("Preset", style="cyan", no_wrap=True)
    table.add_column("Codec", style="green")
    table.add_column("Max Width", justify="right")
    table.add_column("CRF", justify="right")
    table.add_column("Speed")
    table.add_column("Audio", justify="right")
    table.add_column("Description", style="dim")

    for preset in BUILTIN_PRESETS.values():
        res = f"{preset.max_width}px" if preset.max_width else "source"
        table.add_row(
            preset.name,
            preset.codec.upper(),
            res,
            str(preset.crf),
            preset.encode_preset,
            preset.audio_bitrate,
            preset.description,
        )

    console.print(table)


@app.command("run")
def run(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
    preset_name: str = typer.Option("web", "--preset", "-p", help="Transcode preset to apply"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files"),
    skip_existing: bool = typer.Option(True, help="Skip files already transcoded"),
) -> None:
    """Transcode all normalized footage to a shareable format.

    Reads from 02_NORMALIZED/, writes to 06_TRANSCODED/<preset>/.
    Run `skyforge transcode presets` to see available presets.

    Example:
        skyforge transcode run --preset web
        skyforge transcode run "My Flight" --preset review
        skyforge transcode run . --preset archive --dry-run
    """
    from skyforge.core.transcoder import (
        generate_transcode_manifest,
        load_presets,
        transcode_project,
    )

    presets = load_presets()
    if preset_name not in presets:
        names = ", ".join(presets.keys())
        console.print(f"[red]Error:[/red] Unknown preset '{preset_name}'. Available: {names}")
        raise typer.Exit(1)

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        console.print('[dim]Create one with: skyforge init new "My Project"[/dim]')
        raise typer.Exit(1)

    norm_dir = proj / "02_NORMALIZED"
    transcode_dir = proj / "06_TRANSCODED"

    if not norm_dir.exists():
        console.print(
            "[red]Error:[/red] No 02_NORMALIZED/ directory. Run `skyforge ingest run` first."
        )
        raise typer.Exit(1)

    preset = presets[preset_name]

    # Count videos for progress bar
    total = sum(
        1
        for d in norm_dir.iterdir()
        if d.is_dir()
        for f in d.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )

    if total == 0:
        console.print("[yellow]No normalized videos found.[/yellow]")
        raise typer.Exit(0)

    res_label = f"{preset.max_width}px max width" if preset.max_width else "source resolution"

    console.print("\n[bold]Skyforge Transcode Pipeline[/bold]")
    console.print(f"  Project:  {proj}")
    console.print(f"  Preset:   {preset_name} — {preset.description}")
    console.print(f"  Codec:    {preset.codec.upper()}, CRF {preset.crf}")
    console.print(f"  Output:   {res_label}")
    console.print(f"  Videos:   {total}")
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
        task = progress.add_task("Transcoding...", total=total)

        def on_progress(file: Path, device: str) -> None:
            progress.update(
                task,
                advance=1,
                description=f"[cyan]{device}[/cyan] {file.name}",
            )

        results = transcode_project(
            norm_dir,
            transcode_dir,
            preset,
            skip_existing,
            dry_run,
            on_progress,
        )

    # Summary
    processed = [r for r in results if not r.skipped and not r.error]
    skipped = [r for r in results if r.skipped]
    errors = [r for r in results if r.error]

    console.print()
    console.print(f"[bold green]Transcoded:[/bold green] {len(processed)} files")
    if skipped:
        console.print(f"[dim]Skipped:[/dim] {len(skipped)} files")
    if errors:
        console.print(f"[red]Errors:[/red] {len(errors)} files")
        for r in errors:
            console.print(f"  [red]{r.source.name}:[/red] {r.error}")

    # Size reduction summary (only meaningful for actual transcodes)
    if processed and not dry_run:
        total_in = sum(r.input_size_bytes for r in processed)
        total_out = sum(r.output_size_bytes for r in processed)
        total_in_mb = total_in / (1024 * 1024)
        total_out_mb = total_out / (1024 * 1024)
        if total_in > 0 and total_out > 0:
            reduction = 100 * (1 - total_out / total_in)
            console.print(
                f"  Size: {total_in_mb:.0f} MB -> {total_out_mb:.0f} MB"
                f" ([green]{reduction:.0f}% smaller[/green])"
            )

    if not dry_run and results:
        manifest = transcode_dir / f"manifest_{preset_name}.json"
        generate_transcode_manifest(results, manifest)
        console.print(f"\n[bold]Output:[/bold]   {transcode_dir / preset_name}")
        console.print(f"[bold]Manifest:[/bold] {manifest}")

    console.print()


@app.command("file")
def transcode_single(
    input_file: Path = typer.Argument(..., help="Input video file"),
    preset_name: str = typer.Option("web", "--preset", "-p", help="Transcode preset to use"),
    output: Path | None = typer.Option(None, "-o", "--output", help="Output file path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show FFmpeg command without running"),
) -> None:
    """Transcode a single video file using a named preset.

    Output defaults to the same directory as input with a _<preset> suffix.

    Example:
        skyforge transcode file video_norm.mp4 --preset web
        skyforge transcode file video_norm.mp4 --preset review -o review.mp4
        skyforge transcode file video_norm.mp4 --preset mobile --dry-run
    """
    from skyforge.core.media import probe_file
    from skyforge.core.transcoder import (
        build_transcode_command,
        load_presets,
        transcode_file,
    )

    if not input_file.exists():
        console.print(f"[red]Error:[/red] File '{input_file}' not found.")
        raise typer.Exit(1)

    presets = load_presets()
    if preset_name not in presets:
        names = ", ".join(presets.keys())
        console.print(f"[red]Error:[/red] Unknown preset '{preset_name}'. Available: {names}")
        raise typer.Exit(1)

    preset = presets[preset_name]
    output_dir = output.parent if output else input_file.parent

    if dry_run:
        info = probe_file(input_file)
        out_path = output or (output_dir / f"{input_file.stem}_{preset_name}.mp4")
        cmd = build_transcode_command(
            input_file,
            out_path,
            preset,
            info.has_audio,
        )
        console.print("[yellow]Dry run — FFmpeg command:[/yellow]")
        console.print(" ".join(cmd))
        return

    result = transcode_file(
        input_file,
        output_dir,
        preset,
        skip_existing=True,
        dry_run=False,
    )

    if result.skipped:
        console.print(f"[dim]Skipped (already exists):[/dim] {result.output}")
    elif result.error:
        console.print(f"[red]Error:[/red] {result.error}")
        raise typer.Exit(1)
    else:
        reduction = ""
        if result.size_reduction_pct is not None:
            reduction = f" ({result.size_reduction_pct:.0f}% smaller)"
        console.print(f"[green]Done:[/green] {result.output}{reduction}")
