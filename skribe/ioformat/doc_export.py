"""Per-document export for TXT, RTF, DOCX, ODT, DOC.

Inputs: the HTML body of a single document plus its comments list.
Outputs: a file in the requested format.

Comment preservation strategy:
- **TXT**: stripped silently (no concept of annotations).
- **RTF / DOC (via DOCX bridge)**: injected as standard RTF ``\\annotation``
  destination groups anchored at the end of each comment's range. Scrivener
  3 reads these via its Comments panel on the other side of the round trip.
- **DOCX**: post-processed into native Word comments (``commentRangeStart``
  / ``commentRangeEnd`` / ``commentReference`` markers in ``document.xml``
  plus a new ``word/comments.xml`` part, with the required
  ``[Content_Types].xml`` override and document-relationship entry).
- **ODT**: stripped for now — ODT annotations have a different structural
  shape (one ``<office:annotation>`` at a point, not a ranged span); not
  yet implemented.
"""
from __future__ import annotations

import io
import logging
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from lxml import etree

from skribe.ioformat.doc_convert import (
    has_pandoc,
    has_soffice,
    run_pandoc,
    run_pandoc_bytes,
    soffice_convert,
)
from skribe.model.comment import Comment

log = logging.getLogger(__name__)

EXPORT_FORMATS: tuple[str, ...] = ("TXT", "RTF", "DOCX", "ODT", "DOC")
EXPORT_EXT: dict[str, str] = {
    "TXT": ".txt",
    "RTF": ".rtf",
    "DOCX": ".docx",
    "ODT": ".odt",
    "DOC": ".doc",
}


class DocExportError(Exception):
    """Raised when a document export fails."""


def supported_export_formats() -> list[str]:
    """Filter the static list by tools available on this machine."""
    fmts = ["TXT"]
    if has_pandoc():
        fmts.extend(["RTF", "DOCX", "ODT"])
        if has_soffice():
            fmts.append("DOC")
    return fmts


# --- Comment injection (RTF) ---------------------------------------------

_MARKER_START = "[[[SKRCMT:{uuid}]]]"
_MARKER_END = "[[[/SKRCMT:{uuid}]]]"
_MARKER_START_RE = re.compile(r"\[\[\[SKRCMT:([0-9A-F\-]+)\]\]\]")
_MARKER_END_RE = re.compile(r"\[\[\[/SKRCMT:([0-9A-F\-]+)\]\]\]")


def _rtf_escape(s: str) -> str:
    """Escape the three RTF specials; leave everything else as text."""
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _wrap_comment_markers_in_html(html_body: str, comments: list[Comment]) -> str:
    """Insert sentinel text around each comment range via a QTextDocument.

    We can't index directly into the HTML string — comment anchors are
    QTextDocument character positions. Load the HTML, insert sentinels via
    a cursor, serialize back. Inserts run end→start so earlier positions
    stay valid.
    """
    if not comments:
        return html_body
    from PySide6.QtGui import QTextCursor, QTextDocument

    doc = QTextDocument()
    doc.setHtml(html_body or "")
    ordered = sorted(comments, key=lambda c: c.anchor_start, reverse=True)
    for c in ordered:
        end_marker = _MARKER_END.format(uuid=c.uuid)
        start_marker = _MARKER_START.format(uuid=c.uuid)
        cursor = QTextCursor(doc)
        cursor.setPosition(min(c.anchor_end, doc.characterCount() - 1))
        cursor.insertText(end_marker)
        cursor.setPosition(min(c.anchor_start, doc.characterCount() - 1))
        cursor.insertText(start_marker)
    return doc.toHtml()


