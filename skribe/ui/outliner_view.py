"""Outliner view: multi-column QTreeView over the binder tree.

Inspired by Scrivener's Outliner. Shows one row per binder item with columns
for title, synopsis, label, status, word-count metrics, and more.

The heavy lifting is done by ``OutlinerProxyModel``, a QIdentityProxyModel
that maps the single-column BinderModel to N columns.
"""
from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import (
    QIdentityProxyModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QMenu,
    QStyle,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTreeView,
    QWidget,
)

from skribe.model.binder_model import (
    BINDER_MIME,
    UUID_ROLE,
    TYPE_ROLE,
    SYNOPSIS_ROLE,
    WORD_COUNT_ROLE,
    TARGET_ROLE,
    LABEL_ID_ROLE,
    STATUS_ID_ROLE,
    INCLUDE_ROLE,
    CREATED_ROLE,
    MODIFIED_ROLE,
    BinderModel,
)
from skribe.model.project import (
    BinderItem, ItemType, LabelDef, Project, StatusDef,
    CustomFieldDef, CustomFieldType,
)


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

class OutlinerColumn(IntEnum):
    TITLE = 0
    SYNOPSIS = 1
    LABEL = 2
    STATUS = 3
    WORD_COUNT = 4
    CHAR_COUNT = 5
    TOTAL_WORD_COUNT = 6
    TARGET = 7
    PROGRESS = 8
    CREATED = 9
    MODIFIED = 10
    INCLUDE_IN_COMPILE = 11


COLUMN_HEADERS: dict[OutlinerColumn, str] = {
    OutlinerColumn.TITLE: "Title",
    OutlinerColumn.SYNOPSIS: "Synopsis",
    OutlinerColumn.LABEL: "Label",
    OutlinerColumn.STATUS: "Status",
    OutlinerColumn.WORD_COUNT: "Words",
    OutlinerColumn.CHAR_COUNT: "Chars",
    OutlinerColumn.TOTAL_WORD_COUNT: "Total Words",
    OutlinerColumn.TARGET: "Target",
    OutlinerColumn.PROGRESS: "Progress",
    OutlinerColumn.CREATED: "Created",
    OutlinerColumn.MODIFIED: "Modified",
    OutlinerColumn.INCLUDE_IN_COMPILE: "Include",
}

DEFAULT_COLUMNS: list[OutlinerColumn] = [
    OutlinerColumn.TITLE,
    OutlinerColumn.SYNOPSIS,
    OutlinerColumn.LABEL,
    OutlinerColumn.STATUS,
    OutlinerColumn.WORD_COUNT,
    OutlinerColumn.PROGRESS,
    OutlinerColumn.INCLUDE_IN_COMPILE,
]

_NUM_COLUMNS = len(OutlinerColumn)


# ---------------------------------------------------------------------------
# Proxy model
# ---------------------------------------------------------------------------

