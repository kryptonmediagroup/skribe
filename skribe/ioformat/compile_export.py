"""Project-wide compile pipeline.

Walks the chosen subtree, concatenates each item's body into a single
HTML document (with chapter-level page breaks), then dispatches that
HTML to the requested target. Targets:

- **Print** / **PDF** — Qt's QTextDocument + QPrinter.
- **Web Page (.html)** — direct write.
- **Plain Text (.txt)** — Qt's plain-text projection.
- **Rich Text (.rtf)** / **DOCX** / **ODT** / **EPUB 2** / **EPUB 3** — pandoc.

This is deliberately a thinner cousin of Scrivener's Compile: no
section-layout templates, no per-item separator styling. The output
pairs well with Skribe's Export-to-Scrivener round trip when a user
needs polished typography that this pipeline can't produce.
"""
from __future__ import annotations

import html as _html
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from skribe.ioformat.doc_convert import has_pandoc, run_pandoc, run_pandoc_bytes
from skribe.model.project import BinderItem, ItemType

log = logging.getLogger(__name__)

# Display labels match Scrivener's "Compile For:" wording so the
# vocabulary is familiar to anyone migrating from there.
FMT_PRINT = "Print"
FMT_PDF = "PDF"
FMT_HTML = "Web Page (.html)"
FMT_EPUB2 = "ePub 2 Ebook (.epub)"
FMT_EPUB3 = "ePub 3 Ebook (.epub)"
FMT_DOCX = "Microsoft Word (.docx)"
FMT_ODT = "Open Office (.odt)"
FMT_RTF = "Rich Text (.rtf)"
FMT_TXT = "Plain Text (.txt)"

COMPILE_FORMATS: tuple[str, ...] = (
    FMT_PRINT,
    FMT_PDF,
    FMT_HTML,
    FMT_EPUB2,
    FMT_EPUB3,
    FMT_DOCX,
    FMT_ODT,
    FMT_RTF,
    FMT_TXT,
)

_FILE_EXT: dict[str, str] = {
    FMT_PDF: ".pdf",
    FMT_HTML: ".html",
    FMT_EPUB2: ".epub",
    FMT_EPUB3: ".epub",
    FMT_DOCX: ".docx",
    FMT_ODT: ".odt",
    FMT_RTF: ".rtf",
    FMT_TXT: ".txt",
}


def file_extension(fmt: str) -> Optional[str]:
    return _FILE_EXT.get(fmt)


def supported_formats() -> list[str]:
    """Filter the static list by what's actually runnable on this box."""
    fmts = [FMT_PRINT, FMT_PDF, FMT_HTML, FMT_TXT]
    if has_pandoc():
        fmts += [FMT_EPUB2, FMT_EPUB3, FMT_DOCX, FMT_ODT, FMT_RTF]
    # Preserve canonical order.
    return [f for f in COMPILE_FORMATS if f in fmts]


class CompileError(Exception):
    """Raised when a compile cannot complete."""


# --- options / front-matter ----------------------------------------------

@dataclass
class FrontMatter:
    enabled: bool = False
    title: str = ""
    subtitle: str = ""
    author: str = ""

    def has_content(self) -> bool:
        return bool(self.title or self.subtitle or self.author)


@dataclass
class CompileOptions:
    items: list[BinderItem] = field(default_factory=list)   # ordered, included only
    project_title: str = ""                                  # used for HTML <title> + EPUB metadata
    front: FrontMatter = field(default_factory=FrontMatter)


# --- HTML composition ---------------------------------------------------

_FRONT_TEMPLATE = (
    '<div class="skribe-front" '
    'style="text-align:center; page-break-after: always;">'
    '<h1 style="margin-top:30%;">{title}</h1>'
    '{subtitle_block}'
    '{author_block}'
    '</div>'
)


def _esc(s: str) -> str:
    return _html.escape(s or "")


def _build_front_html(fm: FrontMatter) -> str:
    if not (fm.enabled and fm.has_content()):
        return ""
    subtitle = f'<h2>{_esc(fm.subtitle)}</h2>' if fm.subtitle else ""
    author = (
        f'<p style="margin-top:24pt;">{_esc(fm.author)}</p>' if fm.author else ""
    )
    return _FRONT_TEMPLATE.format(
        title=_esc(fm.title),
        subtitle_block=subtitle,
        author_block=author,
    )