def _inject_rtf_annotations(rtf: str, comments: Iterable[Comment]) -> str:
    """Swap our sentinel pairs in ``rtf`` for standard RTF annotation groups.

    Point-anchored at the *end* of each comment's range; the commented text
    stays inline. Missing comment IDs are dropped with a warning — the
    commented text remains in place.
    """
    by_id = {c.uuid: c for c in comments}
    # Start markers: drop them (we anchor comments at end).
    rtf = _MARKER_START_RE.sub("", rtf)

    def _replace(match: re.Match) -> str:
        cid = match.group(1)
        c = by_id.get(cid)
        if c is None:
            log.warning("RTF export: no comment for sentinel UUID %s", cid)
            return ""
        author = _rtf_escape(c.author_name or "")
        initials = _rtf_escape(c.author_initials or "?")
        body = _rtf_escape(c.body or "")
        return (
            "{\\*\\atnid " + initials + "}"
            "{\\*\\atnauthor " + author + "}"
            "\\chatn"
            "{\\*\\annotation{\\pard\\plain " + body + "\\par}}"
        )

    return _MARKER_END_RE.sub(_replace, rtf)


# --- Comment injection (DOCX) --------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
_COMMENTS_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"

_W = f"{{{_W_NS}}}"
_MARKER_ANY_RE = re.compile(r"\[\[\[(/?)SKRCMT:([0-9A-F\-]+)\]\]\]")


def _clone(el: etree._Element) -> etree._Element:
    """Deep-copy an element so a second <w:r> can reuse the same <w:rPr>."""
    return etree.fromstring(etree.tostring(el))


def _split_runs_for_markers(doc_root: etree._Element, id_for_uuid: dict[str, str]) -> None:
    """Replace runs containing sentinel text with split runs + comment markers.

    Walks every ``<w:r>`` that has exactly one ``<w:t>`` whose text contains a
    ``[[[SKRCMT:…]]]`` marker, then rebuilds it as a sequence: text run
    prefix, marker element(s), text run suffix — preserving the run's
    ``<w:rPr>`` properties on each text segment.
    """
    W_R, W_T, W_RPR = _W + "r", _W + "t", _W + "rPr"
    for r in list(doc_root.iter(W_R)):
        t_elements = r.findall(W_T)
        if len(t_elements) != 1:
            continue
        t = t_elements[0]
        text = t.text or ""
        if "[[[SKRCMT:" not in text and "[[[/SKRCMT:" not in text:
            continue

        rpr = r.find(W_RPR)

        # Break text into alternating ("text", str) / ("marker", kind, uuid) segments.
        segments: list[tuple] = []
        pos = 0
        for m in _MARKER_ANY_RE.finditer(text):
            if m.start() > pos:
                segments.append(("text", text[pos:m.start()]))
            kind = "end" if m.group(1) else "start"
            segments.append(("marker", kind, m.group(2)))
            pos = m.end()
        if pos < len(text):
            segments.append(("text", text[pos:]))

        new_elements: list[etree._Element] = []
        for seg in segments:
            if seg[0] == "text":
                if not seg[1]:
                    continue
                new_r = etree.Element(W_R)
                if rpr is not None:
                    new_r.append(_clone(rpr))
                new_t = etree.SubElement(new_r, W_T)
                new_t.set(f"{{{_XML_NS}}}space", "preserve")
                new_t.text = seg[1]
                new_elements.append(new_r)
            else:
                _kind, uuid = seg[1], seg[2]
                cid = id_for_uuid.get(uuid)
                if cid is None:
                    log.warning("DOCX export: unknown comment UUID %s", uuid)
                    continue
                if _kind == "start":
                    el = etree.Element(_W + "commentRangeStart")
                    el.set(_W + "id", cid)
                    new_elements.append(el)
                else:
                    end_el = etree.Element(_W + "commentRangeEnd")
                    end_el.set(_W + "id", cid)
                    new_elements.append(end_el)
                    ref_r = etree.Element(W_R)
                    ref_rpr = etree.SubElement(ref_r, W_RPR)
                    rstyle = etree.SubElement(ref_rpr, _W + "rStyle")
                    rstyle.set(_W + "val", "CommentReference")
                    ref = etree.SubElement(ref_r, _W + "commentReference")
                    ref.set(_W + "id", cid)
                    new_elements.append(ref_r)

        parent = r.getparent()
        idx = parent.index(r)
        for el in new_elements:
            parent.insert(idx, el)
            idx += 1
        parent.remove(r)


