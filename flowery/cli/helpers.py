"""Shared plumbing for the command line interface."""

from __future__ import annotations

import asyncio
import functools
import json as jsonlib
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, TypeVar

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..config import UserConfig
from ..errors import AccessDeniedError, FloweryError, NotAuthenticatedError
from ..models import ManhuaSection, NovelChapter, Work, WorkType

__all__ = [
    "TYPE_ICONS",
    "abort_on_error",
    "configure_logging",
    "console",
    "coroutine",
    "download_subtitles",
    "error_console",
    "parse_selection",
    "pass_config",
    "print_chapters_table",
    "print_kv",
    "print_works_table",
    "require_access",
    "require_type",
    "safe_language",
    "unit_dir",
    "unit_label",
    "work_dir",
    "write_metadata",
]

console = Console()
error_console = Console(stderr=True)

TYPE_ICONS: dict[WorkType, str] = {
    WorkType.MANHUA: "🖼",
    WorkType.NOVEL: "📖",
    WorkType.VIDEO: "🎬",
    WorkType.AUDIO: "🎧",
}

F = TypeVar("F", bound=Callable[..., Any])


def coroutine(func: F) -> F:
    """Run an ``async def`` click callback inside a fresh event loop."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def configure_logging(verbosity: int) -> None:
    """Route library logging to stderr, honouring ``-v`` / ``-vv``."""
    import logging

    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger("flowery")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    if level == logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)


pass_config = click.make_pass_decorator(UserConfig, ensure=True)


def abort_on_error(func: F) -> F:
    """Translate package errors into friendly CLI aborts."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except NotAuthenticatedError as exc:
            error_console.print(f"[red]error:[/] {exc}")
            error_console.print("[dim]run [bold]flowery auth login[/] to sign in[/]")
            raise click.exceptions.Exit(2) from exc
        except AccessDeniedError as exc:
            error_console.print(f"[red]access denied:[/] {exc}")
            error_console.print("[dim]this chapter is paid and the account has not purchased it[/]")
            raise click.exceptions.Exit(3) from exc
        except (FloweryError, OSError) as exc:
            error_console.print(f"[red]error:[/] {exc}")
            raise click.exceptions.Exit(1) from exc

    return wrapper  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# chapter selection
# --------------------------------------------------------------------------- #
def parse_selection(
    spec: str | None,
    available: Sequence[int],
    *,
    label: str = "chapter",
) -> list[int]:
    """Expand a selection string such as ``1-5,8,12-`` against ``available``.

    ``None`` or ``all`` selects everything. Open ended ranges (``5-``) and
    negative offsets (``-3``) are supported.
    """
    known = sorted(available)
    if not known:
        return []
    if spec is None or spec.strip().lower() in ("", "all", "*"):
        return list(known)

    picked: set[int] = set()
    lowered = spec.strip().lower()
    if lowered in ("new", "latest"):
        return known[-1:]

    for raw in re.split(r"[,\s]+", lowered):
        if not raw:
            continue
        if "-" not in raw:
            number = _as_int(raw, label)
            picked.add(number)
            continue
        start_text, _, end_text = raw.partition("-")
        start = _as_int(start_text, label) if start_text.strip() else None
        end = _as_int(end_text, label) if end_text.strip() else None
        if start is None and end is None:
            raise click.BadParameter(f"could not parse range {raw!r}")
        if start is None:
            picked.update(known[: max(0, end or 0)])
        elif end is None:
            picked.update(n for n in known if n >= start)
        else:
            low, high = sorted((start, end))
            picked.update(n for n in known if low <= n <= high)

    unknown = sorted(n for n in picked if n not in known)
    if unknown:
        sample = ", ".join(str(n) for n in known[:5])
        suffix = "..." if len(known) > 5 else ""
        raise click.BadParameter(f"{label} {unknown[0]} does not exist (available: {sample}{suffix})")
    return [n for n in known if n in picked]


