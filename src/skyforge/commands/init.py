"""Init command — create new flight projects from a template."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from skyforge.core.project import create_project

app = typer.Typer()
console = Console()

DEFAULT_DEVICES = ["Drone", "iPhone", "Meta_Glasses"]


@app.command("new")
def new_project(
    name: str = typer.Argument(..., help="Project name (e.g., '2nd Flight', 'Downtown Survey')"),
    base_dir: Path = typer.Option(".", help="Parent directory for the project"),
    devices: Optional[list[str]] = typer.Option(
        None, "--device", "-d",
        help="Device directories to create (repeatable). Default: Drone, iPhone, Meta_Glasses",
    ),
):
    """Create a new flight project with standard directory structure.

    Example:
        skyforge init new "2nd Flight"
        skyforge init new "Bridge Survey" -d Drone -d GoPro -d iPhone
    """
    device_list = devices if devices else DEFAULT_DEVICES
    project_path = create_project(base_dir, name, device_list)

    console.print(f"\n[bold green]Project created:[/bold green] {project_path}")
    console.print("\n[bold]Structure:[/bold]")
    console.print(f"  {name}/")
    console.print("  ├── 01_RAW/")
    for d in device_list:
        console.print(f"  │   └── {d}/")
    console.print("  ├── 02_NORMALIZED/")
    console.print("  ├── 02_PROXIES/")
    console.print("  ├── 03_PROJECT/")
    console.print("  ├── 04_EXPORTS/")
    console.print("  └── project.json")
    console.print("\n[dim]Drop your raw footage into 01_RAW/<device>/ then run:[/dim]")
    console.print(f"  [cyan]skyforge ingest run {project_path}[/cyan]")
