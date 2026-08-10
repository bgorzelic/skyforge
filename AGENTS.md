# Skyforge

## What this is

Skyforge is a Python 3.11+ command-line tool that organizes mixed-device aerial footage, normalizes video with FFmpeg, analyzes footage quality, selects useful segments, and exports clips and reports. It can run processing locally or use a configured FlightDeck server, with local fallback. (Sources: `README.md`, `pyproject.toml`, and `CLAUDE.md`, reviewed 2026-08-05.)

## Setup / install

FFmpeg and FFprobe are required. On macOS, the documented setup is:

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
skyforge version
```

Python 3.11 or newer is required. Optional feature groups are `ai`, `detect`, `vision`, `reports`, and `all`; install only the group needed, for example `pip install -e ".[detect]"`. (Sources: `README.md`, `pyproject.toml`, and `CLAUDE.md`, reviewed 2026-08-05.)

## Build / test / lint

No standalone build command is documented. The project uses Hatchling as its build backend.

The documented development checks are:

```bash
ruff check src/ --fix
ruff format src/
pytest --cov=skyforge --cov-report=term-missing
```

Pytest is configured to look in `tests/`, but no `tests/` directory was present in the reviewed checkout. Coverage is configured with an 80% minimum. (Sources: `pyproject.toml`, `CLAUDE.md`, and repository tree, reviewed 2026-08-05.)

## Code style / conventions

- Ruff targets Python 3.11 with a 100-character line length. Enabled lint groups are `E`, `F`, `I`, `N`, `W`, `UP`, `B`, `SIM`, and `RUF`; `B008` is ignored for Typer argument and option defaults.
- The package uses a `src/skyforge/` layout. Typer provides the CLI and Rich provides terminal output.
- Normalized files use `<original>_norm.mp4`, proxies use `<original>_proxy.mp4`, and exported segments use `<source>__seg###__<MM:SS>-<MM:SS>__<tags>.mp4`.
- The current documented media baseline is H.264, `yuv420p`, 30 fps constant frame rate, with HDR sources tonemapped to SDR.

(Sources: `pyproject.toml` and `CLAUDE.md`, reviewed 2026-08-05.)

## Working with multiple agents here

This repository can be worked on by multiple parallel Claude Code and Codex agents launched with this machine's `launch-agents` tool. Each agent receives its own git worktree automatically; do not create branches or worktrees manually for that workflow.

For a multi-agent task, check the shared coordination database for file claims before editing any file another agent may be touching. Claim work through the installed orchestration workflow and coordinate overlapping changes through its shared messaging facilities.

Recent repository history uses Conventional Commit-style subjects such as `feat:`, `fix:`, `docs:`, `style:`, `ci:`, and `chore:`; follow that convention. (Source: recent git commit subjects, reviewed 2026-08-05.)

Never commit secrets. The current `.gitignore` explicitly covers `.env` and `.env.local`, but it does not establish coverage for every possible credential filename. Confirm the exact environment or credential file is ignored before assuming it is safe, and extend ignore rules deliberately when needed. (Source: `.gitignore`, reviewed 2026-08-05.)