def _as_int(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise click.BadParameter(f"{value!r} is not a valid {label} number") from exc


# --------------------------------------------------------------------------- #
# output layout
# --------------------------------------------------------------------------- #
def work_dir(config: UserConfig, work: Work) -> Path:
    """``DOWNLOADS/<work slug>`` for the given work."""
    from ..download import safe_name

    return config.output_root() / safe_name(work.slug)


def unit_dir(root: Path, number: int, slug: str) -> Path:
    """``<root>/<NNN-slug>`` for a single chapter or episode."""
    from ..download import safe_name

    return root / safe_name(f"{number:03d}-{slug}")


def unit_label(number: int, slug: str) -> str:
    return f"{number:03d}-{slug}"


def require_access(item: Any, *, what: str) -> None:
    """Raise a friendly error when the backend says the content is locked."""
    if getattr(item, "has_access", True):
        return
    price = getattr(item, "price", 0) or 0
    title = getattr(item, "slug", "?")
    raise AccessDeniedError(
        f"{what} {title!r} is locked (price {price}); purchase it in the app or pick a different range"
    )


def require_type(work: Work, expected: WorkType, noun: str) -> None:
    """Raise a helpful error when ``work`` is not of the ``expected`` media type."""
    if work.work_type is expected:
        return
    actual = work.work_type.value
    actual_article = "an" if actual[0] in "aeiou" else "a"
    expected_article = "an" if expected.value[0] in "aeiou" else "a"
    raise FloweryError(
        f"{work.slug!r} is {actual_article} {actual}, not {expected_article} {noun} "
        f"(try `flowery {actual} {work.slug}`)"
    )


def write_metadata(root: Path, data: dict[str, Any]) -> None:
    """Persist a ``work.json`` next to the downloaded content."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "work.json").write_text(
        jsonlib.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def print_works_table(works: Iterable[Work], *, title: str = "Works") -> None:
    """Render a list of works as a Rich table."""
    table = Table(title=title, header_style="bold cyan", title_justify="left")
    table.add_column("", width=2)
    table.add_column("Slug", style="green", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Type", justify="right")
    table.add_column("Free", justify="center")
    table.add_column("ID", style="dim", no_wrap=True)
    count = 0
    for work in works:
        count += 1
        table.add_row(
            TYPE_ICONS.get(work.work_type, "•"),
            work.slug,
            work.title,
            work.work_type.value,
            "✓" if work.is_free else "",
            work.id,
        )
    console.print(table)
    console.print(f"[dim]{count} work(s)[/]")


def print_chapters_table(
    items: Sequence[ManhuaSection | NovelChapter],
    *,
    title: str,
    label: str = "chapter",
) -> None:
    """Render chapters/sections grouped by their story arc."""
    from ..client import group_by_title

    if not items:
        console.print(f"[yellow]no {label}s available[/]")
        return

    table = Table(title=title, header_style="bold cyan", title_justify="left")
    table.add_column("#", justify="right", style="green", no_wrap=True)
    table.add_column("Title")
    table.add_column("Group", style="dim")
    table.add_column("Access", justify="center")
    table.add_column("Price", justify="right")
    for _, _, _, chapters in group_by_title(items):
        for chapter in chapters:
            number = getattr(chapter, "section_number", None) or getattr(chapter, "chapter_number", 0)
            access = Text("open", style="green") if chapter.has_access else Text("locked", style="red")
            if chapter.has_access and not chapter.is_free:
                access = Text("owned", style="cyan")
            table.add_row(
                str(number),
                chapter.label(),
                getattr(chapter, "group_title", "") or "",
                access,
                str(chapter.price) if chapter.price else "",
            )
    console.print(table)
    available = sum(1 for item in items if item.has_access)
    console.print(f"[dim]{len(items)} {label}(s), {available} accessible[/]")


def print_kv(pairs: Sequence[tuple[str, Any]], *, title: str | None = None) -> None:
    """Render a two column key/value table."""
    table = Table(title=title, show_header=False, title_justify="left", box=None)
    table.add_column("key", style="bold cyan", no_wrap=True)
    table.add_column("value")
    for key, value in pairs:
        if value in (None, "", [], {}):
            continue
        table.add_row(key, str(value))
    console.print(table)


# --------------------------------------------------------------------------- #
# sidecar downloads
# --------------------------------------------------------------------------- #
async def download_subtitles(
    client: Any,
    subtitle_map: dict[str, str] | None,
    dest_dir: Path,
    stem: str,
    *,
    skip_existing: bool = True,
) -> list[Path]:
    """Fetch every subtitle track of an episode next to the media file.

    Signed URLs are preferred (they are what the mobile client uses); when the
    signing endpoint refuses, the request falls back to the authenticated
    storage path.
    """
    from ..download import FileJob, fetch_many

    if not subtitle_map:
        return []

    try:
        signed = await client.sign("subtitles", list(subtitle_map.values()))
    except FloweryError:
        signed = {}

    jobs: list[FileJob] = []
    for language, path in subtitle_map.items():
        url = signed.get(path) or client.authenticated_url("subtitles", path)
        dest = dest_dir / f"{stem}.{safe_language(language)}.vtt"
        jobs.append(FileJob(url=url, dest=dest, authenticated=path not in signed, label=language))

    report = await fetch_many(
        client,
        jobs,
        description="Subtitles",
        console=console,
        skip_existing=skip_existing,
    )
    for job, reason in report.failed:
        error_console.print(f"[yellow]subtitle {job.label} failed:[/] {reason}")
    return [job.dest for job in jobs if job.dest.exists()]


def safe_language(value: str) -> str:
    """Normalise a language tag for use inside a filename."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "und"
