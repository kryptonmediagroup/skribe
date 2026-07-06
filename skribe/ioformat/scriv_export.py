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
import io
import logging
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lxml import etree

from skribe.ioformat.doc_convert import has_pandoc, run_pandoc
from skribe.ioformat.doc_export import _inject_rtf_annotations, _wrap_comment_markers_in_html
from skribe.ioformat.skribe_io import read_comments, read_document_body
from skribe.model.comment import Comment
from skribe.model.project import BinderItem, CustomFieldDef, CustomFieldType, ItemType, LabelDef, Project, StatusDef

log = logging.getLogger(__name__)

SCRIV_VERSION = "2.0"
SCRIV_FILES_VERSION = "23"
SCRIV_CREATOR = "Skribe"


class ScrivExportError(Exception):
    """Raised when a .scriv export cannot complete."""


# --- type / date mapping -------------------------------------------------
# Scrivener's Fiction template recognizes Characters/Places/Front Matter/
# Notes/Template Sheets folders by their title rather than by a dedicated
# Type attribute — the canonical .scrivx files we've inspected emit plain
# Type="Folder" for those containers and let Scrivener apply the
# special-folder icon at load time. Routing our root-container types
# through "Folder" preserves that behavior on a Skribe→.scriv round trip.
_TYPE_TO_SCRIV = {
    ItemType.DRAFT_FOLDER: "DraftFolder",
    ItemType.RESEARCH_FOLDER: "ResearchFolder",
    ItemType.TRASH_FOLDER: "TrashFolder",
    ItemType.CHARACTERS_FOLDER: "Folder",
    ItemType.PLACES_FOLDER: "Folder",
    ItemType.FRONT_MATTER_FOLDER: "Folder",
    ItemType.NOTES_FOLDER: "Folder",
    ItemType.TEMPLATE_SHEETS_FOLDER: "Folder",
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


def _hex_to_scriv_color(hex_color: str) -> str:
    """Convert '#FD3938' to '0.992 0.224 0.220'."""
    hex_color = hex_color.lstrip("#")
    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return f"{r:.3f} {g:.3f} {b:.3f}"
    except (ValueError, IndexError):
        return "0.800 0.800 0.800"


def _build_label_settings(label_defs: list[LabelDef]) -> etree._Element:
    ls = etree.Element("LabelSettings")
    etree.SubElement(ls, "Title").text = "Label"
    etree.SubElement(ls, "DefaultLabelID").text = "-1"
    labels = etree.SubElement(ls, "Labels")
    # Scrivener uses "-1" as the "no label" sentinel. Skribe defaults use "0"
    # for the same purpose; remap on export so there's exactly one sentinel entry.
    has_sentinel = any(ld.id in ("-1", "0") for ld in label_defs)
    if not has_sentinel:
        etree.SubElement(labels, "Label", ID="-1").text = "No Label"
    for ld in label_defs:
        scriv_id = "-1" if ld.id == "0" else ld.id
        el = etree.SubElement(labels, "Label", ID=scriv_id, Color=_hex_to_scriv_color(ld.color))
        el.text = ld.name
    return ls


def _build_status_settings(status_defs: list[StatusDef]) -> etree._Element:
    ss = etree.Element("StatusSettings")
    etree.SubElement(ss, "Title").text = "Status"
    etree.SubElement(ss, "DefaultStatusID").text = "-1"
    items = etree.SubElement(ss, "StatusItems")
    has_sentinel = any(sd.id in ("-1", "0") for sd in status_defs)
    if not has_sentinel:
        etree.SubElement(items, "Status", ID="-1").text = "No Status"
    for sd in status_defs:
        scriv_id = "-1" if sd.id == "0" else sd.id
        etree.SubElement(items, "Status", ID=scriv_id).text = sd.name
    return ss


def _build_custom_meta_settings(field_defs: list[CustomFieldDef]) -> Optional[etree._Element]:
    if not field_defs:
        return None
    cms = etree.Element("CustomMetaDataSettings")
    for fd in field_defs:
        mdf = etree.SubElement(cms, "MetaDataField",
            Type=fd.field_type.value.capitalize(),
            ID=fd.id,
            Wraps="Yes",
            Color="0.800 0.800 0.800",
            Align="Left")
        etree.SubElement(mdf, "Title").text = fd.name
    return cms


def _build_collections() -> etree._Element:
    cols = etree.Element("Collections")
    binder_col = etree.SubElement(cols, "Collection",
        Type="Binder",
        ID=str(uuid.uuid4()).upper(),
        Color="0.941176 0.937255 0.956863")
    etree.SubElement(binder_col, "Title").text = "Binder"
    search_col = etree.SubElement(cols, "Collection",
        Type="RecentSearch",
        ID=str(uuid.uuid4()).upper(),
        Color="0.901961 0.901961 0.980392")
    etree.SubElement(search_col, "Title").text = "Search Results"
    etree.SubElement(search_col, "SearchSettings",
        Operator="Any", Type="All", Scope="All",
        CompileSetting="All", CaseSensitive="No", IgnoreDiacritics="No")
    return cols


# --- Binder XML builder --------------------------------------------------

_NO_TEXT_SETTINGS = {
    ItemType.DRAFT_FOLDER,
    ItemType.RESEARCH_FOLDER,
    ItemType.TRASH_FOLDER,
    ItemType.CHARACTERS_FOLDER,
    ItemType.PLACES_FOLDER,
    ItemType.FRONT_MATTER_FOLDER,
    ItemType.NOTES_FOLDER,
    ItemType.TEMPLATE_SHEETS_FOLDER,
}


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
    custom = meta.get("custom")
    if custom:
        valid = {fid: val for fid, val in custom.items() if fid}
        if valid:
            cmd_el = etree.SubElement(el, "CustomMetaData")
            for field_id, value in valid.items():
                mdi = etree.SubElement(cmd_el, "MetaDataItem")
                etree.SubElement(mdi, "FieldID").text = field_id
                etree.SubElement(mdi, "Value").text = str(value)
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
    if item.title:
        etree.SubElement(el, "Title").text = item.title

    meta_el = _build_metadata_element(item.metadata or {})
    if meta_el is not None:
        el.append(meta_el)
    else:
        el.append(etree.Element("MetaData"))

    if item.type not in _NO_TEXT_SETTINGS:
        ts = etree.SubElement(el, "TextSettings")
        etree.SubElement(ts, "TextSelection").text = "0,0"

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
    root.append(_build_collections())
    root.append(_build_label_settings(project.label_defs))
    root.append(_build_status_settings(project.status_defs))
    cms = _build_custom_meta_settings(project.custom_field_defs)
    if cms is not None:
        root.append(cms)
    # lxml produces single-quoted XML declaration; Scrivener expects double-quoted.
    xml_bytes = etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return xml_bytes.replace(b"<?xml version='1.0' encoding='UTF-8'?>",
                             b'<?xml version="1.0" encoding="UTF-8"?>', 1)


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

    # Empty placeholder that Scrivener creates on open; must exist so
    # Scrivener doesn't treat its absence as a corrupt bundle.
    (files_dir / "binder.autosave").touch()

    # Per-document RTFs + synopses.
    # binder.backup and scrivx_bytes are written after the scrivx is built.
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
    scrivx_bytes = _build_scrivx(project)
    scrivx_path.write_bytes(scrivx_bytes)

    # binder.backup: ZIP archive containing the .scrivx. Scrivener reads
    # this as the authoritative binder state; without it some versions
    # treat the bundle as corrupt and refuse to open it.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(scrivx_name, scrivx_bytes)
    (files_dir / "binder.backup").write_bytes(buf.getvalue())

    if skipped:
        log.warning(
            "scriv export: %d document(s) skipped because RTF conversion failed: %s",
            len(skipped), ", ".join(skipped[:5]) + ("…" if len(skipped) > 5 else ""),
        )

    return out_scriv
