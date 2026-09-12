"""``flowery novel`` - download novel chapters as text, HTML and EPUB."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import click

from ..client import FloweryClient
from ..config import UserConfig
from ..download import safe_name
from ..errors import FloweryError
from ..htmltext import html_to_text
from ..models import NovelChapter, Work, WorkType
from ..packaging import EpubBook, EpubChapter, markdown_to_epub_chapter, write_epub
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

__all__ = ["novel"]

_VALID_FORMATS = ("md", "txt", "html")


@click.command(name="novel")
@click.argument("work_ref")
@click.option(
    "--chapters",
    "-c",
    "selection",
    default=None,
    help="Chapters to fetch, e.g. '1-5,8' or 'all'. Defaults to every accessible chapter.",
)
@click.option(
    "--format",
    "-f",
    "formats",
    default="md",
    show_default=True,
    help="Comma separated: md, txt, html, epub.",
)
@click.option("--concurrency", "-j", type=click.IntRange(1, 32), default=6, show_default=True)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output root. Defaults to ./DOWNLOADS.",
)
@click.option("--force", is_flag=True, help="Rewrite files that already exist.")
@click.option("--list", "list_only", is_flag=True, help="Only list chapters, download nothing.")
@pass_config
@abort_on_error
@coroutine
async def novel(
    config: UserConfig,
    work_ref: str,
    selection: str | None,
    formats: str,
    concurrency: int,
    output: Path | None,
    force: bool,
    list_only: bool,
) -> None:
    """Download the novel WORK as readable text."""
    requested = {part.strip().lower() for part in formats.split(",") if part.strip()}
    unknown = requested - set(_VALID_FORMATS) - {"epub"}
    if unknown:
        raise click.BadParameter(
            f"unknown format(s): {', '.join(sorted(unknown))} (valid: {', '.join([*_VALID_FORMATS, 'epub'])})"
        )
    if output is not None:
        config.download_dir = str(output)

    async with FloweryClient(config=config) as client:
        work = await client.find_work(work_ref)
        require_type(work, WorkType.NOVEL, "novel")
        chapters = await client.novel_chapters(work.id)

        if list_only:
            print_chapters_table(chapters, title=f"{work.title} - chapters")
            return

        chosen = parse_selection(selection, [c.chapter_number for c in chapters])
        root = work_dir(config, work)

        meta = await client.novel_translation(work.id)
        write_metadata(
            root,
            {
                "title": work.title,
                "slug": work.slug,
                "id": work.id,
                "type": work.work_type.value,
                "original_title": (meta.get("novels") or {}).get("title"),
                "translator": (meta.get("translators") or {}).get("display_name"),
                "total_chapters": meta.get("total_chapters"),
                "total_word_count": meta.get("total_word_count"),
            },
        )

        by_number = {chapter.chapter_number: chapter for chapter in chapters}
        epub_chapters: list[EpubChapter] = []
        downloaded = 0
        words = 0

        for number in chosen:
            chapter = by_number[number]
            label = unit_label(number, chapter.slug)
            try:
                require_access(chapter, what="chapter")
            except FloweryError as exc:
                error_console.print(f"[yellow]skipping {label}:[/] {exc}")
                continue

            payload = await client.novel_chapter(work.id, number)
            if not payload.has_access:
                error_console.print(f"[yellow]skipping {label}:[/] backend refused access")
                continue

            body = payload.chapter
            chapter_dir = unit_dir(root, number, body.slug or chapter.slug)
            chapter_dir.mkdir(parents=True, exist_ok=True)

            markdown = html_to_text(body.content, markdown=True)
            header = f"# {body.label()}\n\n"

            if "md" in requested:
                path = chapter_dir / "chapter.md"
                if force or not path.exists():
                    path.write_text(header + markdown + "\n", encoding="utf-8")
            if "txt" in requested:
                path = chapter_dir / "chapter.txt"
                if force or not path.exists():
                    plain = html_to_text(body.content, markdown=False)
                    path.write_text(f"{body.label()}\n\n" + plain + "\n", encoding="utf-8")
            if "html" in requested:
                path = chapter_dir / "chapter.html"
                if force or not path.exists():
                    path.write_text(
                        "<!DOCTYPE html>\n"
                        f'<html lang="{body.language or "en"}"><head><meta charset="utf-8">'
                        f"<title>{body.label()}</title></head><body>\n"
                        f"<h1>{body.label()}</h1>\n{body.content}\n</body></html>\n",
                        encoding="utf-8",
                    )

            epub_chapters.append(markdown_to_epub_chapter(body.label(), body.content))
            words += body.word_count or len(markdown.split())
            downloaded += 1
            console.print(
                f"[green]✓[/] {label} "
                f"[dim]({body.word_count or len(markdown.split()):,} words)[/] → "
                f"[dim]{chapter_dir}[/]"
            )

        if "epub" in requested and epub_chapters:
            book = EpubBook(
                title=work.title,
                author=(meta.get("translators") or {}).get("display_name") or "Unknown",
                language="en",
                description=html_to_text(meta.get("description") or "", markdown=False),
            )
            target = root / f"{safe_name(work.slug)}.epub"
            write_epub(book, epub_chapters, target)
            console.print(f"[green]✓[/] EPUB ({len(epub_chapters)} chapters) → [dim]{target}[/]")

        console.print(f"\n[bold green]done[/] - {downloaded} chapter(s), {words:,} words under [dim]{root}[/]")
        _write_index(root, work, chapters, [c.chapter_number for c in chapters])


def _write_index(root: Path, work: Work, chapters: list[NovelChapter], chosen: list[int]) -> None:
    index = {
        "slug": work.slug,
        "title": work.title,
        "chapters": [
            {
                "number": chapter.chapter_number,
                "slug": chapter.slug,
                "title": chapter.label(),
                "words": chapter.word_count,
                "accessible": chapter.has_access,
                "selected": chapter.chapter_number in chosen,
            }
            for chapter in chapters
        ],
    }
    (root / "index.json").write_text(jsonlib.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