class OutlinerProxyModel(QIdentityProxyModel):
    """Wraps BinderModel (1 column) and exposes *N* outliner columns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._word_counts: dict[str, tuple[int, int]] = {}  # uuid → (words, chars)

    # -- project / word-count bookkeeping --------------------------------
    def set_project(self, project: Project) -> None:
        self.layoutAboutToBeChanged.emit()
        self._project = project
        self.layoutChanged.emit()

    @property
    def _custom_field_defs(self) -> list[CustomFieldDef]:
        if self._project is None:
            return []
        try:
            return self._project.custom_field_defs
        except AttributeError:
            return []

    @property
    def _total_column_count(self) -> int:
        return _NUM_COLUMNS + len(self._custom_field_defs)

    def _custom_field_for_column(self, col_index: int) -> Optional[CustomFieldDef]:
        offset = col_index - _NUM_COLUMNS
        defs = self._custom_field_defs
        if 0 <= offset < len(defs):
            return defs[offset]
        return None

    def update_word_counts(self, bundle_path: Path) -> None:
        """Walk every TEXT item, read its HTML, and cache word/char counts."""
        from skribe.stats import _html_to_plain, _count_words
        from skribe.ioformat.skribe_io import read_document_body

        if self._project is None:
            return
        counts: dict[str, tuple[int, int]] = {}
        for root in self._project.roots:
            for item in root.walk():
                if item.type is not ItemType.TEXT:
                    continue
                try:
                    html = read_document_body(bundle_path, item.uuid)
                except Exception:
                    html = ""
                plain = _html_to_plain(html)
                wc = _count_words(plain)
                cc = len(plain) if plain else 0
                counts[item.uuid] = (wc, cc)
        self._word_counts = counts
        # Notify views that data in all word-count columns changed.
        if self.rowCount():
            top = self.index(0, int(OutlinerColumn.WORD_COUNT))
            bot = self.index(self.rowCount() - 1, self._total_column_count - 1)
            self.dataChanged.emit(top, bot, [Qt.DisplayRole])

    def _total_word_count(self, item: BinderItem) -> int:
        total = self._word_counts.get(item.uuid, (0, 0))[0]
        for child in item.children:
            total += self._total_word_count(child)
        return total

    # -- column / index mapping ------------------------------------------

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return self._total_column_count

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        # Map parent to source (always column 0), ask source for the row,
        # then create a proxy index at the requested column.
        src_parent = self.mapToSource(parent)
        src = self.sourceModel()
        if src is None:
            return QModelIndex()
        src_idx = src.index(row, 0, src_parent)
        if not src_idx.isValid():
            return QModelIndex()
        return self.createIndex(row, column, src_idx.internalPointer())

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        src = self.sourceModel()
        if src is None:
            return QModelIndex()
        # Build a temporary source index for this item at column 0.
        src_idx = src.createIndex(index.row(), 0, index.internalPointer())
        src_parent = src.parent(src_idx)
        if not src_parent.isValid():
            return QModelIndex()
        return self.createIndex(src_parent.row(), 0, src_parent.internalPointer())

    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:
        if not proxy_index.isValid():
            return QModelIndex()
        src = self.sourceModel()
        if src is None:
            return QModelIndex()
        return src.createIndex(proxy_index.row(), 0, proxy_index.internalPointer())

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:
        if not source_index.isValid():
            return QModelIndex()
        return self.createIndex(source_index.row(), 0, source_index.internalPointer())

    # -- data access -----------------------------------------------------

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        col_idx = index.column()
        item: BinderItem = index.internalPointer()

        # Custom column?
        if col_idx >= _NUM_COLUMNS:
            return self._custom_column_data(item, col_idx, role)

        col = OutlinerColumn(col_idx)

        # Column 0 (TITLE) — delegate entirely to the source model.
        if col is OutlinerColumn.TITLE:
            return self.sourceModel().data(self.mapToSource(index), role)

        # All other columns handle DisplayRole / EditRole only (plus
        # CheckStateRole for INCLUDE_IN_COMPILE).
        if col is OutlinerColumn.SYNOPSIS:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return item.synopsis or ""
            return None

        if col is OutlinerColumn.LABEL:
            if role in (Qt.DisplayRole, Qt.EditRole):
                lid = item.metadata.get("label_id", "0")
                if self._project is not None:
                    ldef = self._project.label_for_id(lid)
                    if ldef is not None:
                        if role == Qt.DisplayRole:
                            return ldef.name
                        return ldef.id
                return lid
            if role == Qt.BackgroundRole:
                lid = item.metadata.get("label_id", "0")
                if self._project is not None:
                    ldef = self._project.label_for_id(lid)
                    if ldef is not None:
                        c = QColor(ldef.color)
                        c.setAlpha(80)
                        return c
                return None
            return None

        if col is OutlinerColumn.STATUS:
            if role in (Qt.DisplayRole, Qt.EditRole):
                sid = item.metadata.get("status_id", "0")
                if self._project is not None:
                    sdef = self._project.status_for_id(sid)
                    if sdef is not None:
                        if role == Qt.DisplayRole:
                            return sdef.name
                        return sdef.id
                return sid
            return None

        if col is OutlinerColumn.WORD_COUNT:
            if role == Qt.DisplayRole:
                wc = self._word_counts.get(item.uuid, (0, 0))[0]
                return str(wc)
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return None

        if col is OutlinerColumn.CHAR_COUNT:
            if role == Qt.DisplayRole:
                cc = self._word_counts.get(item.uuid, (0, 0))[1]
                return str(cc)
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return None

        if col is OutlinerColumn.TOTAL_WORD_COUNT:
            if role == Qt.DisplayRole:
                return str(self._total_word_count(item))
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return None

        if col is OutlinerColumn.TARGET:
            if role in (Qt.DisplayRole, Qt.EditRole):
                val = item.metadata.get("target_word_count", 0)
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return 0
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return None

        if col is OutlinerColumn.PROGRESS:
            if role == Qt.DisplayRole:
                wc = self._word_counts.get(item.uuid, (0, 0))[0]
                target = item.metadata.get("target_word_count", 0)
                try:
                    target = int(target)
                except (TypeError, ValueError):
                    target = 0
                if target <= 0:
                    return 0.0
                return min(wc / target, 2.0)  # cap at 200 %
            return None

        if col is OutlinerColumn.CREATED:
            if role == Qt.DisplayRole:
                return _format_datetime(item.created)
            if role == Qt.EditRole:
                return item.created
            return None

        if col is OutlinerColumn.MODIFIED:
            if role == Qt.DisplayRole:
                return _format_datetime(item.modified)
            if role == Qt.EditRole:
                return item.modified
            return None

        if col is OutlinerColumn.INCLUDE_IN_COMPILE:
            if role == Qt.CheckStateRole:
                val = item.metadata.get("include_in_compile", True)
                return Qt.Checked if val else Qt.Unchecked
            return None

        return None

    def _custom_column_data(self, item: BinderItem, col_idx: int, role: int) -> Any:
        fd = self._custom_field_for_column(col_idx)
        if fd is None:
            return None
        custom = item.metadata.get("custom", {})
        value = custom.get(fd.id, fd.default)

        if fd.field_type is CustomFieldType.CHECKBOX:
            if role == Qt.CheckStateRole:
                return Qt.Checked if value in ("true", "True", "1", True) else Qt.Unchecked
            return None

        if fd.field_type is CustomFieldType.DATE:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return value or ""
            return None

        if fd.field_type is CustomFieldType.LIST:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return value or ""
            return None

        # TEXT
        if role in (Qt.DisplayRole, Qt.EditRole):
            return value or ""
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        col_idx = index.column()
        item: BinderItem = index.internalPointer()

        if col_idx >= _NUM_COLUMNS:
            return self._set_custom_column_data(item, index, col_idx, value, role)

        col = OutlinerColumn(col_idx)

        if col is OutlinerColumn.TITLE and role == Qt.EditRole:
            return self.sourceModel().setData(self.mapToSource(index), value, role)

        if col is OutlinerColumn.SYNOPSIS and role == Qt.EditRole:
            item.synopsis = str(value)
            item.touch()
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col is OutlinerColumn.LABEL and role == Qt.EditRole:
            item.metadata["label_id"] = str(value)
            item.touch()
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole, Qt.BackgroundRole])
            return True

        if col is OutlinerColumn.STATUS and role == Qt.EditRole:
            item.metadata["status_id"] = str(value)
            item.touch()
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col is OutlinerColumn.TARGET and role == Qt.EditRole:
            try:
                val = int(value)
            except (TypeError, ValueError):
                return False
            item.metadata["target_word_count"] = val
            item.touch()
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            # Progress column depends on target.
            prog_idx = self.index(index.row(), int(OutlinerColumn.PROGRESS), self.parent(index))
            self.dataChanged.emit(prog_idx, prog_idx, [Qt.DisplayRole])
            return True

        if col is OutlinerColumn.CREATED and role == Qt.EditRole:
            item.created = str(value)
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col is OutlinerColumn.MODIFIED and role == Qt.EditRole:
            item.modified = str(value)
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col is OutlinerColumn.INCLUDE_IN_COMPILE and role == Qt.CheckStateRole:
            if isinstance(value, int):
                checked = value == Qt.Checked.value
            else:
                checked = value == Qt.Checked
            item.metadata["include_in_compile"] = checked
            item.touch()
            if self._project is not None:
                self._project.touch()
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True

        return False

    def _set_custom_column_data(self, item: BinderItem, index: QModelIndex, col_idx: int, value: Any, role: int) -> bool:
        fd = self._custom_field_for_column(col_idx)
        if fd is None:
            return False

        custom = item.metadata.setdefault("custom", {})

        if fd.field_type is CustomFieldType.CHECKBOX:
            if role != Qt.CheckStateRole:
                return False
            if isinstance(value, int):
                custom[fd.id] = "true" if value == Qt.Checked.value else "false"
            else:
                custom[fd.id] = "true" if value == Qt.Checked else "false"
        else:
            if role != Qt.EditRole:
                return False
            custom[fd.id] = str(value) if value else ""

        item.touch()
        if self._project is not None:
            self._project.touch()
        self.dataChanged.emit(index, index, [role])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        col_idx = index.column()
        item: BinderItem = index.internalPointer()
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        if col_idx >= _NUM_COLUMNS:
            fd = self._custom_field_for_column(col_idx)
            if fd is not None and not item.type.is_root_container:
                if fd.field_type is CustomFieldType.CHECKBOX:
                    base |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
                else:
                    base |= Qt.ItemIsEditable
            return base

        col = OutlinerColumn(col_idx)
        if col is OutlinerColumn.TITLE:
            if not item.type.is_root_container:
                base |= Qt.ItemIsEditable | Qt.ItemIsDragEnabled
            if item.type.is_container:
                base |= Qt.ItemIsDropEnabled
            return base

        if col is OutlinerColumn.INCLUDE_IN_COMPILE:
            if not item.type.is_root_container:
                base |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
            return base

        if col in (
            OutlinerColumn.SYNOPSIS,
            OutlinerColumn.LABEL,
            OutlinerColumn.STATUS,
            OutlinerColumn.TARGET,
            OutlinerColumn.CREATED,
            OutlinerColumn.MODIFIED,
        ):
            if not item.type.is_root_container:
                base |= Qt.ItemIsEditable
            return base

        # Read-only columns.
        return base

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section < _NUM_COLUMNS:
                try:
                    return COLUMN_HEADERS[OutlinerColumn(section)]
                except (ValueError, KeyError):
                    pass
            else:
                fd = self._custom_field_for_column(section)
                if fd is not None:
                    return fd.name
        return None

    def sibling(self, row: int, column: int, idx: QModelIndex) -> QModelIndex:
        # QIdentityProxyModel.sibling() routes through mapToSource→source→mapFromSource,
        # which fails for columns beyond the single-column source model.
        # QTreeView.indexAt() uses siblingAtColumn() → sibling(), so without this override
        # indexAt() always returns an invalid index for custom columns.
        return self.index(row, column, self.parent(idx))

    def buddy(self, index: QModelIndex) -> QModelIndex:
        # Prevent QIdentityProxyModel.buddy() from collapsing all columns to 0 via mapToSource.
        return index

    # -- drag & drop delegation ------------------------------------------
    # These must delegate to the source BinderModel so row DnD works.

    def supportedDropActions(self) -> Qt.DropActions:
        src = self.sourceModel()
        return src.supportedDropActions() if src is not None else Qt.MoveAction

    def supportedDragActions(self) -> Qt.DropActions:
        src = self.sourceModel()
        return src.supportedDragActions() if src is not None else Qt.MoveAction

    def mimeTypes(self) -> list[str]:
        src = self.sourceModel()
        return src.mimeTypes() if src is not None else [BINDER_MIME]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        # Map proxy indices to source (column 0) before delegating.
        src = self.sourceModel()
        if src is None:
            return QMimeData()
        source_indexes = []
        seen = set()
        for idx in indexes:
            if not idx.isValid():
                continue
            src_idx = self.mapToSource(idx)
            key = (src_idx.row(), id(src_idx.internalPointer()))
            if key not in seen:
                seen.add(key)
                source_indexes.append(src_idx)
        return src.mimeData(source_indexes)

    def canDropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        src = self.sourceModel()
        if src is None:
            return False
        return src.canDropMimeData(data, action, row, 0, self.mapToSource(parent))

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        src = self.sourceModel()
        if src is None:
            return False
        return src.dropMimeData(data, action, row, 0, self.mapToSource(parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_datetime(iso: str) -> str:
    """Best-effort short datetime from an ISO-8601 string."""
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------

class ProgressBarDelegate(QStyledItemDelegate):
    """Paints a coloured progress bar. Data is a float 0.0–2.0."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        value = index.data(Qt.DisplayRole)
        if value is None:
            return
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            ratio = 0.0

        painter.save()
        rect = option.rect.adjusted(4, 3, -4, -3)

        # Background track.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(220, 220, 220))
        painter.drawRoundedRect(rect, 3, 3)

        # Filled bar.
        if ratio > 0:
            fill_w = int(rect.width() * min(ratio, 1.0))
            fill_rect = rect.adjusted(0, 0, -(rect.width() - fill_w), 0)
            if ratio >= 1.0:
                color = QColor(76, 217, 100)  # green
            elif ratio >= 0.5:
                # yellow → green
                t = (ratio - 0.5) / 0.5
                color = QColor(
                    int(255 * (1 - t) + 76 * t),
                    int(204 * (1 - t) + 217 * t),
                    int(0 * (1 - t) + 100 * t),
                )
            else:
                # red → yellow
                t = ratio / 0.5
                color = QColor(
                    int(252 * (1 - t) + 255 * t),
                    int(61 * (1 - t) + 204 * t),
                    0,
                )
            painter.setBrush(color)
            painter.drawRoundedRect(fill_rect, 3, 3)

        # Percentage text.
        pct_text = f"{int(ratio * 100)}%"
        painter.setPen(QPen(QColor(50, 50, 50)))
        painter.drawText(rect, Qt.AlignCenter, pct_text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        return option.rect.size() if option.rect.isValid() else super().sizeHint(option, index)


class CheckBoxDelegate(QStyledItemDelegate):
    """Paints a centred checkbox; toggles via editorEvent (no editor widget)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        # Draw the base (selection highlight, etc.).
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QStyle.style()

        # Checkbox indicator.
        check_state = index.data(Qt.CheckStateRole)
        opt = QStyleOptionViewItem(option)
        opt.features |= QStyleOptionViewItem.HasCheckIndicator
        opt.checkState = Qt.CheckState(check_state) if check_state is not None else Qt.Unchecked
        # Centre the checkbox.
        opt.displayAlignment = Qt.AlignCenter
        opt.text = ""
        opt.icon = QIcon()
        widget = option.widget
        style = widget.style() if widget else None
        if style is not None:
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        else:
            super().paint(painter, option, index)

    def editorEvent(self, event, model, option, index) -> bool:
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            current = index.data(Qt.CheckStateRole)
            new_val = Qt.Unchecked if current == Qt.Checked else Qt.Checked
            return model.setData(index, new_val.value, Qt.CheckStateRole)
        return False

    def createEditor(self, parent, option, index):
        # No editor widget — all interaction via editorEvent.
        return None


class ComboDelegate(QStyledItemDelegate):
    """Combo-box delegate for Label / Status columns.

    Constructed with a list of ``(id, name)`` pairs that populate the dropdown.
    For the Label column, pass *label_colors* mapping id → hex color so the
    cell background can be tinted.
    """

    def __init__(
        self,
        items: list[tuple[str, str]],
        label_colors: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = items  # [(id, display_name), ...]
        self._label_colors = label_colors or {}

    def set_items(self, items: list[tuple[str, str]], label_colors: dict[str, str] | None = None) -> None:
        self._items = items
        if label_colors is not None:
            self._label_colors = label_colors

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        # Tint the background for label columns.
        bg = index.data(Qt.BackgroundRole)
        if bg is not None and isinstance(bg, QColor):
            painter.save()
            painter.fillRect(option.rect, bg)
            painter.restore()
        super().paint(painter, option, index)

    def createEditor(self, parent: QWidget, option, index) -> QComboBox:
        combo = QComboBox(parent)
        for item_id, name in self._items:
            combo.addItem(name, userData=item_id)
        return combo

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:
        current_id = index.data(Qt.EditRole)
        for i in range(editor.count()):
            if editor.itemData(i) == str(current_id):
                editor.setCurrentIndex(i)
                return

    def setModelData(self, editor: QComboBox, model, index: QModelIndex) -> None:
        item_id = editor.currentData()
        if item_id is not None:
            model.setData(index, item_id, Qt.EditRole)




class TextDelegate(QStyledItemDelegate):
    """Inline text editor for custom meta TEXT fields with word wrap."""

    _PAD = 4  # px horizontal padding each side

    # -- display ---------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        # Draw selection / hover / alternate-row background via the style.
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style is None:
            from PySide6.QtWidgets import QApplication
            style = QApplication.style()
        # Background only — we draw the text ourselves for word-wrap.
        option.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)

        text = index.data(Qt.DisplayRole) or ""
        if not text:
            return

        rect = option.rect.adjusted(self._PAD, 2, -self._PAD, -2)
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.setPen(option.palette.highlightedText().color())
        else:
            painter.setPen(option.palette.text().color())
        painter.setFont(option.font)
        painter.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        from PySide6.QtCore import QSize, QRect
        from PySide6.QtGui import QFontMetrics

        text = index.data(Qt.DisplayRole) or ""
        width = option.rect.width() if option.rect.isValid() else 150
        fm = QFontMetrics(option.font)
        if not text:
            return QSize(width, fm.height() + 4)
        text_width = max(width - 2 * self._PAD, 30)
        bounding = fm.boundingRect(
            QRect(0, 0, text_width, 100000),
            Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
            text,
        )
        return QSize(width, max(bounding.height() + 6, fm.height() + 4))

    # -- editor ----------------------------------------------------------

    def createEditor(self, parent: QWidget, option, index) -> QWidget:
        from PySide6.QtWidgets import QPlainTextEdit
        from PySide6.QtGui import QTextOption

        editor = QPlainTextEdit(parent)
        editor.setFrameShape(QPlainTextEdit.NoFrame)
        editor.setWordWrapMode(QTextOption.WordWrap)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setTabChangesFocus(True)
        # Close the editor on Enter/Return instead of inserting a newline.
        editor.installEventFilter(self)
        # Adjust height as the user types.
        editor.document().contentsChanged.connect(
            lambda: self._resize_editor(editor)
        )
        return editor

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commitData.emit(obj)
            self.closeEditor.emit(obj, QStyledItemDelegate.NoHint)
            return True
        return super().eventFilter(obj, event)

    def _resize_editor(self, editor) -> None:
        doc = editor.document()
        doc.setTextWidth(editor.viewport().width())
        margins = editor.contentsMargins()
        needed = int(doc.size().height()) + margins.top() + margins.bottom() + 4
        geo = editor.geometry()
        # Grow (or shrink) downward from the cell's top edge; never
        # smaller than the original cell height stored by updateEditorGeometry.
        min_h = getattr(editor, '_cell_height', geo.height())
        target = max(needed, min_h)
        if target != geo.height():
            geo.setHeight(target)
            editor.setGeometry(geo)

    def setEditorData(self, editor, index: QModelIndex) -> None:
        value = index.data(Qt.EditRole) or ""
        editor.setPlainText(str(value))

    def setModelData(self, editor, model, index: QModelIndex) -> None:
        model.setData(index, editor.toPlainText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor._cell_height = option.rect.height()
        editor.setGeometry(option.rect)
        self._resize_editor(editor)

class DateDelegate(QStyledItemDelegate):
    """Inline date editor using a QDateEdit widget."""

    def createEditor(self, parent: QWidget, option, index) -> QWidget:
        from PySide6.QtWidgets import QDateEdit
        from PySide6.QtCore import QDate
        editor = QDateEdit(parent)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd")
        return editor

    def setEditorData(self, editor, index: QModelIndex) -> None:
        from PySide6.QtCore import QDate
        value = index.data(Qt.EditRole) or ""
        try:
            date = QDate.fromString(value, "yyyy-MM-dd")
            if date.isValid():
                editor.setDate(date)
            else:
                editor.setDate(QDate.currentDate())
        except Exception:
            editor.setDate(QDate.currentDate())

    def setModelData(self, editor, model, index: QModelIndex) -> None:
        model.setData(index, editor.date().toString("yyyy-MM-dd"), Qt.EditRole)


class DateTimeDelegate(QStyledItemDelegate):
    """Inline datetime editor using a QDateTimeEdit widget."""

    def createEditor(self, parent: QWidget, option, index) -> QWidget:
        from PySide6.QtWidgets import QDateTimeEdit
        from PySide6.QtCore import QDateTime
        editor = QDateTimeEdit(parent)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd HH:mm")
        return editor

    def setEditorData(self, editor, index: QModelIndex) -> None:
        from PySide6.QtCore import QDateTime
        value = index.data(Qt.EditRole) or ""
        try:
            dt = QDateTime.fromString(value, Qt.ISODate)
            if dt.isValid():
                editor.setDateTime(dt)
            else:
                editor.setDateTime(QDateTime.currentDateTimeUtc())
        except Exception:
            editor.setDateTime(QDateTime.currentDateTimeUtc())

    def setModelData(self, editor, model, index: QModelIndex) -> None:
        model.setData(index, editor.dateTime().toUTC().toString(Qt.ISODate), Qt.EditRole)


_RESIZE_MARGIN = 4  # px from row border for drag-resize grab


class RowHeightDelegate(QStyledItemDelegate):
    """Default item delegate that enforces per-row minimum heights set by drag-resize."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        sh = super().sizeHint(option, index)
        view = self.parent()
        if view is not None:
            item = index.internalPointer()
            if item is not None and hasattr(item, 'uuid'):
                min_h = view._row_heights.get(item.uuid)
                if min_h is not None and min_h > sh.height():
                    sh.setHeight(min_h)
        return sh

# ---------------------------------------------------------------------------
# Outliner view
# ---------------------------------------------------------------------------

class OutlinerView(QTreeView):
    """Multi-column tree view showing the binder as an outliner."""

    item_activated = Signal(QModelIndex)
    context_menu_requested = Signal(QModelIndex, object)  # (proxy_index, QPoint)
    custom_fields_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._proxy: OutlinerProxyModel | None = None
        self._project: Project | None = None
        self._label_delegate: ComboDelegate | None = None
        self._status_delegate: ComboDelegate | None = None
        self._row_heights: dict[str, int] = {}   # uuid → minimum row height
        self._resize_row = None  # (QPersistentModelIndex, start_y, start_height)

        # -- selection & interaction --
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(False)
        self.setSortingEnabled(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setMouseTracking(True)

        # -- drag & drop --
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

        # -- edit triggers --
        self.setEditTriggers(
            QTreeView.DoubleClicked | QTreeView.EditKeyPressed | QTreeView.SelectedClicked
        )

        # -- context menu --
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        # -- header --
        hdr = self.header()
        hdr.setSectionsMovable(True)
        hdr.setStretchLastSection(True)
        hdr.setDefaultSectionSize(100)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_context_menu)

    # -- model setup -----------------------------------------------------

    def set_outliner_model(self, proxy: OutlinerProxyModel) -> None:
        """Assign the proxy model and configure delegates/columns."""
        self._proxy = proxy
        self.setModel(proxy)
        self._setup_delegates()
        self._setup_columns()

    def _setup_columns(self) -> None:
        """Initial column visibility and sizing."""
        hdr = self.header()
        total = self._proxy._total_column_count if self._proxy else _NUM_COLUMNS
        for col in range(total):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        self.set_visible_columns(DEFAULT_COLUMNS)

    def _setup_delegates(self) -> None:
        """Install column-specific delegates."""
        # Progress bar.
        self.setItemDelegateForColumn(int(OutlinerColumn.PROGRESS), ProgressBarDelegate(self))
        # Checkbox.
        self.setItemDelegateForColumn(int(OutlinerColumn.INCLUDE_IN_COMPILE), CheckBoxDelegate(self))
        # Label / Status combos — populated later when project is set.
        self._label_delegate = ComboDelegate([], parent=self)
        self.setItemDelegateForColumn(int(OutlinerColumn.LABEL), self._label_delegate)
        self._status_delegate = ComboDelegate([], parent=self)
        self.setItemDelegateForColumn(int(OutlinerColumn.STATUS), self._status_delegate)
        # Created / Modified datetime editors.
        self.setItemDelegateForColumn(int(OutlinerColumn.CREATED), DateTimeDelegate(self))
        self.setItemDelegateForColumn(int(OutlinerColumn.MODIFIED), DateTimeDelegate(self))
        # Default delegate for all other columns — enforces row-height overrides.
        self._row_height_delegate = RowHeightDelegate(self)
        self.setItemDelegate(self._row_height_delegate)

    # -- project ---------------------------------------------------------

    def set_project(self, project: Project) -> None:
        self._project = project
        if self._proxy is not None:
            self._proxy.set_project(project)
        self._refresh_delegates()
        self._setup_custom_delegates()

    def _refresh_delegates(self) -> None:
        """Update combo delegate items from current project definitions."""
        if self._project is None:
            return
        # Label delegate.
        if self._label_delegate is not None:
            try:
                defs = self._project.label_defs
                items = [(d.id, d.name) for d in defs]
                colors = {d.id: d.color for d in defs}
                self._label_delegate.set_items(items, colors)
            except AttributeError:
                pass  # label_defs not yet on Project
        # Status delegate.
        if self._status_delegate is not None:
            try:
                defs = self._project.status_defs
                items = [(d.id, d.name) for d in defs]
                self._status_delegate.set_items(items)
            except AttributeError:
                pass  # status_defs not yet on Project
    
    def _setup_custom_delegates(self) -> None:
        """Install delegates for custom field columns."""
        if self._project is None:
            return
        try:
            defs = self._project.custom_field_defs
        except AttributeError:
            return
        for i, fd in enumerate(defs):
            col_idx = _NUM_COLUMNS + i  # Calculate column index for each custom field
            if fd.field_type is CustomFieldType.CHECKBOX:
                self.setItemDelegateForColumn(col_idx, CheckBoxDelegate(self))
            elif fd.field_type is CustomFieldType.LIST:
                items = [(c, c) for c in fd.choices]
                self.setItemDelegateForColumn(col_idx, ComboDelegate(items, parent=self))  
            elif fd.field_type is CustomFieldType.DATE:
                self.setItemDelegateForColumn(col_idx, DateDelegate(self))
            elif fd.field_type is CustomFieldType.TEXT:
                self.setItemDelegateForColumn(col_idx, TextDelegate(self))
            
    # -- column visibility -----------------------------------------------

    def set_visible_columns(self, columns: list) -> None:
        """Set which columns are visible. Accepts OutlinerColumn and/or int indices."""
        visible = set(int(c) for c in columns)
        total = self._proxy._total_column_count if self._proxy else _NUM_COLUMNS
        for col_idx in range(total):
            self.setColumnHidden(col_idx, col_idx not in visible)

    def visible_columns(self) -> list[int]:
        """Return indices of all visible columns (built-in and custom)."""
        total = self._proxy._total_column_count if self._proxy else _NUM_COLUMNS
        return [c for c in range(total) if not self.isColumnHidden(c)]

    def _show_header_context_menu(self, pos) -> None:
        menu = QMenu(self)
        for col in OutlinerColumn:
            action = QAction(COLUMN_HEADERS[col], menu)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(int(col)))
            if col is OutlinerColumn.TITLE:
                action.setEnabled(False)  # title always visible
            action.toggled.connect(lambda checked, c=int(col): self.setColumnHidden(c, not checked))
            menu.addAction(action)
        # Custom columns
        if self._project is not None:
            try:
                defs = self._project.custom_field_defs
            except AttributeError:
                defs = []
            if defs:
                menu.addSeparator()
                for i, fd in enumerate(defs):
                    col_idx = _NUM_COLUMNS + i
                    action = QAction(fd.name, menu)
                    action.setCheckable(True)
                    action.setChecked(not self.isColumnHidden(col_idx))
                    action.toggled.connect(lambda checked, c=col_idx: self.setColumnHidden(c, not checked))
                    menu.addAction(action)
        menu.addSeparator()
        act_edit_fields = QAction("Custom Metadata Fields…", menu)
        act_edit_fields.triggered.connect(self.custom_fields_requested.emit)
        menu.addAction(act_edit_fields)
        menu.exec(self.header().mapToGlobal(pos))

    # -- signals / activation -------------------------------------------

    def _on_context_menu(self, pos) -> None:
        idx = self.indexAt(pos)
        global_pos = self.viewport().mapToGlobal(pos)
        self.context_menu_requested.emit(idx, global_pos)

    # -- row resize (drag row-border) ------------------------------------

    def _row_border_index(self, viewport_pos) -> QModelIndex | None:
        """Return column-0 index of the row whose bottom border is near *viewport_pos*."""
        idx = self.indexAt(viewport_pos)
        if not idx.isValid():
            return None
        idx0 = idx.sibling(idx.row(), 0)
        rect = self.visualRect(idx0)
        y = viewport_pos.y()
        # Near the top edge → resize the row above this one.
        if y - rect.top() <= _RESIZE_MARGIN:
            above = self.indexAbove(idx0)
            if above.isValid():
                return above.sibling(above.row(), 0)
        # Near the bottom edge → resize this row.
        if rect.bottom() - y <= _RESIZE_MARGIN:
            return idx0
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            border = self._row_border_index(pos)
            if border is not None:
                rect = self.visualRect(border)
                self._resize_row = (
                    QPersistentModelIndex(border),
                    pos.y(),
                    rect.height(),
                )
                self.viewport().setCursor(Qt.SplitVCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._resize_row is not None:
            persist, start_y, start_h = self._resize_row
            idx = QModelIndex(persist)
            if idx.isValid():
                item = idx.internalPointer()
                if item is not None:
                    delta = pos.y() - start_y
                    new_h = max(start_h + delta, 20)
                    self._row_heights[item.uuid] = new_h
                    self.doItemsLayout()
            event.accept()
            return
        # Change cursor when hovering near a row border.
        border = self._row_border_index(pos)
        if border is not None:
            self.viewport().setCursor(Qt.SplitVCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_row is not None:
            self._resize_row = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- word counts -----------------------------------------------------

    def refresh_word_counts(self, bundle_path: Path) -> None:
        if self._proxy is not None:
            self._proxy.update_word_counts(bundle_path)

    # -- theming ---------------------------------------------------------

    def apply_theme(self, theme) -> None:
        """Minimal theme hook — alternating row colours follow the palette."""
        # The Qt palette from the theme already sets AlternateBase; just
        # ensure the property is active.
        self.setAlternatingRowColors(True)

    # -- state persistence -----------------------------------------------

    def save_column_state(self) -> dict:
        hdr = self.header()
        total = self._proxy._total_column_count if self._proxy else _NUM_COLUMNS
        return {
            "visible": self.visible_columns(),
            "order": [hdr.logicalIndex(i) for i in range(total)],
            "widths": {c: hdr.sectionSize(c) for c in range(total)},
            "row_heights": dict(self._row_heights),
        }

    def restore_column_state(self, state: dict) -> None:
        if not state:
            return
        total = self._proxy._total_column_count if self._proxy else _NUM_COLUMNS
        # Visibility.
        visible = state.get("visible")
        if visible is not None:
            cols = [v for v in visible if isinstance(v, int) and 0 <= v < total]
            if cols:
                self.set_visible_columns(cols)
        # Column order.
        order = state.get("order")
        hdr = self.header()
        if order is not None and len(order) == total:
            for visual, logical in enumerate(order):
                if logical < total:
                    current_visual = hdr.visualIndex(logical)
                    if current_visual != visual:
                        hdr.moveSection(current_visual, visual)
        # Widths.
        widths = state.get("widths")
        if widths is not None:
            for col_str, w in widths.items():
                try:
                    col = int(col_str)
                    if 0 <= col < total:
                        hdr.resizeSection(col, w)
                except (TypeError, ValueError):
                    pass
        # Row heights.
        rh = state.get("row_heights")
        if isinstance(rh, dict):
            self._row_heights = {k: v for k, v in rh.items() if isinstance(v, (int, float))}

    def closeEditor(self, editor, hint):
        """Suppress row navigation when the user presses Enter to finish editing."""
        if hint in (QAbstractItemDelegate.EditNextItem, QAbstractItemDelegate.EditPreviousItem):
            hint = QAbstractItemDelegate.NoHint
        super().closeEditor(editor, hint)

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position().toPoint()
        # Double-click on a row border → reset that row to its natural height.
        border = self._row_border_index(pos)
        if border is not None:
            item = border.internalPointer()
            if item is not None and hasattr(item, 'uuid'):
                if self._row_heights.pop(item.uuid, None) is not None:
                    self.doItemsLayout()
            event.accept()
            return
        idx = self.indexAt(pos)
        if idx.isValid() and idx.column() == int(OutlinerColumn.TITLE):
            self.item_activated.emit(idx)
        elif idx.isValid() and idx.flags() & Qt.ItemIsEditable:
            self.edit(idx, QAbstractItemView.EditTrigger.DoubleClicked, event)
        else:
            super().mouseDoubleClickEvent(event)