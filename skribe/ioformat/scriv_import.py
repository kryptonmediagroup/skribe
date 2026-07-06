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
from skribe.model.project import BinderItem, CustomFieldDef, CustomFieldType, ItemType, LabelDef, Project, StatusDef, default_label_defs, default_status_defs

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


# Scrivener's Fiction template doesn't tag Characters/Places/Front Matter/
# Notes/Template Sheets with a dedicated Type attribute — it leaves them as
# Type="Folder" and lets the title carry the meaning. Match titles
# case-insensitively so we round-trip the special-folder icon when users
# rename (e.g. "characters") and still recover the canonical type on import.
_TITLE_TO_TYPE = {
    "characters": ItemType.CHARACTERS_FOLDER,
    "places": ItemType.PLACES_FOLDER,
    "front matter": ItemType.FRONT_MATTER_FOLDER,
    "notes": ItemType.NOTES_FOLDER,
    "template sheets": ItemType.TEMPLATE_SHEETS_FOLDER,
}

def _classify_root_container(title: str, fallback: ItemType) -> ItemType:
    """Promote a plain FOLDER to a special root-container type by title."""
    if fallback is not ItemType.FOLDER:
        return fallback
    return _TITLE_TO_TYPE.get(title.strip().lower(), fallback)



def _scriv_color_to_hex(color_str: str) -> str:
    """Convert '0.993 0.224 0.22' to '#FD3938'."""
    try:
        parts = color_str.strip().split()
        r = int(float(parts[0]) * 255 + 0.5)
        g = int(float(parts[1]) * 255 + 0.5)
        b = int(float(parts[2]) * 255 + 0.5)
        return f"#{r:02X}{g:02X}{b:02X}"
    except (ValueError, IndexError):
        return "#CCCCCC"


def _parse_label_settings(root: etree._Element) -> list[LabelDef]:
    """Extract label definitions from the .scrivx root."""
    ls = root.find(".//LabelSettings/Labels")
    if ls is None:
        return default_label_defs()
    defs = []
    for el in ls.findall("Label"):
        lid = el.get("ID", "")
        color_str = el.get("Color", "")
        name = (el.text or "").strip()
        color = _scriv_color_to_hex(color_str) if color_str else "#CCCCCC"
        defs.append(LabelDef(id=lid, name=name or "Untitled", color=color))
    return defs or default_label_defs()


def _parse_status_settings(root: etree._Element) -> list[StatusDef]:
    """Extract status definitions from the .scrivx root."""
    ss = root.find(".//StatusSettings/StatusItems")
    if ss is None:
        return default_status_defs()
    defs = []
    for el in ss.findall("Status"):
        sid = el.get("ID", "")
        name = (el.text or "").strip()
        defs.append(StatusDef(id=sid, name=name or "Untitled"))
    return defs or default_status_defs()

def _parse_custom_meta_settings(root: etree._Element) -> list[CustomFieldDef]:
    """Extract custom metadata field definitions from the .scrivx root."""
    cms = root.find(".//CustomMetaDataSettings")
    if cms is None:
        return []
    defs = []
    for el in cms.findall("MetaDataField"):
        fid = ""
        name = ""
        ft = CustomFieldType.TEXT
        default = ""
        choices: list[str] = []
        for child in el:
            tag = etree.QName(child).localname
            text = (child.text or "").strip()
            if tag == "FieldID":
                fid = text
            elif tag == "Title":
                name = text
            elif tag == "Type":
                try:
                    ft = CustomFieldType(text.lower())
                except ValueError:
                    ft = CustomFieldType.TEXT
            elif tag == "DefaultValue":
                default = text
            elif tag == "ListValues":
                for lv in child.findall("ListValue"):
                    choices.append((lv.text or "").strip())
        if fid:
            defs.append(CustomFieldDef(
                id=fid, name=name, field_type=ft,
                default=default, choices=choices,
            ))
    return defs

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
    # Root containers in Scrivener come across as plain Type="Folder" with a
    # well-known title — promote them so the binder shows the right icon
    # and round-trip exports don't lose the special-folder status.
    item_type = _classify_root_container(title, item_type)

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

    project.label_defs = _parse_label_settings(root)
    project.status_defs = _parse_status_settings(root)
    project.custom_field_defs = _parse_custom_meta_settings(root)

    save_project(project, out_bundle)
    return project
