"""Corkboard view: an index-card grid of a container's direct children.

Reuses the BinderModel; ``setRootIndex(container_idx)`` decides which
container to display. The same model powers the binder tree, so add /
remove / move / dataChanged updates flow to both views automatically.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from skribe.model.binder_model import SYNOPSIS_ROLE


CARD_W = 220
CARD_H = 150
CARD_MARGIN = 18
TITLE_BAR_H = 28
TEXT_PAD = 8

BG_CORK = QColor("#f4ecd8")
CARD_FILL = QColor("#ffffff")
CARD_BORDER = QColor("#b9a47c")
CARD_DIVIDER = QColor("#d8c79b")
TITLE_FILL = QColor("#fbf6e6")
SELECTED_BORDER = QColor("#d4a017")
TITLE_COLOR = QColor("#222222")
SYNOPSIS_COLOR = QColor("#3a3a3a")
SYNOPSIS_PLACEHOLDER = QColor("#9a9a9a")


class IndexCardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: ARG002
        return QSize(CARD_W, CARD_H)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(2, 2, -3, -3)
        selected = bool(option.state & QStyle.State_Selected)

        painter.setPen(QPen(SELECTED_BORDER if selected else CARD_BORDER, 2 if selected else 1))
        painter.setBrush(CARD_FILL)
        painter.drawRoundedRect(rect, 4, 4)

        # Title bar — rounded only at the top; flatten the bottom against the divider.
        title_rect = QRect(rect.left(), rect.top(), rect.width(), TITLE_BAR_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(TITLE_FILL)
        painter.drawRoundedRect(title_rect.adjusted(1, 1, -1, 0), 4, 4)
        painter.drawRect(QRect(
            title_rect.left() + 1,
            title_rect.bottom() - 5,
            title_rect.width() - 2,
            6,
        ))

        painter.setPen(QPen(CARD_DIVIDER, 1))
        painter.drawLine(
            rect.left() + 6, title_rect.bottom() + 1,
            rect.right() - 6, title_rect.bottom() + 1,
        )

        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(TITLE_COLOR)
        title = str(index.data(Qt.DisplayRole) or "")
        text_rect = title_rect.adjusted(TEXT_PAD, 0, -TEXT_PAD, 0)
        elided = painter.fontMetrics().elidedText(title, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)

        synopsis = str(index.data(SYNOPSIS_ROLE) or "").strip()
        body_rect = QRect(
            rect.left() + TEXT_PAD,
            title_rect.bottom() + 6,
            rect.width() - 2 * TEXT_PAD,
            rect.bottom() - title_rect.bottom() - 12,
        )
        body_font = QFont(option.font)
        body_font.setItalic(not synopsis)
        painter.setFont(body_font)
        painter.setPen(SYNOPSIS_COLOR if synopsis else SYNOPSIS_PLACEHOLDER)
        painter.drawText(
            body_rect,
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
            synopsis or "(no synopsis)",
        )

        painter.restore()

    def createEditor(self, parent, option, index):  # noqa: ARG002
        editor = QLineEdit(parent)
        editor.setFrame(True)
        return editor

    def setEditorData(self, editor: QLineEdit, index: QModelIndex) -> None:
        editor.setText(str(index.data(Qt.EditRole) or ""))
        editor.selectAll()

    def setModelData(self, editor: QLineEdit, model, index: QModelIndex) -> None:
        model.setData(index, editor.text(), Qt.EditRole)

    def updateEditorGeometry(self, editor: QLineEdit, option, index) -> None:  # noqa: ARG002
        rect = option.rect
        editor.setGeometry(
            rect.left() + 4,
            rect.top() + 3,
            rect.width() - 8,
            TITLE_BAR_H + 2,
        )


class CorkboardView(QListView):
    """Linear corkboard: index cards laid out left-to-right, wrapping."""

    card_activated = Signal(QModelIndex)
    context_menu_requested = Signal(QModelIndex, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # ListMode (not IconMode) with LeftToRight flow + wrapping gives the
        # same visual grid but routes drag-and-drop through the model normally.
        # IconMode + Movement.Static blocks the view from emitting drop events,
        # which made cards snap back to their original position.
        self.setViewMode(QListView.ListMode)
        self.setResizeMode(QListView.Adjust)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(CARD_MARGIN)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked
        )
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setItemDelegate(IndexCardDelegate(self))

        self.setStyleSheet(
            f"QListView {{ background-color: {BG_CORK.name()}; border: none; }}"
        )

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.clicked.connect(self.card_activated.emit)

    def _on_context_menu(self, pos: QPoint) -> None:
        idx = self.indexAt(pos)
        global_pos = self.viewport().mapToGlobal(pos)
        self.context_menu_requested.emit(idx, global_pos)
