"""``flowery video`` - download HLS video streams and their subtitles."""

from __future__ import annotations

from pathlib import Path

import click

from ..client import FloweryClient
from ..config import UserConfig
from ..download import download_hls, remove_temp, safe_name
from ..errors import FloweryError
from ..models import SeriesDetail, VideoEpisode, WorkType
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

__all__ = ["video"]

DEFAULT_KEY_PATH = "keys/{slug}-key.bin"


@click.command(name="video")
@click.argument("work_ref")
@click.option(
    "--episodes",
    "-e",
    "selection",
    default=None,
    help="Episodes to fetch, e.g. '1-3' or 'all'. Defaults to every accessible episode.",
)
@click.option("--subtitles/--no-subtitles", default=True, show_default=True)
@click.option(
    "--quality",
    "-q",
    default="best",
    show_default=True,
    help="HLS variant: best, worst, or a height such as 720p/1080p.",
)
@click.option(
    "--remux/--no-remux",
    default=True,
    show_default=True,
    help="Use ffmpeg to wrap the stream in MP4; otherwise keep the raw .ts.",
)
@click.option("--keep-segments", is_flag=True, help="Keep the decrypted segment directory.")
@click.option("--concurrency", "-j", type=click.IntRange(1, 16), default=6, show_default=True)
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
async def video(
    config: UserConfig,
    work_ref: str,
    selection: str | None,
    subtitles: bool,
    quality: str,
    remux: bool,
    keep_segments: bool,
    concurrency: int,
    output: Path | None,
    force: bool,
    list_only: bool,
) -> None:
    """Download the video WORK, decrypting the AES-128 HLS stream."""
    if output is not None:
        config.download_dir = str(output)

    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        require_type(work, WorkType.VIDEO, "video")
        series = await client.series(work)
        episodes = await client.video_episodes(work.id)

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
                "created_at": work.created_at,
                "author": (series.authors or {}).get("display_name"),
                "languages": series.languages,
                "total_episodes": series.total_episode_count,
            },
        )

        key = await _load_key(client, series, work.slug)
        by_number = {episode.episode_number: episode for episode in episodes}
        downloaded = 0

        for number in chosen:
            listing = by_number[number]
            label = unit_label(number, listing.slug)
            console.rule(f"[bold]{label} - {listing.label()}", style="magenta")

            # The listing endpoint always reports has_access=False for videos, so the
            # per-episode response is the only trustworthy source of truth.
            detail = await client.video_episode(work.id, number)
            if not detail.has_access or not detail.video_url:
                error_console.print(f"[yellow]skipping {label}:[/] no access (price {detail.price})")
                continue

            episode_dir = unit_dir(root, number, detail.slug)
            # Without remuxing the final artefact is a raw MPEG-TS stream, so the
            # existing-file check has to look for that extension instead.
            suffix = ".mp4" if remux else ".ts"
            target = episode_dir / f"{safe_name(detail.slug)}{suffix}"
            if force or not target.exists():
                try:
                    produced = await download_hls(
                        client,
                        detail.video_url,
                        target,
                        key,
                        quality=quality,
                        concurrency=concurrency,
                        console=console,
                        remux=remux,
                        keep_segments=keep_segments,
                    )
                except FloweryError as exc:
                    error_console.print(f"[red]✗[/] {label}: {exc}")
                    continue
                console.print(f"  [green]✓[/] video → [dim]{produced}[/]")
            else:
                console.print(f"  [dim]skipped existing video {target.name}[/]")

            if subtitles:
                files = await download_subtitles(
                    client,
                    detail.subtitle_url,
                    episode_dir,
                    safe_name(detail.slug),
                    skip_existing=not force,
                )
                if files:
                    console.print(f"  [green]✓[/] {len(files)} subtitle track(s)")
            remove_temp(episode_dir)
            downloaded += 1

        console.print(f"\n[bold green]done[/] - {downloaded} episode(s) under [dim]{root}[/]")


async def _load_key(client: FloweryClient, series: SeriesDetail, slug: str) -> bytes:
    """Fetch the AES-128 content key for a series."""
    url = series.key_file_url or client.public_url(DEFAULT_KEY_PATH.format(slug=slug))
    data = await client.fetch_bytes(url)
    if not data:
        raise FloweryError(f"empty HLS key returned by {url}")
    if len(data) != 16:
        raise FloweryError(f"unexpected HLS key length {len(data)} (want 16 bytes)")
    return data


def _print_episodes(title: str, episodes: list[VideoEpisode]) -> None:
    from rich.table import Table

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
