# Skyforge

**A command-line tool for managing drone footage from raw files to polished deliverables.**

Skyforge takes the chaos of multi-device aerial footage (DJI drones, iPhones, GoPros, Meta Ray-Ban glasses, Insta360 cameras) and turns it into an organized, analyzed, export-ready media library. It handles the tedious parts automatically: format normalization, quality analysis, segment selection, and report-ready exports with timecode burn-in.

## What It Does

Here's the typical workflow, in plain English:

1. **You fly** and capture footage from one or more devices
2. **You run `skyforge init`** to create an organized project folder
3. **You drop your raw files** into the `01_RAW/` folder
4. **You run `skyforge ingest run`** and Skyforge normalizes everything to a common baseline (same frame rate, same color space, same codec) so your footage plays nicely together in any editor
5. **You run `skyforge analyze run`** and Skyforge watches your footage frame-by-frame, scores it for quality, picks the best segments, and exports polished clips

No video editing skills required. No NLE software needed. Just your footage and a terminal.

## Quick Start

### Prerequisites

You need two things installed:

1. **Python 3.11 or newer** - [Download here](https://www.python.org/downloads/) or `brew install python` on Mac
2. **FFmpeg** - The engine that processes video. Install with `brew install ffmpeg` on Mac, or [download here](https://ffmpeg.org/download.html) for other platforms

To check if you have them:

```bash
python3 --version    # Should show 3.11 or higher
ffmpeg -version      # Should show version info, not "command not found"
```

### Install Skyforge

```bash
# Clone the repo
git clone https://github.com/bgorzelic/skyforge.git
cd skyforge

# Create a virtual environment (keeps things clean)
python3 -m venv .venv
source .venv/bin/activate    # On Mac/Linux
# .venv\Scripts\activate     # On Windows

# Install skyforge
pip install -e .
```

That's it. Test it works:

```bash
skyforge version
# Should print: skyforge v0.3.0
```

## Usage

### Step 1: Create a Project

Every flight session gets its own project folder:

```bash
skyforge init new "My First Flight"
```

This creates a folder structure like:

```
My First Flight/
├── 01_RAW/              # Put your raw footage here
│   ├── Drone/           # DJI drone footage
│   ├── iPhone/          # iPhone footage
│   └── Meta_Glasses/    # Smart glasses footage
├── 02_NORMALIZED/       # Skyforge puts processed video here
├── 02_PROXIES/          # Smaller editing copies
├── 03_PROJECT/          # Project workspace
├── 04_EXPORTS/          # Final deliverables
└── project.json         # Project metadata
```

You can customize the device folders:

```bash
skyforge init new "Bridge Survey" -d Drone -d GoPro -d iPhone
```

### Step 2: Add Your Footage

Copy or move your raw video files into the `01_RAW/` subfolders. Put drone footage in `Drone/`, phone footage in `iPhone/`, etc.

### Step 3: Scan (Optional)

Before processing, you can preview what Skyforge found:

```bash
skyforge ingest scan "My First Flight"
```

This shows you a summary of every video file: codec, resolution, frame rate, HDR status, and any issues detected (like variable frame rate).

### Step 4: Ingest & Normalize

```bash
skyforge ingest run "My First Flight"
```

This is where the magic starts. For each video file, Skyforge:

- **Detects the device** automatically from the filename
- **Fixes variable frame rate (VFR)** - phone footage is notorious for this and it causes sync issues in editors
- **Converts HDR to SDR** - drone HDR footage gets tonemapped so it looks correct on standard displays
- **Normalizes to a common format** - H.264, 30fps, consistent color space
- **Generates editing proxies** - smaller 1080p copies for faster editing

After this step, `02_NORMALIZED/` contains footage that's ready to work with, regardless of what device captured it.

### Step 5: Analyze & Export

```bash
skyforge analyze run "My First Flight"
```

This is the smart part. Skyforge:

1. **Watches every frame** and measures blur, brightness, contrast, and motion
2. **Detects scene changes** using AI scene detection
3. **Scores each segment** on a 0-1 confidence scale
4. **Tags segments** automatically (static_shot, slow_pan, establishing_shot, reveal_shot, etc.)
5. **Selects the best clips** based on quality thresholds
6. **Exports polished clips** at 1080p with burned-in timecodes

The results:

- `03_ANALYSIS/` - Detailed JSON reports with per-frame quality data
- `04_SELECTS/` - The trimmed best segments
- `05_EXPORTS/` - Report-ready clips with timecode and filename overlays (great for sharing with clients who don't have video software)

### Step 6: Transcode for Sharing (Optional)

Need smaller files for client review, social media, or cloud storage? Skyforge has HandBrake-style presets built in:

```bash
# See what presets are available
skyforge transcode presets

# Transcode all your normalized footage to 720p H.265 (great for web/social)
skyforge transcode run "My First Flight" --preset web

# Need max compatibility for a client who uses Windows Media Player?
skyforge transcode run "My First Flight" --preset review

# Transcode just one file
skyforge transcode file video_norm.mp4 --preset mobile

# Preview what would happen without actually doing it
skyforge transcode run "My First Flight" --preset web --dry-run
```

The built-in presets:

| Preset | What It Does | Typical Size Reduction |
|---|---|---|
| `web` | 720p H.265 — small files for social media and websites | 70-80% smaller |
| `review` | 1080p H.264 — plays everywhere, good for client review | 40-60% smaller |
| `archive` | Full resolution H.265 — long-term storage, saves space | 30-50% smaller |
| `mobile` | 480p H.264 — tiny files for phone preview | 85-95% smaller |

Output goes to `06_TRANSCODED/<preset>/` mirroring your device folder structure.

### Step 7: Parse Telemetry (Optional)

If your drone records SRT telemetry (DJI Avata, ATOM drones, etc.):

```bash
skyforge telemetry parse flight.SRT
skyforge telemetry parse flight.SRT -f gpx    # Export as GPX for Google Earth
skyforge telemetry parse flight.SRT -f kml    # Export as KML
```

This extracts GPS coordinates, altitude, speed, camera settings, and more from your flight data.

## All Commands

| Command | What It Does |
|---|---|
| `skyforge init new "Name"` | Create a new project folder |
| `skyforge ingest scan <dir>` | Preview media files without processing |
| `skyforge ingest run <dir>` | Normalize footage and create proxies |
| `skyforge analyze run <dir>` | Analyze quality, select segments, export clips |
| `skyforge analyze summary <dir>` | Show analysis results summary |
| `skyforge telemetry parse <file>` | Parse SRT telemetry to JSON/CSV/GPX/KML |
| `skyforge telemetry summary <file>` | Show flight telemetry summary |
| `skyforge telemetry parse-all <dir>` | Parse all SRT files in a project |
| `skyforge transcode presets` | Show available transcode presets |
| `skyforge transcode run <dir>` | Transcode all normalized footage |
| `skyforge transcode file <file>` | Transcode a single video file |
| `skyforge flights list` | List all flight projects in a directory |
| `skyforge flights info <dir>` | Show detailed info about a flight project |
| `skyforge version` | Show version |

Every command supports `--help` for detailed options:

```bash
skyforge ingest run --help
skyforge analyze run --help
```

## Options & Tuning

### Ingest Options

```bash
skyforge ingest run "My Flight" \
  --fps 60           # Keep 60fps instead of downsampling to 30
  --crf 16           # Higher quality (lower CRF = better, 18 is default)
  --skip-proxies     # Don't generate proxy files
  --dry-run          # Preview what would happen without processing
```

### Analyze Options

```bash
skyforge analyze run "My Flight" \
  --min-segment 3      # Minimum clip length in seconds (default: 5)
  --max-segment 30     # Maximum clip length in seconds (default: 25)
  --min-confidence 0.5 # Higher = pickier about quality (default: 0.3)
  --skip-export        # Analyze only, don't export clips
  --dry-run            # Show what would be selected without exporting
```

### Transcode Options

```bash
skyforge transcode run "My Flight" \
  --preset web       # Which preset: web, review, archive, mobile
  --dry-run          # Preview without transcoding
  --no-skip-existing # Re-transcode even if output already exists
```

## FlightDeck Integration (Advanced)

Skyforge can optionally connect to FlightDeck, a full-featured drone media processing platform. When connected, heavy processing happens on the server instead of your laptop.

### Setup

```bash
# Authenticate with FlightDeck
skyforge auth login --api-key YOUR_API_KEY

# Check connection
skyforge auth status
skyforge status health
```

### How It Works

When FlightDeck is configured:

- `skyforge ingest run` uploads footage to FlightDeck for server-side processing
- `skyforge analyze run` submits analysis jobs to FlightDeck
- `skyforge export deliverable <segment_id>` requests report-ready exports
- `skyforge status job <job_id>` checks processing progress

When FlightDeck is not available (offline, not configured), everything falls back to local processing automatically. You always get results either way.

### Force Local Mode

```bash
# Use --local flag on any command
skyforge ingest run "My Flight" --local

# Or set it globally
export SKYFORGE_LOCAL_MODE=true
```

### FlightDeck Commands

| Command | What It Does |
|---|---|
| `skyforge auth login` | Save your API key |
| `skyforge auth status` | Show connection info |
| `skyforge auth logout` | Remove stored credentials |
| `skyforge status job <id>` | Check a processing job |
| `skyforge status job <id> --watch` | Watch a job until it finishes |
| `skyforge status health` | Test FlightDeck connectivity |
| `skyforge export deliverable <id>` | Export a report-ready clip from FlightDeck |

### Configuration

Skyforge stores config in `~/.skyforge/`:

```toml
# ~/.skyforge/config.toml
[api]
url = "https://your-flightdeck-server.com"

[local]
mode = false
default_project_dir = "."
flights_dir = "flights"

[processing]
target_fps = 30
crf = 18
```

API keys are stored separately in `~/.skyforge/credentials.toml` with restricted file permissions (only you can read it).

Environment variables override everything:

| Variable | Purpose |
|---|---|
| `FLIGHTDECK_URL` | FlightDeck API URL |
| `FLIGHTDECK_API_KEY` | API authentication key |
| `SKYFORGE_LOCAL_MODE` | Set to `true` to disable API calls |

## Supported Devices

Skyforge automatically detects these devices from filename patterns:

| Device | Filename Pattern | Example |
|---|---|---|
| DJI Drone | `DJI_*`, `PTSC_*` | `DJI_0042.MP4`, `PTSC_0001.MOV` |
| iPhone | `IMG_*` | `IMG_1234.MOV` |
| GoPro | `GH*`, `GX*`, `GOPR*` | `GH010042.MP4` |
| Meta Ray-Ban | `PXL_*`, `META_*` | `PXL_20240101.MP4` |
| Insta360 | `INSP_*`, `VID_*_00_*` | `VID_20240101_00_001.insv` |
| Unknown | Anything else | Still works, just labeled "unknown" |

## Supported Formats

**Video:** `.mov`, `.mp4`, `.m4v`, `.avi`, `.mkv`, `.mts`, `.m2ts`

**Images:** `.jpg`, `.jpeg`, `.png`, `.dng`, `.raw`, `.tiff`, `.tif`, `.heic`, `.cr2`, `.arw`, `.nef`

**Telemetry:** `.srt` (DJI/ATOM format)

## How the Quality Analysis Works

If you're curious about what's happening under the hood:

### Frame Analysis

Every N seconds (default: 1), Skyforge grabs a frame and measures:

- **Blur** - Uses the Laplacian variance method. A sharp frame scores high; a blurry frame (motion blur, out of focus) scores low.
- **Brightness** - Average pixel intensity. Too dark or too bright gets a penalty.
- **Contrast** - Standard deviation of pixel values. Flat, washed-out footage scores low.
- **Motion** - Difference between consecutive frames. Some motion is good (cinematic movement); too much is bad (jerky footage).

### Segment Scoring

Each frame gets a quality score from 0 to 1:

| Factor | Effect |
|---|---|
| Blurry frame | -0.5 penalty |
| Very dark | -0.6 penalty |
| Dim | -0.2 penalty |
| Overexposed | -0.4 penalty |
| Low contrast | -0.5 penalty |
| Smooth motion | +0.1 bonus |
| Excessive motion | -0.2 penalty |
| Good exposure | +0.1 bonus |

Consecutive high-scoring frames get merged into segments. Segments are split at scene changes and must meet minimum/maximum duration requirements.

### Automatic Tagging

Each segment is automatically classified:

| Tag | Meaning |
|---|---|
| `static_shot` | Camera barely moving (tripod or hover) |
| `slow_pan` | Gentle camera movement |
| `fast_motion` | Quick movement or action |
| `establishing_shot` | Wide shot at start of footage |
| `reveal_shot` | Camera moving to reveal a subject |
| `high_quality` | Above 80% confidence score |
| `good_exposure` | Well-lit footage |
| `low_light` | Darker conditions |

## Project Structure (for developers)

```
src/skyforge/
├── cli.py              # Main entry point
├── client.py           # FlightDeck API client
├── config.py           # Configuration management
├── commands/           # CLI commands (thin wrappers)
│   ├── init.py         # Project creation
│   ├── ingest.py       # Scan + normalize + proxy
│   ├── analyze.py      # Quality analysis + selection + export
│   ├── telemetry.py    # SRT telemetry parsing
│   ├── flights.py      # Flight project listing
│   ├── export.py       # FlightDeck deliverable export
│   ├── status.py       # Job status checking
│   └── auth.py         # API authentication
└── core/               # Business logic (no CLI dependencies)
    ├── media.py         # File detection, ffprobe, device ID
    ├── pipeline.py      # Normalization pipeline (FFmpeg)
    ├── analyzer.py      # Frame-level quality analysis (OpenCV)
    ├── selector.py      # Segment scoring and selection
    ├── exporter.py      # FFmpeg trimming and burn-in
    ├── telemetry.py     # SRT parsing and GPS export
    └── project.py       # Project folder management
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src/ --fix
ruff format src/

# Run tests
pytest --cov=skyforge --cov-report=term-missing
```

## License

MIT

## Author

[AI Aerial Solutions](https://aiaerialsolutions.com)