def _build_comments_xml(comments: list[Comment], id_for_uuid: dict[str, str]) -> bytes:
    """Construct ``word/comments.xml`` with one ``<w:comment>`` per comment."""
    root = etree.Element(_W + "comments", nsmap={"w": _W_NS})
    for c in comments:
        cid = id_for_uuid[c.uuid]
        date = c.created or datetime.utcnow().isoformat(timespec="seconds") + "Z"
        comment_el = etree.SubElement(root, _W + "comment")
        comment_el.set(_W + "id", cid)
        comment_el.set(_W + "author", c.author_name or "")
        comment_el.set(_W + "initials", c.author_initials or "")
        comment_el.set(_W + "date", date)
        # One paragraph per line of the comment body.
        for line in (c.body or "").splitlines() or [""]:
            p = etree.SubElement(comment_el, _W + "p")
            r = etree.SubElement(p, _W + "r")
            t = etree.SubElement(r, _W + "t")
            t.set(f"{{{_XML_NS}}}space", "preserve")
            t.text = line
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _patch_content_types(raw: bytes) -> bytes:
    """Ensure ``[Content_Types].xml`` has an Override for ``word/comments.xml``."""
    tree = etree.fromstring(raw)
    ns = {"ct": _CT_NS}
    for ov in tree.findall("ct:Override", ns):
        if ov.get("PartName") == "/word/comments.xml":
            return raw  # already there, no change
    new = etree.SubElement(tree, f"{{{_CT_NS}}}Override")
    new.set("PartName", "/word/comments.xml")
    new.set("ContentType", _COMMENTS_CT)
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def _patch_document_rels(raw: bytes) -> bytes:
    """Add a ``relationships/comments`` relationship pointing to ``comments.xml``."""
    tree = etree.fromstring(raw)
    ns = {"r": _REL_NS}
    for rel in tree.findall("r:Relationship", ns):
        if rel.get("Type") == _COMMENTS_REL_TYPE:
            return raw
    existing_ids = {rel.get("Id") for rel in tree.findall("r:Relationship", ns)}
    # Pick a fresh Id ("rIdNNN").
    n = 100
    while f"rId{n}" in existing_ids:
        n += 1
    new = etree.SubElement(tree, f"{{{_REL_NS}}}Relationship")
    new.set("Id", f"rId{n}")
    new.set("Type", _COMMENTS_REL_TYPE)
    new.set("Target", "comments.xml")
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def _inject_docx_comments(docx_bytes: bytes, comments: list[Comment]) -> bytes:
    """Rebuild ``docx_bytes`` with sentinel markers replaced by native Word comments."""
    if not comments:
        return docx_bytes
    id_for = {c.uuid: str(i) for i, c in enumerate(comments)}

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zin:
        parts: dict[str, bytes] = {name: zin.read(name) for name in zin.namelist()}

    doc_xml = parts.get("word/document.xml")
    if doc_xml is None:
        log.warning("DOCX export: document.xml missing; skipping comment injection")
        return docx_bytes

    doc_root = etree.fromstring(doc_xml)
    _split_runs_for_markers(doc_root, id_for)
    parts["word/document.xml"] = etree.tostring(
        doc_root, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    parts["word/comments.xml"] = _build_comments_xml(comments, id_for)

    if "[Content_Types].xml" in parts:
        parts["[Content_Types].xml"] = _patch_content_types(parts["[Content_Types].xml"])
    if "word/_rels/document.xml.rels" in parts:
        parts["word/_rels/document.xml.rels"] = _patch_document_rels(
            parts["word/_rels/document.xml.rels"]
        )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return out.getvalue()


# --- Format dispatchers --------------------------------------------------

def _html_to_plaintext(html_body: str) -> str:
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setHtml(html_body or "")
    return doc.toPlainText()


def _html_to_rtf(html_body: str, comments: list[Comment]) -> str:
    """HTML → RTF text, with comment annotations embedded."""
    if not has_pandoc():
        raise DocExportError("pandoc is required for RTF export.")
    wrapped = _wrap_comment_markers_in_html(html_body or "<p></p>", comments)
    # --standalone: without it pandoc emits an RTF *fragment* (no
    # \rtf1 header, no font/color tables); most RTF readers (Scrivener,
    # Word) reject fragments and render an empty body.
    rtf = run_pandoc(wrapped, "html", "rtf", extra_args=("-s",))
    if rtf is None:
        raise DocExportError("pandoc failed to produce RTF.")
    return _inject_rtf_annotations(rtf, comments)


def export_document(
    html_body: str,
    comments: Iterable[Comment],
    target_path: Path,
    fmt: str,
) -> None:
    """Write ``html_body`` to ``target_path`` in the requested format.

    ``fmt`` is one of :data:`EXPORT_FORMATS`. Raises :class:`DocExportError`
    on any failure.
    """
    fmt = fmt.upper()
    if fmt not in EXPORT_FORMATS:
        raise DocExportError(f"Unsupported export format: {fmt}")
    target_path = Path(target_path)
    comments_list = list(comments)

    if fmt == "TXT":
        target_path.write_text(_html_to_plaintext(html_body), encoding="utf-8")
        return

    if fmt == "RTF":
        rtf = _html_to_rtf(html_body, comments_list)
        target_path.write_text(rtf, encoding="utf-8")
        return

    if fmt in ("DOCX", "ODT"):
        if not has_pandoc():
            raise DocExportError(f"pandoc is required for {fmt} export.")
        to_fmt = fmt.lower()
        # DOCX carries real Word comments; ODT strips them for now.
        if fmt == "DOCX" and comments_list:
            source_html = _wrap_comment_markers_in_html(html_body or "<p></p>", comments_list)
        else:
            source_html = html_body or "<p></p>"
        raw = run_pandoc_bytes(source_html, "html", to_fmt)
        if raw is None:
            raise DocExportError(f"pandoc failed to produce {fmt}.")
        if fmt == "DOCX" and comments_list:
            raw = _inject_docx_comments(raw, comments_list)
        target_path.write_bytes(raw)
        if fmt == "ODT" and comments_list:
            log.info(
                "ODT export: %d comment(s) stripped (ODT annotation emitter not yet implemented).",
                len(comments_list),
            )
        return

    if fmt == "DOC":
        if not has_pandoc():
            raise DocExportError("pandoc is required for DOC export.")
        if not has_soffice():
            raise DocExportError(
                "LibreOffice (soffice) is required for DOC export but was not found."
            )
        # HTML → DOCX via pandoc (with comment markers injected), then
        # DOCX → DOC via LibreOffice headless. LibreOffice preserves Word
        # comments across the conversion.
        if comments_list:
            source_html = _wrap_comment_markers_in_html(html_body or "<p></p>", comments_list)
        else:
            source_html = html_body or "<p></p>"
        raw_docx = run_pandoc_bytes(source_html, "html", "docx")
        if raw_docx is None:
            raise DocExportError("pandoc failed on the DOCX intermediate for DOC export.")
        if comments_list:
            raw_docx = _inject_docx_comments(raw_docx, comments_list)
        with tempfile.TemporaryDirectory(prefix="skribe-doc-export-") as tmp:
            tmp_dir = Path(tmp)
            intermediate = tmp_dir / "intermediate.docx"
            intermediate.write_bytes(raw_docx)
            produced = soffice_convert(intermediate, "doc", out_dir=tmp_dir)
            if produced is None or not produced.is_file():
                raise DocExportError("LibreOffice failed to convert DOCX to DOC.")
            target_path.write_bytes(produced.read_bytes())
        return

    raise DocExportError(f"Unhandled format: {fmt}")  # defensive
