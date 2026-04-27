"""Binder tree view — QTreeView wrapper with a context menu."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractItemView, QMenu, QMessageBox, QTreeView

from skribe.model.binder_model import BinderModel
from skribe.model.project import BinderItem, ItemType


class BinderView(QTreeView):
    add_requested = Signal(QModelIndex, ItemType)
    delete_requested = Signal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(False)
        self.setEditTriggers(QTreeView.EditKeyPressed | QTreeView.SelectedClicked)
        self.setSelectionBehavior(QTreeView.SelectRows)
        # Extended selection: Shift-click for ranges, Ctrl/Cmd-click to
        # toggle individual rows. The editor still tracks one "current"
        # item via currentChanged; multi-select is an additive aggregate
        # consumed by features like Statistics.
        self.setSelectionMode(QTreeView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        # Drag-and-drop: internal reorder/reparent moves only.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def binder_model(self) -> Optional[BinderModel]:
        m = self.model()
        return m if isinstance(m, BinderModel) else None

    def current_item(self) -> Optional[BinderItem]:
        model = self.binder_model()
        if model is None:
            return None
        return model.item_from_index(self.currentIndex())

    def _show_context_menu(self, pos: QPoint) -> None:
        model = self.binder_model()
        if model is None:
            return
        index = self.indexAt(pos)
        menu = QMenu(self)

        act_add_text = QAction("Add Text", self)
        act_add_text.triggered.connect(lambda: self.add_requested.emit(index, ItemType.TEXT))
        menu.addAction(act_add_text)

        act_add_folder = QAction("Add Folder", self)
        act_add_folder.triggered.connect(lambda: self.add_requested.emit(index, ItemType.FOLDER))
        menu.addAction(act_add_folder)

        menu.addSeparator()

        act_rename = QAction("Rename", self)
        act_rename.setEnabled(index.isValid())
        act_rename.triggered.connect(lambda: self.edit(index))
        menu.addAction(act_rename)

        act_delete = QAction("Delete", self)
        item = model.item_from_index(index)
        act_delete.setEnabled(item is not None and not item.type.is_root_container)
        act_delete.triggered.connect(lambda: self._confirm_delete(index))
        menu.addAction(act_delete)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _confirm_delete(self, index: QModelIndex) -> None:
        model = self.binder_model()
        if model is None or not index.isValid():
            return
        item = model.item_from_index(index)
        if item is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete item",
            f'Delete "{item.title or "(untitled)"}" and all its children?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(index)
