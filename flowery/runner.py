"""``flowery`` console entry point."""

from __future__ import annotations

from .cli.app import cli

__all__ = ["main"]


def main() -> None:
    """Run the command line interface."""
    cli(prog_name="flowery")
