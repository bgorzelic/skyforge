"""Vision command — AI-powered aerial image analysis using LLM vision APIs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from skyforge.core.media import VIDEO_EXTENSIONS
from skyforge.core.project import detect_project_dir

app = typer.Typer()
console = Console()


@app.command("profiles")
def list_profiles() -> None:
    """List available vision analysis profiles."""
    from skyforge.core.vision import ANALYSIS_PROFILES

    table = Table(title="Vision Analysis Profiles")
    table.add_column("Key", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description", max_width=60)
    table.add_column("Categories", style="yellow", max_width=40)

    for key, cfg in ANALYSIS_PROFILES.items():
        prompt_preview = cfg["prompt"][:60] + ("..." if len(cfg["prompt"]) > 60 else "")
        categories = ", ".join(cfg["categories"])
        table.add_row(key, cfg["name"], prompt_preview, categories)

    console.print(table)


@app.command("run")
def run(
    project_dir: Path = typer.Argument(".", help="Flight project directory"),
    profile: str = typer.Option("general", "--profile", "-p", help="Analysis profile"),
    provider: str = typer.Option("claude", "--provider", help="LLM provider (claude or openai)"),
    interval: float = typer.Option(5.0, "--interval", "-i", help="Frame sample interval (seconds)"),
    max_frames: int = typer.Option(20, "--max-frames", help="Max frames to analyze per video"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estimate cost without calling APIs"),
) -> None:
    """Run AI vision analysis on normalized flight footage.

    Samples frames from each normalized video and sends them to an LLM
    vision API for inspection. Results are saved as JSON in 08_VISION/.
    """
    try:
        from skyforge.core.vision import (
            ANALYSIS_PROFILES,
            analyze_video,
            estimate_cost,
            save_vision_report,
        )
    except ImportError:
        console.print(
            "[red]Error:[/red] Vision dependencies not installed.\n"
            "Install with: [bold]pip install skyforge\\[vision][/bold]"
        )
        raise typer.Exit(1) from None

    if profile not in ANALYSIS_PROFILES:
        console.print(f"[red]Error:[/red] Unknown profile '{profile}'.")
        console.print(f"Available: {', '.join(ANALYSIS_PROFILES)}")
        raise typer.Exit(1)

    proj = detect_project_dir(project_dir)
    if not proj:
        console.print("[red]Error:[/red] Not a skyforge project (no 01_RAW/ found).")
        raise typer.Exit(1)

    norm_dir = proj / "02_NORMALIZED"
    if not norm_dir.exists():
        console.print(
            "[red]Error:[/red] No 02_NORMALIZED/ directory. Run `skyforge ingest run` first."
        )
        raise typer.Exit(1)

    # Collect normalized videos grouped by device
    videos: list[tuple[str, Path]] = []
    for device_dir in sorted(norm_dir.iterdir()):
        if not device_dir.is_dir():
            continue
        for f in sorted(device_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append((device_dir.name, f))

    if not videos:
        console.print("[yellow]No normalized videos found.[/yellow]")
        raise typer.Exit(0)

    profile_cfg = ANALYSIS_PROFILES[profile]
    console.print("\n[bold]Skyforge AI Vision Analysis[/bold]")
    console.print(f"  Project:    {proj}")
    console.print(f"  Profile:    {profile_cfg['name']}")
    console.print(f"  Provider:   {provider}")
    console.print(f"  Interval:   {interval}s")
    console.print(f"  Max frames: {max_frames}")
    console.print(f"  Videos:     {len(videos)}")
    console.print()

    # -- Dry run: estimate costs --
    if dry_run:
        cost_table = Table(title="Cost Estimate (dry run)")
        cost_table.add_column("Device", style="cyan")
        cost_table.add_column("Video", max_width=30)
        cost_table.add_column("Duration", justify="right")
        cost_table.add_column("Frames", justify="right")
        cost_table.add_column("Est. Cost", justify="right", style="yellow")

        total_cost = 0.0
        total_frames_est = 0

        for device, video in videos:
            est = estimate_cost(
                video, sample_interval=interval, max_frames=max_frames, provider=provider
            )
            total_cost += est["estimated_cost_usd"]
            total_frames_est += est["frame_count"]
            dur_str = f"{est['duration_s']:.0f}s"
            cost_table.add_row(
                device,
                video.name,
                dur_str,
                str(est["frame_count"]),
                f"${est['estimated_cost_usd']:.3f}",
            )

        console.print(cost_table)
        console.print(
            f"\n[bold]Total:[/bold] {total_frames_est} frames, ~${total_cost:.2f} estimated cost"
        )
        console.print("[yellow]Dry run complete. No API calls made.[/yellow]")
        return

    # -- Actual analysis --
    vision_dir = proj / "08_VISION"
    total_findings_all = 0
    total_frames_all = 0
    severity_totals: dict[str, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(videos))

        for device, video in videos:
            progress.update(task, description=f"[cyan]{device}[/cyan] {video.name}")

            def _make_progress_cb(dev: str, vid: Path) -> Callable[[int, int, int], None]:
                def _cb(current: int, total: int, findings: int) -> None:
                    progress.update(
                        task,
                        description=(
                            f"[cyan]{dev}[/cyan] {vid.name}"
                            f" frame {current}/{total} ({findings} findings)"
                        ),
                    )

                return _cb

            progress_cb = _make_progress_cb(device, video)

            try:
                report = analyze_video(
                    video,
                    profile=profile,
                    provider=provider,
                    sample_interval=interval,
                    max_frames=max_frames,
                    on_progress=progress_cb,
                )
            except ImportError as exc:
                pkg = "anthropic" if provider == "claude" else "openai"
                console.print(
                    f"\n[red]Error:[/red] Missing package '{pkg}': {exc}\n"
                    f"Install with: [bold]pip install {pkg}[/bold]"
                )
                raise typer.Exit(1) from None
            except ValueError as exc:
                console.print(f"\n[red]Configuration error:[/red] {exc}")
                raise typer.Exit(1) from None
            except RuntimeError as exc:
                console.print(f"\n[red]Error analyzing {video.name}:[/red] {exc}")
                progress.advance(task)
                continue

            # Save report
            output_path = vision_dir / device / f"{video.stem}_vision.json"
            save_vision_report(report, output_path)

            total_frames_all += report.total_frames_analyzed
            for sev, count in report.summary.items():
                severity_totals[sev] = severity_totals.get(sev, 0) + count
                total_findings_all += count

            progress.advance(task)

    # -- Summary --
    console.print()
    console.print("[bold green]Vision analysis complete![/bold green]")
    console.print(f"  Output:   {vision_dir}")
    console.print(f"  Frames:   {total_frames_all} analyzed")
    console.print(f"  Findings: {total_findings_all} total")

    if severity_totals:
        sev_table = Table(title="Findings by Severity")
        sev_table.add_column("Severity", style="bold")
        sev_table.add_column("Count", justify="right")

        severity_order = ["critical", "high", "medium", "low", "info"]
        severity_styles = {
            "critical": "bold red",
            "high": "red",
            "medium": "yellow",
            "low": "cyan",
            "info": "dim",
        }
        for sev in severity_order:
            if sev in severity_totals:
                sev_table.add_row(
                    f"[{severity_styles.get(sev, '')}]{sev}[/]",
                    str(severity_totals[sev]),
                )

        console.print(sev_table)

    console.print()


@app.command("file")
def analyze_file(
    video_file: Path = typer.Argument(..., help="Path to a video file"),
    profile: str = typer.Option("general", "--profile", "-p", help="Analysis profile"),
    provider: str = typer.Option("claude", "--provider", help="LLM provider (claude or openai)"),
    interval: float = typer.Option(5.0, "--interval", "-i", help="Frame sample interval (seconds)"),
    max_frames: int = typer.Option(20, "--max-frames", help="Max frames to analyze"),
) -> None:
    """Analyze a single video file with AI vision.

    Results are saved as JSON next to the input file.
    """
    try:
        from skyforge.core.vision import (
            ANALYSIS_PROFILES,
            analyze_video,
            save_vision_report,
        )
    except ImportError:
        console.print(
            "[red]Error:[/red] Vision dependencies not installed.\n"
            "Install with: [bold]pip install skyforge\\[vision][/bold]"
        )
        raise typer.Exit(1) from None

    if not video_file.exists():
        console.print(f"[red]Error:[/red] File not found: {video_file}")
        raise typer.Exit(1)

    if profile not in ANALYSIS_PROFILES:
        console.print(f"[red]Error:[/red] Unknown profile '{profile}'.")
        console.print(f"Available: {', '.join(ANALYSIS_PROFILES)}")
        raise typer.Exit(1)

    profile_cfg = ANALYSIS_PROFILES[profile]
    console.print("\n[bold]Skyforge AI Vision — Single File[/bold]")
    console.print(f"  File:     {video_file}")
    console.print(f"  Profile:  {profile_cfg['name']}")
    console.print(f"  Provider: {provider}")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=max_frames)

        def _on_progress(current: int, total: int, findings: int) -> None:
            progress.update(
                task,
                total=total,
                completed=current,
                description=f"Frame {current}/{total} ({findings} findings)",
            )

        try:
            report = analyze_video(
                video_file,
                profile=profile,
                provider=provider,
                sample_interval=interval,
                max_frames=max_frames,
                on_progress=_on_progress,
            )
        except ImportError as exc:
            pkg = "anthropic" if provider == "claude" else "openai"
            console.print(
                f"\n[red]Error:[/red] Missing package '{pkg}': {exc}\n"
                f"Install with: [bold]pip install {pkg}[/bold]"
            )
            raise typer.Exit(1) from None
        except ValueError as exc:
            console.print(f"\n[red]Error:[/red] {exc}")
            raise typer.Exit(1) from None

    # Save alongside input file
    output_path = video_file.parent / f"{video_file.stem}_vision.json"
    save_vision_report(report, output_path)

    console.print(f"\n[green]Report saved:[/green] {output_path}")
    console.print(f"  Frames analyzed: {report.total_frames_analyzed}")

    total_findings = sum(len(f.findings) for f in report.frames)
    console.print(f"  Total findings:  {total_findings}")

    if report.summary:
        sev_table = Table(title="Findings by Severity")
        sev_table.add_column("Severity", style="bold")
        sev_table.add_column("Count", justify="right")

        severity_order = ["critical", "high", "medium", "low", "info"]
        severity_styles = {
            "critical": "bold red",
            "high": "red",
            "medium": "yellow",
            "low": "cyan",
            "info": "dim",
        }
        for sev in severity_order:
            if sev in report.summary:
                sev_table.add_row(
                    f"[{severity_styles.get(sev, '')}]{sev}[/]",
                    str(report.summary[sev]),
                )

        console.print(sev_table)

    console.print()
