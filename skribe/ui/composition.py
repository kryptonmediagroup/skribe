"""Composition Mode — distraction-free fullscreen editor.

A frameless fullscreen window containing a single QTextEdit that shares
the main editor's QTextDocument.  Edits made in either view are live in
both.  The dark surround fades in on open and the text area is
width-constrained for comfortable reading.

Toggle with F11 from the main window or press Escape / F11 inside the
composition window to return.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Maximum width (px) of the text area — keeps lines readable on wide monitors.
_MAX_TEXT_WIDTH = 780
# Surround color — a near-black that sits behind the text area.
_SURROUND_COLOR = "#1a1a1a"
# Page colors for the composition text area.
_PAGE_BG = "#2b2b2b"
_PAGE_TEXT = "#d8d8d8"
_SELECTION_BG = "#3d6080"
_SELECTION_TEXT = "#ffffff"


class CompositionWindow(QWidget):
    """Fullscreen distraction-free editor.

    Shares a ``QTextDocument`` with the main editor so that edits
    propagate instantly in both directions.
    """

    closed = Signal()  # emitted when the user leaves composition mode

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowState(Qt.WindowFullScreen)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        # Dark surround
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(_SURROUND_COLOR))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        # Text editor — styled for the dark composition environment.
        self._text = QTextEdit(self)
        self._text.setFrameShape(QTextEdit.NoFrame)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_page_palette()

        # Center the text area with a max width.
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 40, 0, 40)
        inner.addWidget(self._text)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        outer.addLayout(inner, 0)
        outer.addStretch(1)

        # Keyboard shortcuts — Escape and F11 both exit.
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._leave)
        QShortcut(QKeySequence(Qt.Key_F11), self, self._leave)

    # --- public API --------------------------------------------------

    def enter(self, document: QTextDocument, font: QFont) -> None:
        """Activate composition mode on *document* with *font*."""
        self._text.setDocument(document)
        self._text.setFont(font)
        self._text.setMaximumWidth(_MAX_TEXT_WIDTH)
        self._text.setMinimumWidth(_MAX_TEXT_WIDTH)
        self.showFullScreen()
        self._text.setFocus()

    # --- internals ---------------------------------------------------

    def _leave(self) -> None:
        # Detach the shared document so the main editor owns it again
        # without interference from this widget's destruction.
        self._text.setDocument(QTextDocument())
        self.hide()
        self.closed.emit()

    def _apply_page_palette(self) -> None:
        pal = self._text.palette()
        pal.setColor(QPalette.Base, QColor(_PAGE_BG))
        pal.setColor(QPalette.Text, QColor(_PAGE_TEXT))
        pal.setColor(QPalette.Highlight, QColor(_SELECTION_BG))
        pal.setColor(QPalette.HighlightedText, QColor(_SELECTION_TEXT))
        self._text.setPalette(pal)
        self._text.viewport().setPalette(pal)

    def keyPressEvent(self, event) -> None:
        # Catch F11 even if the shortcut doesn't fire (some platforms).
        if event.key() == Qt.Key_F11:
            self._leave()
            return
        super().keyPressEvent(event)
