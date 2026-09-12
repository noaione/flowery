"""``flowery manhua`` - download manhua chapters as page images."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import click

from ..client import FloweryClient
from ..config import UserConfig
from ..download import FileJob, fetch_many, remove_temp, safe_name
from ..errors import FloweryError
from ..models import ManhuaSection, Work, WorkType
from ..packaging import IMAGE_SUFFIXES, write_cbz
from .helpers import (
    abort_on_error,
    console,
    coroutine,
    error_console,
    parse_selection,
    pass_config,
    print_chapters_table,
    require_access,
    require_type,
    unit_dir,
    unit_label,
    work_dir,
    write_metadata,
)

__all__ = ["manhua"]

_IMAGE_FALLBACK = ".jpg"


@click.command(name="manhua")
@click.argument("work_ref")
@click.option(
    "--chapters",
    "-c",
    "selection",
    default=None,
    help="Chapters to fetch, e.g. '1-5,8' or 'all'. Defaults to all accessible chapters.",
)
@click.option("--cbz/--no-cbz", default=False, show_default=True, help="Also build a CBZ archive.")
@click.option("--concurrency", "-j", type=click.IntRange(1, 32), default=8, show_default=True)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output root. Defaults to ./DOWNLOADS.",
)
@click.option("--force", is_flag=True, help="Re-download files that already exist.")
@click.option("--list", "list_only", is_flag=True, help="Only list chapters, download nothing.")
@pass_config
@abort_on_error
@coroutine
async def manhua(
    config: UserConfig,
    work_ref: str,
    selection: str | None,
    cbz: bool,
    concurrency: int,
    output: Path | None,
    force: bool,
    list_only: bool,
) -> None:
    """Download the manhua WORK as page images."""
    if output is not None:
        config.download_dir = str(output)

    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        require_type(work, WorkType.MANHUA, "manhua")
        sections = await client.manhua_sections(work.id)

        if list_only:
            print_chapters_table(sections, title=f"{work.title} - chapters")
            return

        chosen = parse_selection(selection, [s.section_number for s in sections])
        root = work_dir(config, work)

        meta = await client.manhua_translation(work.id)
        write_metadata(
            root,
            {
                "title": work.title,
                "slug": work.slug,
                "id": work.id,
                "type": work.work_type.value,
                "created_at": work.created_at,
                "original_title": (meta.get("manhuas") or {}).get("title"),
                "translator": (meta.get("translators") or {}).get("display_name"),
                "total_chapters": meta.get("total_sections"),
            },
        )

        by_number = {section.section_number: section for section in sections}
        downloaded = 0
        for number in chosen:
            section = by_number[number]
            label = unit_label(number, section.slug)
            console.rule(f"[bold]{label} - {section.label()}", style="cyan")

            try:
                require_access(section, what="chapter")
            except FloweryError as exc:
                error_console.print(f"[yellow]skipping:[/] {exc}")
                continue

            if not section.image_urls:
                error_console.print(f"[yellow]skipping {label}:[/] no pages returned")
                continue

            chapter_dir = unit_dir(root, number, section.slug)
            jobs = [
                FileJob(
                    url=client.manhua_image_url(path),
                    dest=chapter_dir / f"{index:03d}{Path(path).suffix or _IMAGE_FALLBACK}",
                    authenticated=True,
                    label=Path(path).name,
                    # A page may have been post-processed in place (denoising turns
                    # a .jpg into a .png), so accept any image suffix as present.
                    candidates=IMAGE_SUFFIXES,
                )
                for index, path in enumerate(section.image_urls, start=1)
            ]
            report = await fetch_many(
                client,
                jobs,
                concurrency=concurrency,
                description=f"{label} pages",
                console=console,
                skip_existing=not force,
            )
            console.print(f"  [green]✓[/] {report.summary()} → [dim]{chapter_dir}[/]")
            for job, reason in report.failed:
                error_console.print(f"  [red]✗[/] {job.display()}: {reason}")
            if report.failed:
                continue

            if cbz:
                try:
                    archive = write_cbz(chapter_dir, root / f"{safe_name(label)}.cbz")
                except FloweryError as exc:
                    error_console.print(f"  [yellow]cbz:[/] {exc}")
                else:
                    console.print(f"  [green]✓[/] archive → [dim]{archive}[/]")
            downloaded += 1
            remove_temp(chapter_dir)

        console.print(f"\n[bold green]done[/] - {downloaded} chapter(s) under [dim]{root}[/]")
        _write_index(root, work, sections)


def _write_index(root: Path, work: Work, sections: list[ManhuaSection]) -> None:
    """Drop a small JSON index describing what is on disk."""
    index = {
        "slug": work.slug,
        "title": work.title,
        "created_at": work.created_at,
        "chapters": [
            {
                "number": section.section_number,
                "slug": section.slug,
                "title": section.label(),
                "pages": len(section.image_urls),
                "accessible": section.has_access,
                "created_at": section.created_at,
            }
            for section in sections
        ],
    }
    (root / "index.json").write_text(jsonlib.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
