# Skyforge

CLI tool for **AI Aerial Solutions** — manage aerial footage, process with AI/ML, and track flight data.

## Installation

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"
```

## Usage

```bash
# Show version
skyforge version

# Scan a directory for media files
skyforge ingest scan ./IPHONE

# List flight sessions
skyforge flights list

# Get flight session details
skyforge flights info "1st Flight"

# AI analysis (coming soon)
skyforge process analyze video.mov
```

## Commands

| Command | Description |
|---------|-------------|
| `skyforge ingest scan <dir>` | Scan directory for aerial footage |
| `skyforge ingest organize <src> <dest>` | Organize media files |
| `skyforge flights list` | List flight sessions |
| `skyforge flights info <name>` | Flight session details |
| `skyforge process analyze <file>` | AI analysis on media |
| `skyforge process extract-frames <video>` | Extract video frames |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=skyforge

# Lint and format
ruff check src/
ruff format src/
```

## Project Structure

```
├── src/skyforge/
│   ├── cli.py              # CLI entry point
│   ├── commands/
│   │   ├── ingest.py       # Media import & organization
│   │   ├── flights.py      # Flight session tracking
│   │   └── process.py      # AI/ML processing
│   ├── core/
│   │   └── media.py        # Media file handling
│   └── utils/
├── tests/
├── docs/
└── pyproject.toml
```

## License

MIT
