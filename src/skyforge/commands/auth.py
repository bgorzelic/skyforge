"""Auth command — authenticate with FlightDeck API."""

import typer
from rich.console import Console

from skyforge.config import CREDENTIALS_FILE, load_config, save_credentials

app = typer.Typer()
console = Console()


@app.command("login")
def login(
    api_key: str = typer.Option(None, "--api-key", "-k", help="FlightDeck API key"),
) -> None:
    """Authenticate with FlightDeck using an API key.

    The key is stored in ~/.skyforge/credentials.toml with chmod 600 permissions,
    separate from the main config file.

    Example:
        skyforge auth login
        skyforge auth login --api-key sk-abc123
    """
    if not api_key:
        api_key = typer.prompt("Enter your FlightDeck API key", hide_input=True)

    if not api_key.strip():
        console.print("[red]Error:[/red] API key cannot be empty.")
        raise typer.Exit(1)

    save_credentials(api_key.strip())
    console.print(f"[green]API key saved[/green] to {CREDENTIALS_FILE}")

    # Verify the connection with the newly saved key
    from skyforge.client import FlightDeckClient

    config = load_config()
    config.api_key = api_key.strip()

    with FlightDeckClient(config) as client:
        if client.health_check():
            console.print(f"[green]Connected[/green] to FlightDeck at {config.api_url}")
        else:
            console.print(
                f"[yellow]Warning:[/yellow] Could not reach FlightDeck at {config.api_url}"
            )
            console.print(
                "[dim]The key is saved"
                " - connection may work once the server is running.[/dim]"
            )


@app.command("status")
def auth_status() -> None:
    """Show current authentication and connection status.

    Example:
        skyforge auth status
    """
    config = load_config()

    console.print(f"API URL:  {config.api_url}")

    if config.api_key:
        key = config.api_key
        masked = (key[:4] + "..." + key[-4:]) if len(key) > 8 else "***"
        console.print(f"API Key:  {masked}")
    else:
        console.print("API Key:  [yellow]not set[/yellow]")

    console.print(f"Local:    {'yes' if config.local_mode else 'no'}")

    if config.is_configured:
        from skyforge.client import FlightDeckClient

        with FlightDeckClient(config) as client:
            if client.health_check():
                console.print("Health:   [green]connected[/green]")
            else:
                console.print("Health:   [red]unreachable[/red]")
    else:
        console.print("Health:   [dim]not configured — run: skyforge auth login[/dim]")


@app.command("logout")
def logout() -> None:
    """Remove stored API credentials.

    Example:
        skyforge auth logout
    """
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
        console.print("[green]Credentials removed.[/green]")
    else:
        console.print("[dim]No credentials stored.[/dim]")
