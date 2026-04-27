"""Qt tree model wrapping a Skribe Project's binder.

One column: the item title. Item data carries the UUID of the underlying
BinderItem so views can resolve back to the model object.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QAbstractItemModel,
    QMimeData,
    QModelIndex,
    Qt,
)
from PySide6.QtGui import QIcon

from skribe.model.project import BinderItem, ItemType, Project

UUID_ROLE = Qt.UserRole + 1
TYPE_ROLE = Qt.UserRole + 2
SYNOPSIS_ROLE = Qt.UserRole + 3

BINDER_MIME = "application/x-skribe-binder-uuid"


class BinderModel(QAbstractItemModel):
    def __init__(self, project: Optional[Project] = None, parent=None):
        super().__init__(parent)
        self._project = project or Project.new()
        # Icons — use Qt's standard pixmaps as placeholders until we ship art.
        from PySide6.QtWidgets import QApplication, QStyle
        style = QApplication.style()
        self._icons = {
            ItemType.DRAFT_FOLDER: style.standardIcon(QStyle.SP_DirIcon),
            ItemType.RESEARCH_FOLDER: style.standardIcon(QStyle.SP_DirLinkIcon),
            ItemType.TRASH_FOLDER: style.standardIcon(QStyle.SP_TrashIcon),
            ItemType.FOLDER: style.standardIcon(QStyle.SP_DirIcon),
            ItemType.TEXT: style.standardIcon(QStyle.SP_FileIcon),
        }

    # --- project lifecycle ---

    def project(self) -> Project:
        return self._project

    def set_project(self, project: Project) -> None:
        self.beginResetModel()
        self._project = project
        self.endResetModel()

    def item_from_index(self, index: QModelIndex) -> Optional[BinderItem]:
        if not index.isValid():
            return None
        return index.internalPointer()

    def index_for_item(self, item: BinderItem) -> QModelIndex:
        parent = item.parent
        if parent is None:
            row = self._project.roots.index(item)
            return self.createIndex(row, 0, item)
        row = parent.children.index(item)
        return self.createIndex(row, 0, item)

    # --- tree navigation ---

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            if row < len(self._project.roots):
                return self.createIndex(row, column, self._project.roots[row])
            return QModelIndex()
        parent_item: BinderItem = parent.internalPointer()
        if row < len(parent_item.children):
            return self.createIndex(row, column, parent_item.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item: BinderItem = index.internalPointer()
        parent_item = item.parent
        if parent_item is None:
            return QModelIndex()
        grand = parent_item.parent
        if grand is None:
            row = self._project.roots.index(parent_item)
        else:
            row = grand.children.index(parent_item)
        return self.createIndex(row, 0, parent_item)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._project.roots)
        item: BinderItem = parent.internalPointer()
        return len(item.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    # --- data / flags ---

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        item: BinderItem = index.internalPointer()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return item.title or "(untitled)"
        if role == Qt.DecorationRole:
            return self._icons.get(item.type, QIcon())
        if role == UUID_ROLE:
            return item.uuid
        if role == TYPE_ROLE:
            return item.type.value
        if role == SYNOPSIS_ROLE:
            return item.synopsis or ""
        if role == Qt.ToolTipRole:
            return f"{item.type.value}  ·  {item.uuid}"
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        item: BinderItem = index.internalPointer()
        new_title = str(value).strip()
        if new_title == item.title:
            return False
        item.title = new_title
        item.touch()
        self._project.touch()
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            # Permit dropping onto the empty space at the root level to no effect
            # (the model itself remains a stable row of root containers).
            return Qt.NoItemFlags
        item: BinderItem = index.internalPointer()
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not item.type.is_root_container:
            f |= Qt.ItemIsEditable | Qt.ItemIsDragEnabled
        if item.type.is_container:
            f |= Qt.ItemIsDropEnabled
        return f

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section == 0:
            return "Binder"
        return None

    # --- drag & drop ---

    def supportedDropActions(self) -> Qt.DropActions:
        return Qt.MoveAction

    def supportedDragActions(self) -> Qt.DropActions:
        return Qt.MoveAction

    def mimeTypes(self) -> list[str]:
        return [BINDER_MIME]

    def mimeData(self, indexes) -> QMimeData:
        uuids = []
        seen = set()
        for idx in indexes:
            if not idx.isValid() or idx.column() != 0:
                continue
            item: BinderItem = idx.internalPointer()
            if item.type.is_root_container:
                continue
            if item.uuid in seen:
                continue
            seen.add(item.uuid)
            uuids.append(item.uuid)
        md = QMimeData()
        md.setData(BINDER_MIME, "\n".join(uuids).encode("utf-8"))
        return md

    def canDropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if action != Qt.MoveAction:
            return False
        if not data.hasFormat(BINDER_MIME):
            return False
        # Only containers accept drops.
        if not parent.isValid():
            return False
        target: BinderItem = parent.internalPointer()
        if not target.type.is_container:
            return False
        # Reject if any source is an ancestor of the target (prevents cycles).
        for uuid in self._decode_uuids(data):
            src = self._project.find(uuid)
            if src is None:
                continue
            if src is target or self._is_descendant(src, target):
                return False
        return True

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        target: BinderItem = parent.internalPointer()
        insert_row = row if row >= 0 else len(target.children)
        moved_any = False
        for uuid in self._decode_uuids(data):
            src = self._project.find(uuid)
            if src is None or src.type.is_root_container:
                continue
            if src is target or self._is_descendant(src, target):
                continue
            if self._move_item(src, target, insert_row):
                # Next sibling in a multi-drop lands just after the previous.
                insert_row = target.children.index(src) + 1
                moved_any = True
        if moved_any:
            self._project.touch()
        return moved_any

    # --- dnd helpers ---

    @staticmethod
    def _decode_uuids(data: QMimeData) -> list[str]:
        raw = bytes(data.data(BINDER_MIME)).decode("utf-8", errors="ignore")
        return [u for u in raw.split("\n") if u]

    @staticmethod
    def _is_descendant(ancestor: BinderItem, candidate: BinderItem) -> bool:
        """True if ``candidate`` lives anywhere beneath ``ancestor``."""
        node = candidate.parent
        while node is not None:
            if node is ancestor:
                return True
            node = node.parent
        return False

    def removeRows(self, row: int, count: int, parent: QModelIndex = QModelIndex()) -> bool:
        # No-op. Qt's view calls this after InternalMove drops, but our
        # ``dropMimeData`` already relocated the item via ``_move_item``.
        # Explicit deletions go through ``remove_item`` directly.
        return True

    def _move_item(self, item: BinderItem, new_parent: BinderItem, new_row: int) -> bool:
        old_parent = item.parent
        if old_parent is None:
            return False  # moving root containers is disallowed
        old_row = old_parent.children.index(item)
        src_index = self.index_for_item(old_parent)
        dst_index = self.index_for_item(new_parent)

        # Clamp destination row; adjust when moving within the same parent.
        new_row = max(0, min(new_row, len(new_parent.children)))
        if old_parent is new_parent:
            if new_row == old_row or new_row == old_row + 1:
                return False  # no-op
            # Qt requires dst to not equal src for beginMoveRows; use remove+insert.
            target_row = new_row if new_row < old_row else new_row - 1
        else:
            target_row = new_row

        self.beginRemoveRows(src_index, old_row, old_row)
        old_parent.remove_child(item)
        self.endRemoveRows()

        self.beginInsertRows(dst_index, target_row, target_row)
        new_parent.add_child(item, index=target_row)
        self.endInsertRows()
        return True

    # --- structural edits ---

    def add_item(self, parent_index: QModelIndex, item_type: ItemType, title: str = "") -> QModelIndex:
        if parent_index.isValid():
            parent_item: BinderItem = parent_index.internalPointer()
        else:
            # default: first draft folder
            parent_item = self._project.root_draft() or (self._project.roots[0] if self._project.roots else None)
            if parent_item is None:
                return QModelIndex()
            parent_index = self.index_for_item(parent_item)
        if not parent_item.type.is_container:
            # promote: add as sibling under the item's parent
            actual_parent = parent_item.parent
            if actual_parent is None:
                return QModelIndex()
            parent_item = actual_parent
            parent_index = self.index_for_item(actual_parent)

        row = len(parent_item.children)
        new_item = BinderItem(type=item_type, title=title or _default_title(item_type))
        self.beginInsertRows(parent_index, row, row)
        parent_item.add_child(new_item)
        self.endInsertRows()
        self._project.touch()
        return self.createIndex(row, 0, new_item)

    def notify_item_changed(self, item: BinderItem) -> None:
        """Emit dataChanged for ``item`` so attached views (corkboard) repaint.

        Used when a field that's not edited through the model — currently the
        synopsis, owned by the inspector — has been mutated on the BinderItem
        directly.
        """
        idx = self.index_for_item(item)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [SYNOPSIS_ROLE, Qt.DisplayRole])

    def remove_item(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        item: BinderItem = index.internalPointer()
        if item.type.is_root_container:
            return False
        parent_item = item.parent
        parent_index = self.parent(index)
        if parent_item is None:
            return False
        row = parent_item.children.index(item)
        self.beginRemoveRows(parent_index, row, row)
        parent_item.remove_child(item)
        self.endRemoveRows()
        self._project.touch()
        return True


def _default_title(item_type: ItemType) -> str:
    return {
        ItemType.FOLDER: "New Folder",
        ItemType.TEXT: "Untitled",
    }.get(item_type, "New Item")
