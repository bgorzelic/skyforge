"""Skyforge configuration — FlightDeck API settings and local preferences."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".skyforge"
CONFIG_FILE = CONFIG_DIR / "config.toml"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.toml"


@dataclass
class SkyforgeConfig:
    """Skyforge configuration settings."""

    # FlightDeck API
    api_url: str = "http://localhost:8004"
    api_key: str = ""

    # Mode
    local_mode: bool = False  # If True, never try FlightDeck API

    # Local defaults
    default_project_dir: str = "."
    flights_dir: str = "flights"

    # Processing defaults
    target_fps: int = 30
    crf: int = 18
    proxy_crf: int = 28

    @property
    def is_configured(self) -> bool:
        """Check if FlightDeck API is configured."""
        return bool(self.api_url and self.api_key)


def load_config() -> SkyforgeConfig:
    """Load configuration from file and environment, returning merged config.

    Priority order (highest to lowest):
    1. Environment variables
    2. ~/.skyforge/credentials.toml (API key only)
    3. ~/.skyforge/config.toml
    4. Defaults
    """
    config = SkyforgeConfig()

    # Load from TOML config file if it exists
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)

        api = data.get("api", {})
        if "url" in api:
            config.api_url = api["url"]
        if "key" in api:
            config.api_key = api["key"]

        local = data.get("local", {})
        if "mode" in local:
            config.local_mode = local["mode"]
        if "default_project_dir" in local:
            config.default_project_dir = local["default_project_dir"]
        if "flights_dir" in local:
            config.flights_dir = local["flights_dir"]

        processing = data.get("processing", {})
        if "target_fps" in processing:
            config.target_fps = processing["target_fps"]
        if "crf" in processing:
            config.crf = processing["crf"]

    # Load credentials from separate file (overrides config.toml key)
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE, "rb") as f:
            creds = tomllib.load(f)
        if "api_key" in creds:
            config.api_key = creds["api_key"]

    # Environment overrides (highest priority)
    if url := os.environ.get("FLIGHTDECK_URL"):
        config.api_url = url
    if key := os.environ.get("FLIGHTDECK_API_KEY"):
        config.api_key = key
    if os.environ.get("SKYFORGE_LOCAL_MODE", "").lower() in ("1", "true", "yes"):
        config.local_mode = True

    return config


def save_config(config: SkyforgeConfig) -> None:
    """Save configuration to TOML file.

    Does not write the API key — use save_credentials() for that.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    content = f"""# Skyforge CLI Configuration

[api]
url = "{config.api_url}"

[local]
mode = {str(config.local_mode).lower()}
default_project_dir = "{config.default_project_dir}"
flights_dir = "{config.flights_dir}"

[processing]
target_fps = {config.target_fps}
crf = {config.crf}
"""
    CONFIG_FILE.write_text(content)


def save_credentials(api_key: str) -> None:
    """Save API credentials to a separate file with restricted permissions.

    Stored in ~/.skyforge/credentials.toml (chmod 600), separate from the
    main config so it is not accidentally committed or shared.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(f'api_key = "{api_key}"\n')
    CREDENTIALS_FILE.chmod(0o600)
