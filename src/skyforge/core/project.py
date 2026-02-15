"""Project management — create and manage flight project directories."""

import json
from datetime import datetime
from pathlib import Path

# Standard project directory structure
PROJECT_DIRS = [
    "01_RAW",
    "02_NORMALIZED",
    "02_PROXIES",
    "03_PROJECT",
    "04_EXPORTS",
]


def create_project(base_dir: Path, name: str, devices: list[str] | None = None) -> Path:
    """Create a new flight project with standard directory structure.

    Args:
        base_dir: Parent directory for the project.
        name: Project name (e.g., '2nd Flight', 'Downtown Survey').
        devices: List of device subdirectories to create under 01_RAW.
                 Defaults to ['Drone', 'iPhone', 'Meta_Glasses'].

    Returns:
        Path to the created project directory.
    """
    if devices is None:
        devices = ["Drone", "iPhone", "Meta_Glasses"]

    project_dir = base_dir / name
    project_dir.mkdir(parents=True, exist_ok=True)

    for d in PROJECT_DIRS:
        (project_dir / d).mkdir(exist_ok=True)

    # Create device subdirectories under RAW, NORMALIZED, and PROXIES
    for device in devices:
        (project_dir / "01_RAW" / device).mkdir(exist_ok=True)
        (project_dir / "02_NORMALIZED" / device).mkdir(exist_ok=True)
        (project_dir / "02_PROXIES" / device).mkdir(exist_ok=True)

    # Write project metadata
    metadata = {
        "name": name,
        "created": datetime.now().isoformat(),
        "devices": devices,
        "status": "created",
    }
    meta_path = project_dir / "project.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps(metadata, indent=2))

    return project_dir


def load_project(project_dir: Path) -> dict:
    """Load project metadata from project.json."""
    meta_path = project_dir / "project.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return {"name": project_dir.name, "status": "unknown"}


def detect_project_dir(path: Path) -> Path | None:
    """Walk up from path to find a project root (contains 01_RAW/)."""
    current = path.resolve()
    for _ in range(10):
        if (current / "01_RAW").is_dir():
            return current
        if current == current.parent:
            break
        current = current.parent
    return None
