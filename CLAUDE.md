# Horizon by Yosemite — Skyforge CLI

## Project Overview
Aerial media pipeline CLI for managing drone footage. Ingests multi-device footage (DJI drones, iPhone, Meta Ray-Ban glasses, GoPro, Insta360), normalizes to a common baseline, performs AI-driven quality analysis, selects best segments, and exports production-ready clips.

## Tech Stack
- **Language:** Python 3.11+
- **CLI Framework:** Typer + Rich
- **Media Processing:** FFmpeg/FFprobe (subprocess), OpenCV (headless)
- **Scene Detection:** PySceneDetect
- **Build:** Hatchling
- **Linting:** Ruff (line-length 100)
- **Testing:** Pytest + pytest-cov (80% target)
- **Package:** `src/skyforge/` layout

## Architecture
```
src/skyforge/
├── cli.py              # Typer app entry point
├── commands/           # CLI command modules (init, ingest, analyze, telemetry, flights, process)
├── core/               # Business logic
│   ├── media.py        # MediaInfo, probe_file, scan_directory, device detection
│   ├── pipeline.py     # Ingest pipeline: normalize, HDR tonemap, CFR, proxy generation
│   ├── project.py      # Flight project creation and directory structure
│   ├── analyzer.py     # Frame-level quality analysis (blur, brightness, contrast, motion)
│   ├── selector.py     # Segment selection algorithm with confidence scoring
│   ├── exporter.py     # FFmpeg segment trimming and report-ready export
│   └── telemetry.py    # SRT telemetry parsing (GPS, camera settings, flight data)
└── utils/              # Shared utilities
```

## Flight Project Structure
```
Flight_Name/
├── 01_RAW/             # Original footage by device
├── 02_NORMALIZED/      # H.264, 30fps, SDR, CFR baseline
├── 02_PROXIES/         # 1080p editing proxies
├── 03_ANALYSIS/        # Frame analysis JSONs, contact sheets, keyframes
├── 04_SELECTS/         # Selected segment metadata
├── 05_EXPORTS/         # Report-ready 1080p clips with timecode burn
├── 05_TELEMETRY/       # Parsed telemetry (JSON, CSV, GPX, KML)
└── project.json        # Project metadata
```

## Key Conventions
- Normalized files: `<original>_norm.mp4`
- Proxy files: `<original>_proxy.mp4`
- Exported segments: `<source>__seg###__<MM:SS>-<MM:SS>__<tags>.mp4`
- Device detection from filename patterns (PTSC_* = drone, IMG_* = iPhone)
- All video outputs: H.264, yuv420p, 30fps CFR
- HDR sources are tonemapped to SDR (Hable via zscale)

## Commands
```bash
skyforge init <name>          # Create new flight project
skyforge ingest scan <dir>    # Scan and probe media files
skyforge ingest run <dir>     # Full ingest pipeline
skyforge analyze run <dir>    # Analyze → Select → Export pipeline
skyforge telemetry parse      # Parse SRT telemetry data
skyforge flights list         # List all flight projects
```

## Development
```bash
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src/ --fix
ruff format src/
pytest --cov=skyforge --cov-report=term-missing
```

## Important Notes
- Large media files (.MOV, .mp4) are gitignored — only metadata/JSON tracked
- FFmpeg and FFprobe must be installed (`brew install ffmpeg`)
- Flight data lives in `flights/` subdirectory
- Shell script `ingest_videos.sh` is legacy — prefer `skyforge ingest run`
