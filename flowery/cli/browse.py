"""``flowery works`` / ``info`` / ``list`` / ``calendar`` - read-only browsing."""

from __future__ import annotations

import json as jsonlib
from datetime import datetime, timezone
from typing import Any

import click
from rich.table import Table

from ..client import FloweryClient
from ..config import UserConfig
from ..htmltext import html_to_text
from ..models import Work, WorkType
from .helpers import (
    TYPE_ICONS,
    abort_on_error,
    console,
    coroutine,
    pass_config,
    print_chapters_table,
    print_kv,
    print_works_table,
)

__all__ = ["TYPE_CHOICE", "calendar", "info", "list_units", "works"]

TYPE_CHOICE = click.Choice([t.value for t in WorkType] + ["all"], case_sensitive=False)


@click.command(name="works")
@click.option("--type", "-t", "work_type", type=TYPE_CHOICE, default="all", show_default=True)
@click.option("--search", "-s", "term", default=None, help="Filter by title or slug.")
@click.option("--free", "free_only", is_flag=True, help="Only show free works.")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON.")
@pass_config
@abort_on_error
@coroutine
async def works(
    config: UserConfig,
    work_type: str,
    term: str | None,
    free_only: bool,
    as_json: bool,
) -> None:
    """List every available work."""
    async with FloweryClient(config=config) as client:
        items = await client.list_works()

    if work_type.lower() != "all":
        wanted = WorkType(work_type.lower())
        items = [work for work in items if work.work_type is wanted]
    if term:
        needle = term.lower()
        items = [work for work in items if needle in work.title.lower() or needle in work.slug]
    if free_only:
        items = [work for work in items if work.is_free]

    if as_json:
        click.echo(jsonlib.dumps([w.model_dump(mode="json") for w in items], indent=2))
        return
    print_works_table(items, title="Available works")


