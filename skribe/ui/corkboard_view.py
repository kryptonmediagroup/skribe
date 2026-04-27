"""Corkboard view: an index-card grid of a container's direct children.

Reuses the BinderModel; ``setRootIndex(container_idx)`` decides which
container to display. The same model powers the binder tree, so add /
remove / move / dataChanged updates flow to both views automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
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
from skribe.themes import Theme


CARD_W = 220
CARD_H = 150
CARD_MARGIN = 18
TITLE_BAR_H = 28
TEXT_PAD = 8

CORK_TEXTURE_PATH = Path(__file__).resolve().parent.parent / "resources" / "textures" / "cork.jpg"

_cork_pixmap_cache: QPixmap | None = None


def _cork_pixmap() -> QPixmap | None:
    """Lazy-load the cork texture once. Returns None if the file is missing
    or fails to decode, so the caller can fall back to a solid color."""
    global _cork_pixmap_cache
    if _cork_pixmap_cache is None and CORK_TEXTURE_PATH.is_file():
        pm = QPixmap(str(CORK_TEXTURE_PATH))
        if not pm.isNull():
            _cork_pixmap_cache = pm
    return _cork_pixmap_cache


@dataclass(frozen=True)
class CorkPalette:
    background: QColor
    card_fill: QColor
    card_border: QColor
    card_divider: QColor
    title_fill: QColor
    selected_border: QColor
    title_color: QColor
    synopsis_color: QColor
    placeholder: QColor


_LIGHT_CORK = CorkPalette(
    background=QColor("#f4ecd8"),
    card_fill=QColor("#ffffff"),
    card_border=QColor("#b9a47c"),
    card_divider=QColor("#d8c79b"),
    title_fill=QColor("#fbf6e6"),
    selected_border=QColor("#d4a017"),
    title_color=QColor("#222222"),
    synopsis_color=QColor("#3a3a3a"),
    placeholder=QColor("#9a9a9a"),
)

_DARK_CORK = CorkPalette(
    background=QColor("#2b2620"),
    card_fill=QColor("#3a352e"),
    card_border=QColor("#6a5a3e"),
    card_divider=QColor("#4d4434"),
    title_fill=QColor("#43392c"),
    selected_border=QColor("#d4a017"),
    title_color=QColor("#f0e6d2"),
    synopsis_color=QColor("#d6cdba"),
    placeholder=QColor("#7a7261"),
)

_SOLARIZED_DARK_CORK = CorkPalette(
    background=QColor("#073642"),
    card_fill=QColor("#0a4352"),
    card_border=QColor("#586e75"),
    card_divider=QColor("#0e4858"),
    title_fill=QColor("#0d4a5b"),
    selected_border=QColor("#b58900"),
    title_color=QColor("#fdf6e3"),
    synopsis_color=QColor("#eee8d5"),
    placeholder=QColor("#586e75"),
)


def cork_palette_for(theme: Theme) -> CorkPalette:
    """Map a Theme to a cork palette. Light/Sepia/System use the warm cork
    look; dark themes get tone-matched variants so the cards still feel
    like cards, not plain panels."""
    if theme.key == "dark":
        return _DARK_CORK
    if theme.key == "solarized_dark":
        return _SOLARIZED_DARK_CORK
    return _LIGHT_CORK


class IndexCardDelegate(QStyledItemDelegate):
    def __init__(self, view: "CorkboardView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index):  # noqa: ARG002
        return QSize(CARD_W, CARD_H)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        cork = self._view.cork_palette
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(2, 2, -3, -3)
        selected = bool(option.state & QStyle.State_Selected)

        painter.setPen(QPen(
            cork.selected_border if selected else cork.card_border,
            2 if selected else 1,
        ))
        painter.setBrush(cork.card_fill)
        painter.drawRoundedRect(rect, 4, 4)

        # Title bar — rounded only at the top; flatten the bottom against the divider.
        title_rect = QRect(rect.left(), rect.top(), rect.width(), TITLE_BAR_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(cork.title_fill)
        painter.drawRoundedRect(title_rect.adjusted(1, 1, -1, 0), 4, 4)
        painter.drawRect(QRect(
            title_rect.left() + 1,
            title_rect.bottom() - 5,
            title_rect.width() - 2,
            6,
        ))

        painter.setPen(QPen(cork.card_divider, 1))
        painter.drawLine(
            rect.left() + 6, title_rect.bottom() + 1,
            rect.right() - 6, title_rect.bottom() + 1,
        )

        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(cork.title_color)
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
        painter.setPen(cork.synopsis_color if synopsis else cork.placeholder)
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
        self._cork = _LIGHT_CORK
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

        self._refresh_stylesheet()

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.clicked.connect(self.card_activated.emit)

    @property
    def cork_palette(self) -> CorkPalette:
        return self._cork

    def apply_theme(self, theme: Theme) -> None:
        self._cork = cork_palette_for(theme)
        self._refresh_stylesheet()
        self.viewport().update()

    def _refresh_stylesheet(self) -> None:
        # Drop the frame border via stylesheet, but leave the viewport
        # transparent so the QPalette TexturePattern brush below shows
        # through. Stylesheet background-image on QAbstractScrollArea is
        # unreliable for full-area tiling; the palette brush is.
        self.setStyleSheet("QListView { border: none; background: transparent; }")
        self._refresh_viewport_background()

    def _refresh_viewport_background(self) -> None:
        viewport = self.viewport()
        viewport.setAutoFillBackground(True)
        pal = viewport.palette()
        pixmap = _cork_pixmap()
        if pixmap is not None:
            # QBrush(QPixmap) defaults to TexturePattern — Qt tiles the
            # pixmap across the entire fill region in both X and Y.
            brush = QBrush(pixmap)
        else:
            brush = QBrush(self._cork.background)
        pal.setBrush(QPalette.Base, brush)
        pal.setBrush(QPalette.Window, brush)
        viewport.setPalette(pal)

    def _on_context_menu(self, pos: QPoint) -> None:
        idx = self.indexAt(pos)
        global_pos = self.viewport().mapToGlobal(pos)
        self.context_menu_requested.emit(idx, global_pos)
