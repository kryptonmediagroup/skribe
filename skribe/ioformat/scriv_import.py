"""Import a Scrivener 3 .scriv project bundle into a Skribe project.

RTF conversion uses pandoc when available (preserves bold/italic/lists/
alignment) and falls back to striprtf (text-only) otherwise. The pandoc
subprocess plumbing lives in :mod:`skribe.ioformat.doc_convert` so it can
be shared with per-document import/export.

Known limits:
- Label IDs, Status IDs, keywords, and custom metadata are copied verbatim
  into ``metadata`` but not yet resolved against their lookup tables.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from lxml import etree

from skribe.ioformat.doc_convert import rtf_to_html_fallback, rtf_to_html_pandoc
from skribe.ioformat.skribe_io import save_project, write_document_body
from skribe.model.project import BinderItem, ItemType, Project

log = logging.getLogger(__name__)


# Scrivener 3 Type attribute → our ItemType
_TYPE_MAP = {
    "DraftFolder": ItemType.DRAFT_FOLDER,
    "ResearchFolder": ItemType.RESEARCH_FOLDER,
    "TrashFolder": ItemType.TRASH_FOLDER,
    "Folder": ItemType.FOLDER,
    "Text": ItemType.TEXT,
}


def _map_type(scriv_type: Optional[str], has_children: bool) -> ItemType:
    if scriv_type and scriv_type in _TYPE_MAP:
        return _TYPE_MAP[scriv_type]
    return ItemType.FOLDER if has_children else ItemType.TEXT


def _preprocess_scriv_rtf(raw: str) -> str:
    """Adjust quirks in Scrivener's RTF so pandoc produces prose, not code.

    Scrivener tags its default fonts as ``\\fmodern`` (fixed-pitch) in the
    font table, which causes pandoc to emit each paragraph as a ``<pre>``
    block. Rewriting the category to ``\\froman`` avoids this without
    affecting rendering semantics for our purposes.
    """
    return raw.replace("\\fmodern", "\\froman")


def _read_text_body(scriv_path: Path, uuid: str) -> str:
    rtf_file = scriv_path / "Files" / "Data" / uuid / "content.rtf"
    if not rtf_file.is_file():
        return ""
    try:
        raw = rtf_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    preprocessed = _preprocess_scriv_rtf(raw)
    pandoc_html = rtf_to_html_pandoc(preprocessed)
    if pandoc_html is not None:
        return pandoc_html
    return rtf_to_html_fallback(raw)


def _read_synopsis(scriv_path: Path, uuid: str) -> str:
    syn_file = scriv_path / "Files" / "Data" / uuid / "synopsis.txt"
    if not syn_file.is_file():
        return ""
    try:
        return syn_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _parse_metadata(meta_el: Optional[etree._Element]) -> dict:
    """Extract metadata fields we recognize."""
    meta: dict = {}
    if meta_el is None:
        return meta
    for child in meta_el:
        tag = etree.QName(child).localname
        text = (child.text or "").strip()
        if tag == "IncludeInCompile":
            meta["include_in_compile"] = text.lower() == "yes"
        elif tag == "LabelID":
            meta["label_id"] = text
        elif tag == "StatusID":
            meta["status_id"] = text
        elif tag == "CustomMetaData":
            custom = {}
            for cmd in child:
                key = cmd.get("FieldID") or ""
                custom[key] = (cmd.text or "").strip()
            if custom:
                meta["custom"] = custom
        else:
            meta.setdefault("_raw", {})[tag] = text
    return meta


def _convert_binder_item(
    el: etree._Element,
    scriv_path: Path,
    out_bundle: Path,
) -> BinderItem:
    uuid = el.get("UUID") or ""
    scriv_type = el.get("Type")
    created = el.get("Created") or ""
    modified = el.get("Modified") or ""

    title_el = el.find("Title")
    title = (title_el.text if title_el is not None else "") or ""

    children_el = el.find("Children")
    child_elements = list(children_el) if children_el is not None else []
    has_children = len(child_elements) > 0
    item_type = _map_type(scriv_type, has_children)

    meta = _parse_metadata(el.find("MetaData"))
    synopsis = _read_synopsis(scriv_path, uuid) if uuid else ""

    item = BinderItem(
        uuid=uuid or BinderItem().uuid,
        type=item_type,
        title=title,
        synopsis=synopsis,
        created=created,
        modified=modified,
        metadata=meta,
    )

    if item_type is ItemType.TEXT or not item_type.is_container:
        body = _read_text_body(scriv_path, uuid) if uuid else ""
        if body:
            write_document_body(out_bundle, item.uuid, body)

    for child_el in child_elements:
        child = _convert_binder_item(child_el, scriv_path, out_bundle)
        child.parent = item
        item.children.append(child)

    return item


def import_scriv(scriv_path: Path, out_bundle: Path) -> Project:
    """Import a .scriv project into a new .skribe bundle at ``out_bundle``."""
    scriv_path = Path(scriv_path)
    out_bundle = Path(out_bundle)
    if not scriv_path.is_dir():
        raise FileNotFoundError(f"Not a .scriv bundle: {scriv_path}")

    scrivx_files = list(scriv_path.glob("*.scrivx"))
    if not scrivx_files:
        raise FileNotFoundError(f"No .scrivx file found in {scriv_path}")
    scrivx = scrivx_files[0]

    tree = etree.parse(str(scrivx))
    root = tree.getroot()
    binder_el = root.find("Binder")
    if binder_el is None:
        raise ValueError(f"{scrivx}: missing <Binder> element")

    identifier = root.get("Identifier") or ""
    name = scriv_path.stem
    project = Project(name=name, identifier=identifier or Project().identifier)
    project.roots = []
    project.path = out_bundle

    for child_el in binder_el:
        item = _convert_binder_item(child_el, scriv_path, out_bundle)
        project.roots.append(item)

    # Ensure the standard three root containers exist, even if .scriv is minimal.
    have_types = {r.type for r in project.roots}
    if ItemType.DRAFT_FOLDER not in have_types:
        project.roots.insert(0, BinderItem(type=ItemType.DRAFT_FOLDER, title="Manuscript"))
    if ItemType.RESEARCH_FOLDER not in have_types:
        project.roots.append(BinderItem(type=ItemType.RESEARCH_FOLDER, title="Research"))
    if ItemType.TRASH_FOLDER not in have_types:
        project.roots.append(BinderItem(type=ItemType.TRASH_FOLDER, title="Trash"))

    save_project(project, out_bundle)
    return project