@click.command(name="info")
@click.argument("work_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON.")
@pass_config
@abort_on_error
@coroutine
async def info(config: UserConfig, work_ref: str, as_json: bool) -> None:
    """Show metadata for a single WORK (slug, id or a unique title fragment)."""
    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        payload = await _describe(client, work)

    if as_json:
        click.echo(jsonlib.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    print_kv(
        [
            ("title", work.title),
            ("slug", work.slug),
            ("type", f"{TYPE_ICONS.get(work.work_type, '')} {work.work_type.value}".strip()),
            ("id", work.id),
            ("free", "yes" if work.is_free else "no"),
            *[(k, v) for k, v in payload.items() if k not in {"title", "slug", "type", "id", "free"}],
        ],
        title=f"{work.title}",
    )


async def _describe(client: FloweryClient, work: Work) -> dict[str, object]:
    """Collect type specific metadata for ``info``."""
    if work.work_type is WorkType.MANHUA:
        meta = await client.manhua_translation(work.id)
        return {
            "original title": (meta.get("manhuas") or {}).get("title"),
            "translator": (meta.get("translators") or {}).get("display_name"),
            "chapters": meta.get("total_sections"),
            "update status": meta.get("update_status"),
            "schedule": meta.get("update_details"),
            "views": meta.get("view_count"),
            "description": html_to_text(meta.get("description") or "", markdown=False),
        }
    if work.work_type is WorkType.NOVEL:
        meta = await client.novel_translation(work.id)
        words = meta.get("total_word_count") or 0
        return {
            "original title": (meta.get("novels") or {}).get("title"),
            "translator": (meta.get("translators") or {}).get("display_name"),
            "chapters": f"{meta.get('total_chapters') or '?'}",
            "words": f"{words:,}" if words else None,
            "update status": meta.get("update_status"),
            "views": meta.get("view_count"),
            "description": html_to_text(meta.get("description") or "", markdown=False),
        }
    series = await client.series(work)
    episodes = (
        await client.video_episodes(work.id)
        if work.work_type is WorkType.VIDEO
        else await client.audio_episodes(work.id)
    )
    languages = series.languages or _subtitle_languages(episodes)
    return {
        "author": (series.authors or {}).get("display_name"),
        "episodes": series.total_episode_count or len(episodes),
        "subtitles": ", ".join(languages) if languages else None,
        "update status": series.update_status,
        "views": series.view_count,
        "description": html_to_text((series.description or {}).get("en") or "", markdown=False),
    }


def _subtitle_languages(episodes: list[Any]) -> list[str]:
    found: list[str] = []
    for episode in episodes:
        for language in getattr(episode, "subtitle_url", None) or {}:
            if language not in found:
                found.append(language)
    return sorted(found)


@click.command(name="list")
@click.argument("work_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON.")
@pass_config
@abort_on_error
@coroutine
async def list_units(config: UserConfig, work_ref: str, as_json: bool) -> None:
    """List chapters / episodes of WORK."""
    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        items, label = await _units(client, work)

    if as_json:
        click.echo(
            jsonlib.dumps(
                [item.model_dump(mode="json") for item in items],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if work.work_type in (WorkType.MANHUA, WorkType.NOVEL):
        print_chapters_table(items, title=f"{work.title} - {label}s", label=label)
        return

    table = Table(title=f"{work.title} - {label}s", header_style="bold cyan", title_justify="left")
    table.add_column("#", justify="right", style="green")
    table.add_column("Title")
    table.add_column("Duration", justify="right")
    table.add_column("Subtitles")
    table.add_column("Access", justify="center")
    table.add_column("Price", justify="right")
    for item in items:
        # `has_access` is only meaningful for manhua/novel; for video and audio the
        # listing endpoint always reports False, so fall back to the price instead.
        if item.is_free:
            access = "[green]free[/]"
        elif item.has_access:
            access = "[cyan]owned[/]"
        else:
            access = "[yellow]paid[/]"
        table.add_row(
            str(item.episode_number),
            item.label(),
            _duration(item.duration),
            ", ".join((item.subtitle_url or {}).keys()),
            access,
            str(item.price) if item.price else "",
        )
    console.print(table)
    console.print(f"[dim]{len(items)} {label}(s)[/]")


def _duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


async def _units(client: FloweryClient, work: Work) -> tuple[list[Any], str]:
    """Return the listing models plus the noun describing them."""
    if work.work_type is WorkType.MANHUA:
        return await client.manhua_sections(work.id), "chapter"
    if work.work_type is WorkType.NOVEL:
        return await client.novel_chapters(work.id), "chapter"
    if work.work_type is WorkType.VIDEO:
        return await client.video_episodes(work.id), "episode"
    return await client.audio_episodes(work.id), "episode"


@click.command(name="calendar")
@click.option("--year", "-y", type=int, default=None, help="Defaults to the current year.")
@click.option("--month", "-m", type=int, default=None, help="Defaults to the current month.")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON.")
@pass_config
@abort_on_error
@coroutine
async def calendar(config: UserConfig, year: int | None, month: int | None, as_json: bool) -> None:
    """Show the release schedule for a month."""
    today = datetime.now(timezone.utc)
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12:
        raise click.BadParameter("month must be between 1 and 12")

    async with FloweryClient(config=config) as client:
        entries = await client.update_calendar(year, month)

    if as_json:
        click.echo(jsonlib.dumps([e.model_dump(mode="json") for e in entries], indent=2))
        return

    if not entries:
        console.print(f"[yellow]nothing scheduled for {year}-{month:02d}[/]")
        return

    table = Table(
        title=f"Updates for {year}-{month:02d}",
        header_style="bold cyan",
        title_justify="left",
    )
    table.add_column("Date", no_wrap=True)
    table.add_column("Work")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("Status", style="dim")
    for entry in sorted(entries, key=lambda e: e.v_scheduled_at or ""):
        when = entry.v_scheduled_at or ""
        try:
            when = datetime.fromisoformat(when).strftime("%Y-%m-%d")
        except ValueError:
            pass
        table.add_row(
            when,
            entry.v_work_title or entry.v_work_slug or "",
            entry.v_title,
            entry.v_content_type,
            entry.v_status or "",
        )
    console.print(table)
