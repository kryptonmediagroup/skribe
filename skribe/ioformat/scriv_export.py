"""Export a Skribe project as a Scrivener 3–compatible .scriv bundle.

The bundle matches the structure observed in the user's actual Scrivener
projects closely enough for Scrivener 3 (Windows) to open it. UUIDs
imported from an original .scriv round-trip unchanged, so the same
project identity survives Scrivener → Skribe → Scrivener.

Scope of this first pass:
- Writes the binder tree (all items, preserving UUIDs).
- Writes content.rtf + synopsis.txt for each TEXT item.
- Emits known metadata (IncludeInCompile, LabelID, StatusID).
- Custom/unknown metadata is dropped for now (safer than guessing its
  exact XML shape).

Out of scope here (Scrivener will fill in defaults or ignore):
- LabelSettings/StatusSettings definitions, ProjectTargets, PrintSettings,
  writing.history, search.indexes, Settings/ XML. Add if round-trip
  testing shows Scrivener needs them.
"""
from __future__ import annotations

import html as _html
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lxml import etree

from skribe.ioformat.doc_convert import has_pandoc, run_pandoc
from skribe.ioformat.doc_export import _inject_rtf_annotations, _wrap_comment_markers_in_html
from skribe.ioformat.skribe_io import read_comments, read_document_body
from skribe.model.comment import Comment
from skribe.model.project import BinderItem, ItemType, Project

log = logging.getLogger(__name__)

SCRIV_VERSION = "2.0"
SCRIV_FILES_VERSION = "23"
SCRIV_CREATOR = "Skribe"


class ScrivExportError(Exception):
    """Raised when a .scriv export cannot complete."""


# --- type / date mapping -------------------------------------------------

_TYPE_TO_SCRIV = {
    ItemType.DRAFT_FOLDER: "DraftFolder",
    ItemType.RESEARCH_FOLDER: "ResearchFolder",
    ItemType.TRASH_FOLDER: "TrashFolder",
    ItemType.FOLDER: "Folder",
    ItemType.TEXT: "Text",
}


def _to_scriv_date(iso_dt: str) -> str:
    """Convert our ISO-8601 timestamp to Scrivener's ``%Y-%m-%d %H:%M:%S %z``."""
    if not iso_dt:
        return ""
    try:
        dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except ValueError:
        return iso_dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


# --- Binder XML builder --------------------------------------------------

def _build_metadata_element(meta: dict) -> Optional[etree._Element]:
    """Produce a ``<MetaData>`` element from our stored dict, or None if empty."""
    el = etree.Element("MetaData")
    wrote_anything = False
    if "include_in_compile" in meta:
        sub = etree.SubElement(el, "IncludeInCompile")
        sub.text = "Yes" if meta.get("include_in_compile") else "No"
        wrote_anything = True
    label_id = meta.get("label_id")
    if label_id:
        sub = etree.SubElement(el, "LabelID")
        sub.text = str(label_id)
        wrote_anything = True
    status_id = meta.get("status_id")
    if status_id:
        sub = etree.SubElement(el, "StatusID")
        sub.text = str(status_id)
        wrote_anything = True
    return el if wrote_anything else None


def _build_binder_item_element(item: BinderItem) -> etree._Element:
    scriv_type = _TYPE_TO_SCRIV.get(item.type, "Text")
    el = etree.Element(
        "BinderItem",
        UUID=item.uuid,
        Type=scriv_type,
        Created=_to_scriv_date(item.created),
        Modified=_to_scriv_date(item.modified),
    )
    title_el = etree.SubElement(el, "Title")
    title_el.text = item.title or ""

    meta_el = _build_metadata_element(item.metadata or {})
    if meta_el is not None:
        el.append(meta_el)
    else:
        el.append(etree.Element("MetaData"))  # empty but present, matches sample

    if item.children:
        children_el = etree.SubElement(el, "Children")
        for child in item.children:
            children_el.append(_build_binder_item_element(child))

    return el


