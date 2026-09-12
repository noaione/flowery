"""``flowery audio`` - download audiobook/drama episodes with subtitles."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from ..client import FloweryClient
from ..config import UserConfig
from ..download import FileJob, fetch_many, remove_temp, safe_name
from ..errors import FloweryError
from ..models import AudioEpisode, WorkType
from .helpers import (
    abort_on_error,
    console,
    coroutine,
    download_subtitles,
    error_console,
    parse_selection,
    pass_config,
    require_type,
    unit_dir,
    unit_label,
    work_dir,
    write_metadata,
)

__all__ = ["audio"]


@click.command(name="audio")
@click.argument("work_ref")
@click.option(
    "--episodes",
    "-e",
    "selection",
    default=None,
    help="Episodes to fetch, e.g. '1-3' or 'all'. Defaults to every accessible episode.",
)
@click.option("--subtitles/--no-subtitles", default=True, show_default=True)
@click.option("--concurrency", "-j", type=click.IntRange(1, 16), default=4, show_default=True)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output root. Defaults to ./DOWNLOADS.",
)
@click.option("--force", is_flag=True, help="Re-download media that already exists.")
@click.option("--list", "list_only", is_flag=True, help="Only list episodes, download nothing.")
@pass_config
@abort_on_error
@coroutine
async def audio(
    config: UserConfig,
    work_ref: str,
    selection: str | None,
    subtitles: bool,
    concurrency: int,
    output: Path | None,
    force: bool,
    list_only: bool,
) -> None:
    """Download the audio WORK as MP3 files."""
    if output is not None:
        config.download_dir = str(output)

    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        require_type(work, WorkType.AUDIO, "audiobook")
        series = await client.series(work)
        episodes = await client.audio_episodes(work.id)

        if list_only:
            _print_episodes(work.title, episodes)
            return

        chosen = parse_selection(selection, [e.episode_number for e in episodes], label="episode")
        root = work_dir(config, work)
        write_metadata(
            root,
            {
                "title": work.title,
                "slug": work.slug,
                "id": work.id,
                "type": work.work_type.value,
                "author": (series.authors or {}).get("display_name"),
                "languages": series.languages,
                "total_episodes": series.total_episode_count,
            },
        )

        by_number = {episode.episode_number: episode for episode in episodes}
        downloaded = 0

        for number in chosen:
            listing = by_number[number]
            label = unit_label(number, listing.slug)
            console.rule(f"[bold]{label} - {listing.label()}", style="blue")

            # Trust the per-episode response: the listing endpoint is unreliable here.
            detail = await client.audio_episode(work.id, number)
            if not detail.has_access or not detail.audio_url:
                error_console.print(f"[yellow]skipping {label}:[/] no access (price {detail.price})")
                continue

            episode_dir = unit_dir(root, number, detail.slug)
            stem = safe_name(detail.slug)
            audio_path = episode_dir / f"{stem}{Path(detail.audio_url).suffix or '.mp3'}"

            if force or not audio_path.exists() or audio_path.stat().st_size == 0:
                try:
                    signed = await client.sign("audios", [detail.audio_url])
                except FloweryError as exc:
                    error_console.print(f"[red]✗[/] {label}: could not sign URL ({exc})")
                    continue
                url = signed.get(detail.audio_url)
                if not url:
                    error_console.print(f"[red]✗[/] {label}: no signed URL returned")
                    continue
                report = await fetch_many(
                    client,
                    [FileJob(url=url, dest=audio_path, label=f"{stem}.mp3")],
                    concurrency=1,
                    description=f"{label} audio",
                    console=console,
                    skip_existing=False,
                )
                if report.failed:
                    error_console.print(f"[red]✗[/] {label}: {report.failed[0][1]}")
                    continue
                console.print(f"  [green]✓[/] audio → [dim]{audio_path}[/]")
            else:
                console.print(f"  [dim]skipped existing audio {audio_path.name}[/]")

            if subtitles:
                files = await download_subtitles(
                    client,
                    detail.subtitle_url,
                    episode_dir,
                    stem,
                    skip_existing=not force,
                )
                if files:
                    console.print(f"  [green]✓[/] {len(files)} subtitle track(s)")
            remove_temp(episode_dir)
            downloaded += 1

        console.print(f"\n[bold green]done[/] - {downloaded} episode(s) under [dim]{root}[/]")


def _print_episodes(title: str, episodes: list[AudioEpisode]) -> None:
    table = Table(title=f"{title} - episodes", header_style="bold cyan", title_justify="left")
    table.add_column("#", justify="right", style="green")
    table.add_column("Title")
    table.add_column("Duration", justify="right")
    table.add_column("Subtitles")
    table.add_column("Access", justify="center")
    for episode in episodes:
        duration = int(episode.duration or 0)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        table.add_row(
            str(episode.episode_number),
            episode.label(),
            f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}",
            ", ".join((episode.subtitle_url or {}).keys()),
            "[green]free[/]" if episode.is_free else "[yellow]paid[/]",
        )
    console.print(table)
