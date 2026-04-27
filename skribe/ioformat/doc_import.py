"""Per-document import for TXT, RTF, DOCX, ODT, and DOC.

Returns ``(title, html_body)`` that the caller pairs with a freshly
created :class:`BinderItem`. Distinct from whole-project ``.scriv``
import in :mod:`scriv_import`.
"""
from __future__ import annotations

import html
import logging
import tempfile
from pathlib import Path

from skribe.ioformat.doc_convert import (
    has_pandoc,
    has_soffice,
    rtf_to_html_fallback,
    rtf_to_html_pandoc,
    run_pandoc,
    soffice_convert,
)

log = logging.getLogger(__name__)

IMPORT_EXTENSIONS: tuple[str, ...] = (".txt", ".rtf", ".docx", ".odt", ".doc")

# Extension → pandoc source format for binary inputs pandoc accepts natively.
_PANDOC_FROM = {".docx": "docx", ".odt": "odt"}


class DocImportError(Exception):
    """Raised when a document cannot be imported."""


def is_importable(path: Path) -> bool:
    return Path(path).suffix.lower() in IMPORT_EXTENSIONS


def _txt_to_html(raw: str) -> str:
    """Wrap each line of plain text in a ``<p>`` tag."""
    paragraphs = raw.replace("\r\n", "\n").split("\n")
    body = "".join(
        f"<p>{html.escape(line)}</p>" if line.strip() else "<p><br/></p>"
        for line in paragraphs
    )
    if not body:
        body = "<p></p>"
    return f"<!DOCTYPE html><html><body>{body}</body></html>"


def import_document(path: Path) -> tuple[str, str]:
    """Read ``path`` in a supported format; return ``(title, html_body)``.

    Raises :class:`DocImportError` for missing files, unsupported formats,
    or conversion failures. Title defaults to the filename stem.
    """
    path = Path(path)
    if not path.is_file():
        raise DocImportError(f"Not a file: {path}")
    ext = path.suffix.lower()
    title = path.stem

    if ext == ".txt":
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DocImportError(f"Failed to read {path}: {exc}") from exc
        return title, _txt_to_html(raw)

    if ext == ".rtf":
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DocImportError(f"Failed to read {path}: {exc}") from exc
        pandoc_html = rtf_to_html_pandoc(raw)
        if pandoc_html is not None:
            return title, pandoc_html
        log.info("pandoc unavailable or failed on %s; using text-only fallback", path.name)
        return title, rtf_to_html_fallback(raw)

    if ext in _PANDOC_FROM:
        if not has_pandoc():
            raise DocImportError(
                f"pandoc is required to import {ext} files but is not installed."
            )
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise DocImportError(f"Failed to read {path}: {exc}") from exc
        out = run_pandoc(raw_bytes, _PANDOC_FROM[ext], "html5")
        if out is None:
            raise DocImportError(f"pandoc failed to convert {path.name}.")
        body = out.strip() or "<p></p>"
        return title, f"<!DOCTYPE html><html><body>{body}</body></html>"

    if ext == ".doc":
        # pandoc can't read the old binary Word format. Bridge via LibreOffice:
        # .doc → .docx → HTML (pandoc).
        if not has_pandoc():
            raise DocImportError("pandoc is required to import .doc files.")
        if not has_soffice():
            raise DocImportError(
                "LibreOffice (soffice) is required to import .doc files but was not found."
            )
        with tempfile.TemporaryDirectory(prefix="skribe-doc-import-") as tmp:
            converted = soffice_convert(path, "docx", out_dir=Path(tmp))
            if converted is None:
                raise DocImportError(f"LibreOffice failed to convert {path.name} to DOCX.")
            try:
                raw_bytes = converted.read_bytes()
            except OSError as exc:
                raise DocImportError(f"Failed to read bridged DOCX: {exc}") from exc
            out = run_pandoc(raw_bytes, "docx", "html5")
            if out is None:
                raise DocImportError(f"pandoc failed on bridged DOCX for {path.name}.")
        body = out.strip() or "<p></p>"
        return title, f"<!DOCTYPE html><html><body>{body}</body></html>"

    raise DocImportError(f"Unsupported format: {ext}")