def _strip_to_body(html: str) -> str:
    """Reduce a QTextEdit-style document to its body content.

    QTextEdit's ``toHtml()`` produces a complete ``<!DOCTYPE>`` +
    ``<html><body>…</body></html>`` document. We need just the body for
    concatenation; pandoc and QTextDocument both accept inline snippets
    once we re-wrap them at the top level.
    """
    if not html:
        return ""
    lo = html.lower()
    body_start = lo.find("<body")
    if body_start == -1:
        return html
    body_close = lo.find(">", body_start)
    if body_close == -1:
        return html
    end = lo.rfind("</body>")
    if end == -1:
        end = len(html)
    return html[body_close + 1:end]


def build_compile_html(
    options: CompileOptions,
    read_body: Callable[[str], str],
) -> str:
    """Concatenate ``options.items`` into one HTML document.

    Each item gets its title as an ``<h1>`` (so EPUB readers and pandoc
    pick it up as a chapter boundary) followed by its body. A
    ``page-break-before`` is inserted before every chapter except the
    first — that's what gives PDF and EPUB readers per-chapter pages.
    Folders contribute their title only; bodies live on TEXT items.
    """
    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head>")
    parts.append('<meta charset="utf-8">')
    if options.project_title:
        parts.append(f"<title>{_esc(options.project_title)}</title>")
    parts.append("</head><body>")

    front_html = _build_front_html(options.front)
    if front_html:
        parts.append(front_html)

    first_chapter = not bool(front_html)
    for item in options.items:
        wrapper = (
            "<div>"
            if first_chapter
            else '<div style="page-break-before: always;">'
        )
        first_chapter = False
        parts.append(wrapper)
        if item.title:
            parts.append(f"<h1>{_esc(item.title)}</h1>")
        if item.type is ItemType.TEXT:
            parts.append(_strip_to_body(read_body(item.uuid) or ""))
        parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


# --- format dispatch ----------------------------------------------------

def write_compile(
    fmt: str,
    html: str,
    target: Path,
    options: CompileOptions,
) -> None:
    """Write ``html`` to ``target`` in the chosen format.

    ``fmt`` must be one of :data:`COMPILE_FORMATS` *except* ``FMT_PRINT``
    — Print drives a printer dialog, so it goes through
    :func:`render_html_to_printer` instead.
    """
    if fmt == FMT_PRINT:
        raise CompileError("Print is handled via render_html_to_printer().")

    target = Path(target)

    if fmt == FMT_HTML:
        target.write_text(html, encoding="utf-8")
        return

    if fmt == FMT_PDF:
        _render_html_to_pdf(html, target)
        return

    if fmt == FMT_TXT:
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setHtml(html)
        target.write_text(doc.toPlainText(), encoding="utf-8")
        return

    if not has_pandoc():
        raise CompileError(f"pandoc is required for {fmt}.")

    if fmt == FMT_RTF:
        # --standalone: emit a complete \rtf1 document, not a fragment.
        rtf = run_pandoc(html, "html", "rtf", extra_args=("-s",))
        if rtf is None:
            raise CompileError("pandoc failed to produce RTF.")
        target.write_text(rtf, encoding="utf-8")
        return

    if fmt in (FMT_DOCX, FMT_ODT):
        to_fmt = "docx" if fmt == FMT_DOCX else "odt"
        raw = run_pandoc_bytes(html, "html", to_fmt)
        if raw is None:
            raise CompileError(f"pandoc failed to produce {fmt}.")
        target.write_bytes(raw)
        return

    if fmt in (FMT_EPUB2, FMT_EPUB3):
        to_fmt = "epub" if fmt == FMT_EPUB2 else "epub3"
        extra: list[str] = ["-s"]
        title = options.project_title or options.front.title
        if title:
            extra += ["--metadata", f"title={title}"]
        if options.front.author:
            extra += ["--metadata", f"author={options.front.author}"]
        raw = run_pandoc_bytes(html, "html", to_fmt, extra_args=tuple(extra))
        if raw is None:
            raise CompileError(f"pandoc failed to produce {fmt}.")
        target.write_bytes(raw)
        return

    raise CompileError(f"Unhandled format: {fmt}")  # defensive


def render_html_to_printer(html: str, printer) -> None:
    """Send ``html`` to a configured QPrinter (Print or PDF target).

    The caller owns the QPrinter — the dialog flow shows a print dialog
    for ``FMT_PRINT`` and constructs a PDF-mode printer for the file
    case. We just render. Imports stay local so the module doesn't pull
    QtPrintSupport unless someone actually prints.
    """
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(html)
    doc.print_(printer)


def _render_html_to_pdf(html: str, target: Path) -> None:
    from PySide6.QtPrintSupport import QPrinter
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(target))
    render_html_to_printer(html, printer)
