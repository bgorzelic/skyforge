"""Detect command — run YOLO object detection on normalized aerial footage."""

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

_INSTALL_HINT = (
    "[red]Error:[/red] ultralytics is not installed.\n"
    "  Install with: [cyan]pip install skyforge\\[detect][/cyan]"
)


@app.command("run")
def run(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
    model: str = typer.Option("yolov8n.pt", "--model", "-m", help="YOLO model weights file"),
    confidence: float = typer.Option(
        0.25, "--confidence", "-c", help="Minimum detection confidence (0-1)"
    ),
    interval: float = typer.Option(2.0, "--interval", "-i", help="Seconds between sampled frames"),
    classes: str | None = typer.Option(
        None, "--classes", help="Comma-separated class filter (e.g. 'car,person,truck')"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview files without running detection"
    ),
) -> None:
    """Run object detection on all normalized videos in a flight project.

    Reads from 02_NORMALIZED/<device>/, writes detection JSON to
    07_DETECTIONS/<device>/<stem>_detections.json.

    Example:
        skyforge detect run
        skyforge detect run "My Flight" --model yolov8s.pt --interval 1.0
        skyforge detect run . --classes car,person,truck --dry-run
    """
    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        console.print('[dim]Create one with: skyforge init new "My Project"[/dim]')
        raise typer.Exit(1)

    norm_dir = proj / "02_NORMALIZED"
    detect_dir = proj / "07_DETECTIONS"

    if not norm_dir.exists():
        console.print(
            "[red]Error:[/red] No 02_NORMALIZED/ directory. Run `skyforge ingest run` first."
        )
        raise typer.Exit(1)

    # Collect video files grouped by device subdirectory
    video_files: list[tuple[Path, str]] = []
    for device_dir in sorted(norm_dir.iterdir()):
        if not device_dir.is_dir():
            continue
        device = device_dir.name
        for f in sorted(device_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                video_files.append((f, device))

    if not video_files:
        console.print("[yellow]No normalized videos found.[/yellow]")
        raise typer.Exit(0)

    classes_filter = [c.strip() for c in classes.split(",")] if classes else None

    console.print("\n[bold]Skyforge Object Detection Pipeline[/bold]")
    console.print(f"  Project:    {proj}")
    console.print(f"  Model:      {model}")
    console.print(f"  Confidence: {confidence}")
    console.print(f"  Interval:   {interval}s between frames")
    console.print(f"  Videos:     {len(video_files)}")
    if classes_filter:
        console.print(f"  Filter:     {', '.join(classes_filter)}")
    if dry_run:
        console.print("  [yellow]DRY RUN — no detection will be performed[/yellow]")
    console.print()

    if dry_run:
        table = Table(title="Files to Process")
        table.add_column("Device", style="cyan")
        table.add_column("File")
        table.add_column("Output", style="dim")
        for video_path, device in video_files:
            out = detect_dir / device / f"{video_path.stem}_detections.json"
            table.add_row(device, video_path.name, str(out.relative_to(proj)))
        console.print(table)
        return

    # Lazy import — fail gracefully if ultralytics not installed
    try:
        from skyforge.core.detector import ObjectDetector, detect_video, save_detections
    except ImportError:
        console.print(_INSTALL_HINT)
        raise typer.Exit(1) from None

    try:
        detector = ObjectDetector(
            model_name=model,
            confidence=confidence,
            device=None,
        )
    except ImportError:
        console.print(_INSTALL_HINT)
        raise typer.Exit(1) from None

    total_objects = 0
    all_classes: dict[str, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Detecting...", total=len(video_files))

        for video_path, device in video_files:
            progress.update(
                task,
                description=f"[cyan]{device}[/cyan] {video_path.name}",
            )

            detections = detect_video(
                video_path=video_path,
                detector=detector,
                sample_interval=interval,
                classes_filter=classes_filter,
            )

            out_path = detect_dir / device / f"{video_path.stem}_detections.json"
            save_detections(detections, out_path)

            # Accumulate stats
            video_obj_count = sum(len(f.detections) for f in detections.frames)
            total_objects += video_obj_count
            for cls_name, count in detections.unique_classes.items():
                all_classes[cls_name] = all_classes.get(cls_name, 0) + count

            progress.advance(task)

    # Summary
    console.print()
    console.print(
        f"[bold green]Detection complete:[/bold green] {len(video_files)} videos processed"
    )
    console.print(f"  Total objects detected: {total_objects}")

    if all_classes:
        top_classes = sorted(all_classes.items(), key=lambda x: x[1], reverse=True)[:5]
        top_str = ", ".join(f"{name} ({count})" for name, count in top_classes)
        console.print(f"  Top classes: {top_str}")

    console.print(f"\n[bold]Output:[/bold] {detect_dir}")
    console.print()


@app.command("file")
def detect_single(
    input_file: Path = typer.Argument(..., help="Input video file"),
    model: str = typer.Option("yolov8n.pt", "--model", "-m", help="YOLO model weights file"),
    confidence: float = typer.Option(
        0.25, "--confidence", "-c", help="Minimum detection confidence (0-1)"
    ),
    interval: float = typer.Option(2.0, "--interval", "-i", help="Seconds between sampled frames"),
    classes: str | None = typer.Option(
        None, "--classes", help="Comma-separated class filter (e.g. 'car,person,truck')"
    ),
) -> None:
    """Run object detection on a single video file.

    Saves results as <stem>_detections.json next to the input file.

    Example:
        skyforge detect file video_norm.mp4
        skyforge detect file clip.mp4 --model yolov8s.pt --interval 1.0
        skyforge detect file clip.mp4 --classes car,person
    """
    if not input_file.exists():
        console.print(f"[red]Error:[/red] File '{input_file}' not found.")
        raise typer.Exit(1)

    if input_file.suffix.lower() not in VIDEO_EXTENSIONS:
        console.print(f"[red]Error:[/red] '{input_file.suffix}' is not a supported video format.")
        raise typer.Exit(1)

    # Lazy import
    try:
        from skyforge.core.detector import ObjectDetector, detect_video, save_detections
    except ImportError:
        console.print(_INSTALL_HINT)
        raise typer.Exit(1) from None

    try:
        detector = ObjectDetector(
            model_name=model,
            confidence=confidence,
            device=None,
        )
    except ImportError:
        console.print(_INSTALL_HINT)
        raise typer.Exit(1) from None

    classes_filter = [c.strip() for c in classes.split(",")] if classes else None

    console.print(f"\n[bold]Detecting objects in:[/bold] {input_file.name}")
    console.print(f"  Model: {model}, Confidence: {confidence}, Interval: {interval}s")
    if classes_filter:
        console.print(f"  Filter: {', '.join(classes_filter)}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Detecting {input_file.name}...", total=None)

        def on_progress(frame_idx: int, total: int, det_count: int) -> None:
            progress.update(
                task,
                total=total,
                completed=frame_idx,
                description=(
                    f"Frame {frame_idx}/{total} — {det_count} object{'s' if det_count != 1 else ''}"
                ),
            )

        detections = detect_video(
            video_path=input_file,
            detector=detector,
            sample_interval=interval,
            classes_filter=classes_filter,
            on_progress=on_progress,
        )

    out_path = input_file.parent / f"{input_file.stem}_detections.json"
    save_detections(detections, out_path)

    # Summary
    total_objects = sum(len(f.detections) for f in detections.frames)
    console.print()
    console.print(f"[bold green]Done:[/bold green] {out_path}")
    console.print(f"  Frames sampled: {detections.total_frames_sampled}")
    console.print(f"  Total objects:  {total_objects}")

    if detections.unique_classes:
        top = sorted(detections.unique_classes.items(), key=lambda x: x[1], reverse=True)
        top_str = ", ".join(f"{name} ({count})" for name, count in top[:5])
        console.print(f"  Classes: {top_str}")

    console.print()


@app.command("summary")
def summary(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
) -> None:
    """Show a summary of existing detection results for a project.

    Reads from 07_DETECTIONS/**/*_detections.json and displays a table
    with per-video statistics and a grand total.

    Example:
        skyforge detect summary
        skyforge detect summary "My Flight"
    """
    from skyforge.core.detector import load_detections

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ directory found).")
        raise typer.Exit(1)

    detect_dir = proj / "07_DETECTIONS"
    if not detect_dir.exists():
        console.print(
            "[yellow]No detection results found.[/yellow] Run `skyforge detect run` first."
        )
        raise typer.Exit(0)

    json_files = sorted(detect_dir.rglob("*_detections.json"))
    if not json_files:
        console.print("[yellow]No detection JSON files found in 07_DETECTIONS/.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Detection Summary")
    table.add_column("Source", style="cyan", max_width=40)
    table.add_column("Frames", justify="right")
    table.add_column("Objects", justify="right", style="green")
    table.add_column("Top Classes", style="dim")

    grand_frames = 0
    grand_objects = 0
    grand_classes: dict[str, int] = {}

    for jf in json_files:
        data = load_detections(jf)
        source = Path(data.get("source", jf.stem)).name
        frames_sampled = data.get("total_frames_sampled", 0)
        unique = data.get("unique_classes", {})

        total_objs = sum(unique.values())
        top_3 = sorted(unique.items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{n} ({c})" for n, c in top_3) if top_3 else "-"

        table.add_row(source, str(frames_sampled), str(total_objs), top_str)

        grand_frames += frames_sampled
        grand_objects += total_objs
        for cls_name, count in unique.items():
            grand_classes[cls_name] = grand_classes.get(cls_name, 0) + count

    # Grand total row
    grand_top = sorted(grand_classes.items(), key=lambda x: x[1], reverse=True)[:3]
    grand_top_str = ", ".join(f"{n} ({c})" for n, c in grand_top) if grand_top else "-"
    table.add_section()
    table.add_row(
        f"[bold]TOTAL ({len(json_files)} files)[/bold]",
        f"[bold]{grand_frames}[/bold]",
        f"[bold]{grand_objects}[/bold]",
        f"[bold]{grand_top_str}[/bold]",
    )

    console.print()
    console.print(table)
    console.print()
