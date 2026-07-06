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
    empty_trash_requested = Signal()
    print_requested = Signal()
    move_to_requested = Signal(QModelIndex, BinderItem)
    copy_to_requested = Signal(QModelIndex, BinderItem)
    open_in_editor_requested = Signal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(False)
        self.setEditTriggers(
            QTreeView.DoubleClicked | QTreeView.EditKeyPressed | QTreeView.SelectedClicked
        )
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

        item = model.item_from_index(index)
        # Items already in the Trash get a "Delete" option (permanent).
        # Everything else gets "Move to Trash".
        in_trash = False
        if item is not None:
            node = item.parent
            while node is not None:
                if node.type is ItemType.TRASH_FOLDER:
                    in_trash = True
                    break
                node = node.parent

        if in_trash:
            act_delete = QAction("Delete", self)
            act_delete.setEnabled(item is not None and not item.type.is_root_container)
            act_delete.triggered.connect(lambda: self._confirm_delete(index))
            menu.addAction(act_delete)
        else:
            act_trash = QAction("Move to Trash", self)
            act_trash.setEnabled(item is not None and not item.type.is_root_container)
            act_trash.triggered.connect(lambda: self.delete_requested.emit(index))
            menu.addAction(act_trash)

        menu.addSeparator()

        # "Move To" / "Copy To" submenus. Each root container becomes a
        # clickable destination and a submenu of its container descendants.
        # The source item and any of its descendants are skipped so a move
        # can't create a cycle. Trash stays in the list even for non-trash
        # items — "Move to Trash" up above is just a shortcut for the
        # common case; users can also reach it through this submenu.
        if item is not None and not item.type.is_root_container:
            move_menu = menu.addMenu("Move To")
            self.populate_destination_menu(
                move_menu,
                item,
                lambda dest, idx=index: self.move_to_requested.emit(idx, dest),
            )
            copy_menu = menu.addMenu("Copy To")
            self.populate_destination_menu(
                copy_menu,
                item,
                lambda dest, idx=index: self.copy_to_requested.emit(idx, dest),
            )
            menu.addSeparator()

        # Empty Trash — only shown when right-clicking the Trash folder.
        if item is not None and item.type is ItemType.TRASH_FOLDER and item.children:
            act_empty_trash = QAction("Empty Trash", self)
            act_empty_trash.triggered.connect(self.empty_trash_requested.emit)
            menu.addAction(act_empty_trash)
            menu.addSeparator()

        # "Open" submenu — the only entry today is "In Editor", which lets
        # the user pull a trashed text item back onto the editing surface
        # without restoring it. Force-enabled whenever there's a valid
        # selection; the handler in MainWindow decides whether the item
        # actually has a body to display.
        if index.isValid() and item is not None and not item.type.is_root_container:
            open_menu = menu.addMenu("Open")
            act_open_editor = open_menu.addAction("In Editor")
            act_open_editor.triggered.connect(
                lambda: self.open_in_editor_requested.emit(index)
            )

        act_print = QAction("Print", self)
        act_print.setEnabled(item is not None)
        act_print.triggered.connect(self.print_requested)
        menu.addAction(act_print)

        menu.exec(self.viewport().mapToGlobal(pos))

    def populate_destination_menu(
        self,
        parent_menu: QMenu,
        source_item: BinderItem,
        pick_callback,
    ) -> None:
        """Populate the top-level "Move To" / "Copy To" submenu.

        Every root container (Manuscript, Research, Trash) becomes a
        nested submenu of valid destinations rooted at that container.
        When the user picks a leaf, ``pick_callback`` is invoked with
        the chosen ``BinderItem`` as its single argument — making this
        helper usable from both this view's own context menu (which
        re-emits as a Qt signal) and from sibling views like the
        corkboard and outliner that resolve destinations inline.
        """
        model = self.binder_model()
        if model is None:
            return
        any_destination = False
        for root in model.root_containers():
            sub = parent_menu.addMenu(root.title or "(untitled)")
            if self._fill_destination_menu(
                sub, root, source_item, pick_callback,
            ):
                any_destination = True
            else:
                parent_menu.removeAction(sub.menuAction())
        if not any_destination:
            placeholder = parent_menu.addAction("(no destinations)")
            placeholder.setEnabled(False)

    def _fill_destination_menu(
        self,
        menu: QMenu,
        container: BinderItem,
        source_item: BinderItem,
        pick_callback,
    ) -> bool:
        """Recursively fill ``menu`` with destinations rooted at ``container``.

        The container itself is exposed as a clickable action (unless it
        would create a cycle by being the source or an ancestor of it).
        Its container descendants become nested submenus, recursively.
        Returns True when at least one destination was added so callers
        can prune empty submenus.
        """
        model = self.binder_model()
        if model is None:
            return False
        any_added = False
        # The container itself is a valid destination if it isn't the
        # source or any ancestor of the source. ``_is_descendant(src,
        # candidate)`` answers "is ``candidate`` a descendant of ``src``",
        # so we want the inverse plus the self-check.
        is_self = container is source_item
        is_descendant_of_source = model._is_descendant(source_item, container)
        if not is_self and not is_descendant_of_source:
            label = container.title or "(untitled)"
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, d=container: pick_callback(d)
            )
            any_added = True
        # Descend into container children that are themselves containers.
        for child in container.children:
            if not child.type.is_container or child is source_item:
                continue
            sub = menu.addMenu(child.title or "(untitled)")
            if self._fill_destination_menu(
                sub, child, source_item, pick_callback,
            ):
                any_added = True
            else:
                menu.removeAction(sub.menuAction())
        return any_added


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
