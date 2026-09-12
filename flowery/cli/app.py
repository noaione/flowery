"""Root command group wiring the individual subcommands together."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from .._metadata import __name__, __version__
from ..client import FloweryClient
from ..config import UserConfig, load_config
from .audio import audio
from .auth import auth_group
from .browse import calendar, info, list_units, works
from .helpers import abort_on_error, configure_logging
from .manhua import manhua
from .novel import novel
from .video import video

__all__ = ["cli"]

_HANDLERS: dict[str, click.Command] = {
    "manhua": manhua,
    "novel": novel,
    "video": video,
    "audio": audio,
}


class SuggestCommandGroup(click.Group):
    """Turns a bare slug into a helpful hint about the ``download`` shortcut."""

    def resolve_command(self, ctx: click.Context, args: list[str]) -> tuple:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and not args[0].startswith("-"):
                click.echo(
                    f"\nHint: to download '{args[0]}' run: flowery download {args[0]}",
                    err=True,
                )
            raise


@click.group(
    cls=SuggestCommandGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, prog_name=__name__, message="%(prog)s %(version)s")
@click.option("--verbose", "-v", count=True, help="Increase log verbosity.")
@click.pass_context
def cli(ctx: click.Context, verbose: int) -> None:
    """Download content from the site.

    Sign in once with `flowery auth login`, then browse with `flowery works`
    and download with `flowery <type> <slug>` (or `flowery download <slug>`).
    """
    ctx.obj = load_config()
    configure_logging(verbose)


for _command in (auth_group, works, info, list_units, calendar, manhua, novel, video, audio):
    cli.add_command(_command)


@cli.command(name="download")
@click.argument("work_ref")
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output root. Defaults to ./DOWNLOADS.",
)
@click.pass_obj
@abort_on_error
def download(config: UserConfig, work_ref: str, output: Path | None) -> None:
    """Download WORK, detecting the media type automatically."""

    async def _detect() -> str:
        async with FloweryClient(config=config) as client:
            work = await client.find_work(work_ref)
            return work.work_type.value

    work_type = asyncio.run(_detect())
    kwargs: dict[str, object] = {"work_ref": work_ref}
    if output is not None:
        kwargs["output"] = output
    click.get_current_context().invoke(_HANDLERS[work_type], **kwargs)