def _build_scrivx(project: Project) -> bytes:
    """Produce the .scrivx document as pretty-printed UTF-8 bytes."""
    root = etree.Element(
        "ScrivenerProject",
        Template="No",
        Version=SCRIV_VERSION,
        Identifier=project.identifier,
        Creator=SCRIV_CREATOR,
        Modified=_to_scriv_date(project.modified),
    )
    binder = etree.SubElement(root, "Binder")
    for top in project.roots:
        binder.append(_build_binder_item_element(top))
    return etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )


# --- HTML → RTF -----------------------------------------------------------

def _html_to_content_rtf(html_body: str, comments: list[Comment]) -> Optional[str]:
    """Render an HTML body (plus comment annotations) to RTF text.

    Returns ``None`` if pandoc is unavailable or refuses the input — the
    caller should treat this as a skip rather than a fatal error, so a
    single bad document doesn't abort the whole project export.
    """
    if not has_pandoc():
        return None
    wrapped = _wrap_comment_markers_in_html(html_body or "<p></p>", comments)
    # --standalone: without it pandoc emits an RTF *fragment* (no
    # \rtf1 header, no font/color tables); Scrivener treats fragments
    # as malformed and renders the document body as empty.
    rtf = run_pandoc(wrapped, "html", "rtf", extra_args=("-s",))
    if rtf is None:
        return None
    return _inject_rtf_annotations(rtf, comments)


# --- Public entry point --------------------------------------------------

def export_scriv(project: Project, out_scriv: Path) -> Path:
    """Write ``project`` to ``out_scriv`` as a .scriv bundle.

    The caller is responsible for deciding whether to overwrite an
    existing target. If ``out_scriv`` already exists it is removed first.
    Returns the resolved output path.
    """
    if project.path is None:
        raise ScrivExportError("Project has no source bundle path; save it first.")
    bundle_src = project.path
    out_scriv = Path(out_scriv)

    if out_scriv.suffix.lower() != ".scriv":
        out_scriv = out_scriv.with_suffix(".scriv")

    if out_scriv.exists():
        if out_scriv.is_dir():
            shutil.rmtree(out_scriv)
        else:
            out_scriv.unlink()
    out_scriv.mkdir(parents=True)
    files_dir = out_scriv / "Files"
    data_dir = files_dir / "Data"
    data_dir.mkdir(parents=True)

    # Auxiliary directories Scrivener expects at reload time. They may stay
    # empty — Scrivener regenerates their contents on first open — but the
    # directories themselves must exist or the project is rejected as
    # malformed.
    (files_dir / "ProjectNotes").mkdir(exist_ok=True)
    (out_scriv / "Icons").mkdir(exist_ok=True)
    (out_scriv / "Snapshots").mkdir(exist_ok=True)
    settings_dir = out_scriv / "Settings"
    settings_dir.mkdir(exist_ok=True)
    (settings_dir / "Compile Formats").mkdir(exist_ok=True)

    # version marker
    (files_dir / "version.txt").write_text(SCRIV_FILES_VERSION, encoding="utf-8")

    # Per-document RTFs + synopses.
    skipped: list[str] = []
    for item in project.walk():
        if item.type.is_container and not item.synopsis:
            continue
        item_dir = data_dir / item.uuid
        item_dir.mkdir(parents=True, exist_ok=True)

        if item.synopsis:
            (item_dir / "synopsis.txt").write_text(item.synopsis, encoding="utf-8")

        if item.type is ItemType.TEXT:
            body = read_document_body(bundle_src, item.uuid)
            comments = read_comments(bundle_src, item.uuid)
            rtf = _html_to_content_rtf(body, comments)
            if rtf is None:
                skipped.append(item.title or item.uuid)
                continue
            (item_dir / "content.rtf").write_text(rtf, encoding="utf-8")

    # .scrivx at the project root. Use the project name as the filename.
    scrivx_name = (project.name or "Skribe Project") + ".scrivx"
    scrivx_path = out_scriv / scrivx_name
    scrivx_path.write_bytes(_build_scrivx(project))

    if skipped:
        log.warning(
            "scriv export: %d document(s) skipped because RTF conversion failed: %s",
            len(skipped), ", ".join(skipped[:5]) + ("…" if len(skipped) > 5 else ""),
        )

    return out_scriv
