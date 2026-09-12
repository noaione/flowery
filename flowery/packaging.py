"""Container formats: CBZ for manhua pages and EPUB for novel chapters.

Both writers use only the standard library so that downloading never depends on
an extra archive package.
"""

from __future__ import annotations

import html
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import FloweryError
from .htmltext import html_to_text

__all__ = ["EpubBook", "EpubChapter", "write_cbz", "write_epub"]

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".jxl"}

_CSS = """\
body { font-family: serif; line-height: 1.6; margin: 1.2em; }
h1, h2 { font-family: sans-serif; line-height: 1.25; }
p { margin: 0 0 0.9em; text-indent: 1.5em; }
blockquote { margin: 0.8em 1.4em; font-style: italic; }
hr { border: none; border-top: 1px solid #999; margin: 1.4em 0; }
"""


def write_cbz(source_dir: Path, dest: Path) -> Path:
    """Archive every image in ``source_dir`` into a CBZ, in filename order."""
    pages = sorted(
        (p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES),
        key=lambda p: p.name,
    )
    if not pages:
        raise FloweryError(f"no images found in {source_dir}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for index, page in enumerate(pages, start=1):
            archive.write(page, arcname=f"{index:03d}{page.suffix.lower()}")
    return dest


@dataclass(slots=True)
class EpubChapter:
    """One XHTML document inside an EPUB."""

    title: str
    html: str
    identifier: str = ""

    def document(self, filename: str, language: str) -> str:
        body = self.html
        if "<p" not in body and "<h" not in body:
            body = f"<p>{html.escape(body)}</p>"
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="'
            f'{language}" lang="{language}">\n'
            "<head>\n"
            '  <meta charset="utf-8"/>\n'
            f"  <title>{html.escape(self.title)}</title>\n"
            '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
            "</head>\n"
            "<body>\n"
            f"  <h2>{html.escape(self.title)}</h2>\n"
            f"  {body}\n"
            "</body>\n"
            "</html>\n"
        )


@dataclass(slots=True)
class EpubBook:
    """Metadata for the EPUB being written."""

    title: str
    author: str = "Unknown"
    language: str = "en"
    identifier: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    description: str = ""
    publisher: str = "flowery"


def _xhtml(text: str) -> str:
    return html.escape(text, quote=True)


def write_epub(book: EpubBook, chapters: list[EpubChapter], dest: Path) -> Path:
    """Write an EPUB 3 archive containing ``chapters``."""
    if not chapters:
        raise FloweryError("cannot write an EPUB without chapters")

    dest.parent.mkdir(parents=True, exist_ok=True)
    names = [f"chapter{index:04d}.xhtml" for index in range(1, len(chapters) + 1)]
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_items = [
        '    <item id="style" href="style.css" media-type="text/css"/>',
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    ]
    spine_items = []
    for name in names:
        item_id = name.removesuffix(".xhtml")
        manifest_items.append(f'    <item id="{item_id}" href="{name}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'    <itemref idref="{item_id}"/>')

    nav_points = "\n".join(
        f'        <li><a href="{name}">{_xhtml(chapter.title)}</a></li>'
        for name, chapter in zip(names, chapters, strict=True)
    )

    description = f"    <dc:description>{_xhtml(book.description)}</dc:description>\n" if book.description else ""

    content_opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="'
        f'{book.language}">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{_xhtml(book.identifier)}</dc:identifier>\n'
        f"    <dc:title>{_xhtml(book.title)}</dc:title>\n"
        f"    <dc:language>{_xhtml(book.language)}</dc:language>\n"
        f"    <dc:creator>{_xhtml(book.author)}</dc:creator>\n"
        f"    <dc:publisher>{_xhtml(book.publisher)}</dc:publisher>\n"
        f"{description}"
        '    <meta property="dcterms:modified">'
        f"{modified}</meta>\n"
        "  </metadata>\n"
        "  <manifest>\n" + "\n".join(manifest_items) + "\n  </manifest>\n"
        "  <spine>\n" + "\n".join(spine_items) + "\n  </spine>\n"
        "</package>\n"
    )

    nav_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="'
        f'{book.language}">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_xhtml(book.title)}</title>\n"
        "</head>\n"
        "<body>\n"
        '  <nav epub:type="toc" id="toc">\n'
        "    <h1>Contents</h1>\n"
        "    <ol>\n"
        f"{nav_points}\n"
        "    </ol>\n"
        "  </nav>\n"
        "</body>\n"
        "</html>\n"
    )

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )

    with zipfile.ZipFile(dest, "w") as archive:
        # `mimetype` must be first and stored uncompressed.
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/style.css", _CSS, compress_type=zipfile.ZIP_DEFLATED)
        for name, chapter in zip(names, chapters, strict=True):
            archive.writestr(
                f"OEBPS/{name}",
                chapter.document(name, book.language),
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return dest


def chapter_html_from_markdown(markdown: str) -> str:
    """Wrap already-converted Markdown text into paragraph HTML."""
    blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
    rendered: list[str] = []
    for block in blocks:
        if block.startswith("# "):
            rendered.append(f"<h3>{html.escape(block[2:].strip())}</h3>")
        elif block == "---":
            rendered.append("<hr/>")
        else:
            rendered.append(f"<p>{html.escape(block).replace(chr(10), '<br/>')}</p>")
    return "\n".join(rendered)


def markdown_to_epub_chapter(title: str, source_html: str) -> EpubChapter:
    """Convert a raw chapter body into an :class:`EpubChapter`."""
    return EpubChapter(title=title, html=chapter_html_from_markdown(html_to_text(source_html)))
