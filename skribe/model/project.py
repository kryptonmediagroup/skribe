"""Core data model: Project, BinderItem, ItemType.

The Project owns an in-memory binder tree. Document bodies are not held in
memory — they live on disk under ``<bundle>/documents/<uuid>/content.html``
and are loaded on demand by the editor.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_uuid() -> str:
    return str(_uuid.uuid4()).upper()


class ItemType(str, Enum):
    DRAFT_FOLDER = "draft_folder"      # the Manuscript root
    RESEARCH_FOLDER = "research_folder"
    TRASH_FOLDER = "trash_folder"
    FOLDER = "folder"
    TEXT = "text"

    @property
    def is_container(self) -> bool:
        return self in {
            ItemType.DRAFT_FOLDER,
            ItemType.RESEARCH_FOLDER,
            ItemType.TRASH_FOLDER,
            ItemType.FOLDER,
        }

    @property
    def is_root_container(self) -> bool:
        return self in {
            ItemType.DRAFT_FOLDER,
            ItemType.RESEARCH_FOLDER,
            ItemType.TRASH_FOLDER,
        }


@dataclass
class LabelDef:
    id: str
    name: str
    color: str = "#CCCCCC"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color}

    @classmethod
    def from_dict(cls, d: dict) -> "LabelDef":
        return cls(id=d["id"], name=d["name"], color=d.get("color", "#CCCCCC"))


@dataclass
class StatusDef:
    id: str
    name: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> "StatusDef":
        return cls(id=d["id"], name=d["name"])



class CustomFieldType(str, Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    LIST = "list"
    DATE = "date"


@dataclass
class CustomFieldDef:
    """A user-defined metadata field that appears as an outliner column."""
    id: str = field(default_factory=_new_uuid)
    name: str = ""
    field_type: CustomFieldType = CustomFieldType.TEXT
    default: str = ""
    choices: list[str] = field(default_factory=list)
    color: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "name": self.name,
            "field_type": self.field_type.value,
        }
        if self.default:
            d["default"] = self.default
        if self.choices:
            d["choices"] = self.choices
        if self.color:
            d["color"] = self.color
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CustomFieldDef":
        ft = CustomFieldType.TEXT
        raw_type = d.get("field_type", "text")
        try:
            ft = CustomFieldType(raw_type)
        except ValueError:
            pass
        return cls(
            id=d.get("id", _new_uuid()),
            name=d.get("name", ""),
            field_type=ft,
            default=d.get("default", ""),
            choices=d.get("choices", []),
            color=d.get("color", ""),
        )

def default_label_defs() -> list[LabelDef]:
    return [
        LabelDef("0", "No Label", "#CCCCCC"),
        LabelDef("1", "Red", "#FC3D39"),
        LabelDef("2", "Orange", "#FF9500"),
        LabelDef("3", "Yellow", "#FFCC00"),
        LabelDef("4", "Green", "#4CD964"),
        LabelDef("5", "Blue", "#5AC8FA"),
        LabelDef("6", "Purple", "#5856D6"),
    ]


def default_status_defs() -> list[StatusDef]:
    return [
        StatusDef("0", "No Status"),
        StatusDef("1", "To Do"),
        StatusDef("2", "In Progress"),
        StatusDef("3", "First Draft"),
        StatusDef("4", "Revised Draft"),
        StatusDef("5", "Final Draft"),
        StatusDef("6", "Done"),
    ]


@dataclass
class BinderItem:
    uuid: str = field(default_factory=_new_uuid)
    type: ItemType = ItemType.TEXT
    title: str = ""
    synopsis: str = ""
    created: str = field(default_factory=_now_iso)
    modified: str = field(default_factory=_now_iso)
    metadata: dict = field(default_factory=dict)
    children: list["BinderItem"] = field(default_factory=list)
    parent: Optional["BinderItem"] = field(default=None, repr=False, compare=False)

    def touch(self) -> None:
        self.modified = _now_iso()

    def add_child(self, item: "BinderItem", index: Optional[int] = None) -> None:
        if not self.type.is_container:
            raise ValueError(f"{self.type} cannot have children")
        item.parent = self
        if index is None or index >= len(self.children):
            self.children.append(item)
        else:
            self.children.insert(index, item)
        self.touch()

    def remove_child(self, item: "BinderItem") -> None:
        self.children.remove(item)
        item.parent = None
        self.touch()

    def walk(self) -> Iterator["BinderItem"]:
        """Depth-first traversal yielding self then all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, uuid: str) -> Optional["BinderItem"]:
        for item in self.walk():
            if item.uuid == uuid:
                return item
        return None

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "type": self.type.value,
            "title": self.title,
            "synopsis": self.synopsis,
            "created": self.created,
            "modified": self.modified,
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BinderItem":
        children_data = data.get("children", [])
        item = cls(
            uuid=data["uuid"],
            type=ItemType(data["type"]),
            title=data.get("title", ""),
            synopsis=data.get("synopsis", ""),
            created=data.get("created", _now_iso()),
            modified=data.get("modified", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )
        for child_data in children_data:
            child = cls.from_dict(child_data)
            child.parent = item
            item.children.append(child)
        return item


@dataclass
class Project:
    """A Skribe project. ``path`` is the .skribe bundle directory on disk."""

    FORMAT_VERSION = 1

    name: str = "Untitled"
    identifier: str = field(default_factory=_new_uuid)
    created: str = field(default_factory=_now_iso)
    modified: str = field(default_factory=_now_iso)
    roots: list[BinderItem] = field(default_factory=list)
    path: Optional[Path] = None
    label_defs: list[LabelDef] = field(default_factory=default_label_defs)
    status_defs: list[StatusDef] = field(default_factory=default_status_defs)
    custom_field_defs: list[CustomFieldDef] = field(default_factory=list)

    @classmethod
    def new(cls, name: str = "Untitled") -> "Project":
        """Create a project with the standard three root containers."""
        project = cls(name=name)
        manuscript = BinderItem(type=ItemType.DRAFT_FOLDER, title="Manuscript")
        research = BinderItem(type=ItemType.RESEARCH_FOLDER, title="Research")
        trash = BinderItem(type=ItemType.TRASH_FOLDER, title="Trash")
        project.roots = [manuscript, research, trash]
        return project

    def touch(self) -> None:
        self.modified = _now_iso()

    def walk(self) -> Iterator[BinderItem]:
        for root in self.roots:
            yield from root.walk()

    def find(self, uuid: str) -> Optional[BinderItem]:
        for root in self.roots:
            hit = root.find(uuid)
            if hit is not None:
                return hit
        return None

    def root_draft(self) -> Optional[BinderItem]:
        for r in self.roots:
            if r.type is ItemType.DRAFT_FOLDER:
                return r
        return None

    def root_research(self) -> Optional[BinderItem]:
        for r in self.roots:
            if r.type is ItemType.RESEARCH_FOLDER:
                return r
        return None

    def root_trash(self) -> Optional[BinderItem]:
        for r in self.roots:
            if r.type is ItemType.TRASH_FOLDER:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "version": self.FORMAT_VERSION,
            "name": self.name,
            "identifier": self.identifier,
            "created": self.created,
            "modified": self.modified,
            "binder": [r.to_dict() for r in self.roots],
            "label_defs": [ld.to_dict() for ld in self.label_defs],
            "status_defs": [sd.to_dict() for sd in self.status_defs],
            "custom_field_defs": [f.to_dict() for f in self.custom_field_defs],
        }

    @classmethod
    def from_dict(cls, data: dict, path: Optional[Path] = None) -> "Project":
        project = cls(
            name=data.get("name", "Untitled"),
            identifier=data.get("identifier", _new_uuid()),
            created=data.get("created", _now_iso()),
            modified=data.get("modified", _now_iso()),
            path=path,
        )
        project.roots = [BinderItem.from_dict(r) for r in data.get("binder", [])]
        project.label_defs = [LabelDef.from_dict(d) for d in data.get("label_defs", [])] or default_label_defs()
        project.status_defs = [StatusDef.from_dict(d) for d in data.get("status_defs", [])] or default_status_defs()
        project.custom_field_defs = [CustomFieldDef.from_dict(d) for d in data.get("custom_field_defs", [])]
        return project

    def label_for_id(self, label_id: str) -> Optional[LabelDef]:
        for ld in self.label_defs:
            if ld.id == label_id:
                return ld
        return None

    def status_for_id(self, status_id: str) -> Optional[StatusDef]:
        for sd in self.status_defs:
            if sd.id == status_id:
                return sd
        return None


    def custom_field_for_id(self, field_id: str) -> Optional[CustomFieldDef]:
        for f in self.custom_field_defs:
            if f.id == field_id:
                return f
        return None