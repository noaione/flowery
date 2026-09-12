"""``flowery auth`` - sign in, sign out and inspect the stored session."""

from __future__ import annotations

import sys
import time

import click
from rich.table import Table

from ..client import FloweryClient
from ..config import UserConfig, config_path, save_config
from ..errors import AuthError
from .helpers import abort_on_error, console, coroutine, pass_config


@click.group(name="auth")
def auth_group() -> None:
    """Manage the stored account session."""


@auth_group.command(name="login")
@click.option("--email", "-e", default=None, help="Account email. Prompted when omitted.")
@click.option(
    "--password",
    "-p",
    default=None,
    help="Account password. Prompted (hidden) when omitted.",
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    help="Read 'email\\npassword' from stdin instead of prompting.",
)
@pass_config
@abort_on_error
@coroutine
async def login(
    config: UserConfig,
    email: str | None,
    password: str | None,
    from_stdin: bool,
) -> None:
    """Authenticate with your account and store the token.

    Credentials are only used for this single request; the resulting refresh
    token is written to the config file so later commands run unattended.
    """
    if from_stdin:
        lines = [line.rstrip("\r\n") for line in sys.stdin]
        if len(lines) < 2:
            raise click.ClickException("expected 'email' and 'password' on stdin")
        email = email or lines[0]
        password = password or lines[1]

    email = email or click.prompt("Email")
    password = password or click.prompt("Password", hide_input=True, confirmation_prompt=False)

    async with FloweryClient(config=config) as client:
        session = await client.login(email.strip(), password)
        config.session = session

    console.print(f"[green]✓[/] signed in as [bold]{session.display_name or session.email}[/]")
    console.print(f"[dim]session stored in {config_path()}[/]")


@auth_group.command(name="logout")
@pass_config
@abort_on_error
@coroutine
async def logout(config: UserConfig) -> None:
    """Forget the stored session."""
    async with FloweryClient(config=config) as client:
        await client.logout()
    console.print("[green]✓[/] signed out")


@auth_group.command(name="status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine readable output.")
@pass_config
@abort_on_error
def status(config: UserConfig, as_json: bool) -> None:
    """Show the stored session and its expiry."""
    session = config.session
    if as_json:
        import json

        click.echo(json.dumps(session.model_dump(mode="json"), indent=2))
        return

    table = Table(title="Session", show_header=False, box=None, title_justify="left")
    table.add_column("key", style="bold cyan", no_wrap=True)
    table.add_column("value")

    if not session.authenticated:
        console.print("[yellow]not signed in[/]")
        console.print("[dim]run [bold]flowery auth login[/] to authenticate[/]")
    else:
        table.add_row("email", session.email or "")
        table.add_row("display name", session.display_name or "")
        table.add_row("user id", session.user_id or "")
        if session.expires_at:
            remaining = int(session.expires_at - time.time())
            human = _humanise(remaining)
            state = "[green]valid[/]" if remaining > 0 else "[red]expired[/]"
            table.add_row("access token", state + f" (expires in {human})")
        table.add_row("refresh token", "present" if session.refresh_token else "missing")
        console.print(table)

    console.print(f"[dim]config: {config_path()}[/]")


def _humanise(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    bits: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            value, seconds = divmod(seconds, size)
            bits.append(f"{value}{unit}")
        if len(bits) == 2:
            break
    return " ".join(bits) or "0s"


@auth_group.command(name="refresh")
@pass_config
@abort_on_error
@coroutine
async def refresh(config: UserConfig) -> None:
    """Force a token refresh using the stored refresh token."""
    if not config.session.refresh_token:
        raise AuthError("no refresh token stored, run `flowery auth login` first")
    async with FloweryClient(config=config) as client:
        await client.refresh_session()
    save_config(config)
    console.print("[green]✓[/] token refreshed")


__all__ = ["auth_group"]
