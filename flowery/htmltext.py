"""Minimal HTML to plain-text / Markdown conversion.

The backend stores novel chapters and work descriptions as a small subset of
HTML (paragraphs, inline emphasis, footnote spans, the occasional anchor or
horizontal rule). Rather than pulling in a full HTML stack this module uses the
standard library's :mod:`html.parser` and emits Markdown-flavoured text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["collapse_blank_lines", "html_to_text", "strip_html"]

_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "table",
    "tr",
    "figure",
}
_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_SKIP_CONTENT = {"script", "style", "head", "title", "meta", "link"}

_WS_RUN = re.compile(r"[ \t\u00a0]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")


class _MarkdownConverter(HTMLParser):
    """Turns the chapter HTML subset into Markdown-flavoured plain text."""

    def __init__(self, *, markdown: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.markdown = markdown
        self.parts: list[str] = []
        self._skip_depth = 0
        self._list_stack: list[str] = []
        self._closers: list[tuple[str, str]] = []
        self._in_pre = False

    # -- output helpers -------------------------------------------------------
    def _emit(self, text: str) -> None:
        if text:
            self.parts.append(text)

    def _newline(self, count: int = 1) -> None:
        current = 0
        if self.parts:
            current = len(self.parts[-1]) - len(self.parts[-1].rstrip("\n"))
        if current < count:
            self.parts.append("\n" * (count - current))

    def _open(self, tag: str, opener: str, closer: str) -> None:
        self._emit(opener)
        self._closers.append((tag, closer))

    def _close(self, tag: str) -> None:
        if self._closers and self._closers[-1][0] == tag:
            _, closer = self._closers.pop()
            self._emit(closer)

    # -- parser hooks ---------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_CONTENT:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        attributes = {name.lower(): (value or "") for name, value in attrs}

        if tag in _HEADINGS:
            self._newline(2)
            if self.markdown:
                self._emit("#" * _HEADINGS[tag] + " ")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._newline(2)
            if self.markdown:
                self._emit("---")
            self._newline(2)
        elif tag == "pre":
            self._in_pre = True
            self._newline(2)
        elif tag in _BLOCK_TAGS:
            self._newline(2)
        elif tag in ("em", "i") and self.markdown:
            self._open(tag, "*", "*")
        elif tag in ("strong", "b") and self.markdown:
            self._open(tag, "**", "**")
        elif tag == "code" and self.markdown:
            self._open(tag, "`", "`")
        elif tag == "ul":
            self._list_stack.append("ul")
        elif tag == "ol":
            self._list_stack.append("ol")
        elif tag == "li":
            self._newline(1)
            if self.markdown:
                ordered = bool(self._list_stack) and self._list_stack[-1] == "ol"
                self._emit("1. " if ordered else "- ")
        elif tag == "blockquote":
            self._newline(2)
            if self.markdown:
                self._emit("> ")
        elif tag == "a":
            href = attributes.get("href", "")
            if self.markdown and href.startswith("http"):
                self._open("a", "[", f"]({href})")
        elif tag == "img":
            alt, src = attributes.get("alt", ""), attributes.get("src", "")
            if self.markdown and src:
                self._emit(f"![{alt}]({src})")
        elif tag == "span":
            # footnote spans are rendered as a bracketed aside
            closer = "]" if self.markdown and attributes.get("data-footnote") else ""
            self._closers.append(("span", closer))
            if closer:
                self._emit("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_CONTENT:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "pre":
            self._in_pre = False
            self._newline(2)
        elif tag in _HEADINGS or tag in _BLOCK_TAGS:
            self._newline(2)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._newline(1)
        elif tag == "li":
            self._newline(1)
        elif tag in ("em", "i", "strong", "b", "code", "a", "span"):
            self._close(tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        self._emit(data)


def collapse_blank_lines(text: str) -> str:
    """Squeeze runs of spaces and blank lines into something readable."""
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return _MANY_NEWLINES.sub("\n\n", text).strip()


def html_to_text(html: str, *, markdown: bool = True) -> str:
    """Convert a chapter body to Markdown (or plain text when ``markdown=False``)."""
    parser = _MarkdownConverter(markdown=markdown)
    parser.feed(html or "")
    parser.close()
    return collapse_blank_lines("".join(parser.parts))


def strip_html(html: str) -> str:
    """Convert a fragment to a single-line plain-text string."""
    return " ".join(html_to_text(html, markdown=False).split())
