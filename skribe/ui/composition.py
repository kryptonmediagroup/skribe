"""Composition Mode — distraction-free fullscreen editor.

A frameless fullscreen window containing a single QTextEdit that shares
the main editor's QTextDocument.  Edits made in either view are live in
both.  The text area fills the full window width.

Toggle with F11 from the main window or press Escape / F11 inside the
composition window to return.  Alt+Shift+F and Alt+Shift+R toggle the
format bar and ruler, persisting the state to app settings.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QPalette,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFontComboBox,
    QSpinBox,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from skribe.settings import Keys, app_settings
from skribe.ui.ruler import RulerWidget

# Surround / page colors for the dark composition environment.
_SURROUND_COLOR = "#1a1a1a"
_PAGE_BG = "#2b2b2b"
_PAGE_TEXT = "#d8d8d8"
_SELECTION_BG = "#3d6080"
_SELECTION_TEXT = "#ffffff"
_TOOLBAR_BG = "#2b2b2b"
_TOOLBAR_TEXT = "#d8d8d8"


class CompositionWindow(QWidget):
    """Fullscreen distraction-free editor.

    Shares a ``QTextDocument`` with the main editor so that edits
    propagate instantly in both directions.
    """

    closed = Signal()  # emitted when the user leaves composition mode

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._settings = app_settings()

        # Dark surround
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(_SURROUND_COLOR))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        # Format toolbar — dark-themed.
        self._toolbar = self._build_toolbar()
        self._toolbar.setVisible(
            bool(self._settings.get(Keys.VIEW_FORMAT_BAR_VISIBLE))
        )

        # Ruler
        self._ruler = RulerWidget(self)
        self._ruler.setVisible(
            bool(self._settings.get(Keys.VIEW_RULER_VISIBLE))
        )

        # Text editor — fills full width, styled for dark environment.
        self._text = QTextEdit(self)
        self._text.setFrameShape(QTextEdit.NoFrame)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_page_palette()
        self._ruler.attach(self._text)

        # Layout — toolbar, ruler, then text filling all remaining space.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._ruler)
        layout.addWidget(self._text, 1)

        # Keyboard shortcuts.
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._leave)
        QShortcut(QKeySequence(Qt.Key_F11), self, self._leave)
        QShortcut(QKeySequence("Alt+Shift+F"), self, self._toggle_format_bar)
        QShortcut(QKeySequence("Alt+Shift+R"), self, self._toggle_ruler)

        # Sync ruler indent when cursor moves.
        self._text.cursorPositionChanged.connect(self._sync_ruler)

    # --- public API --------------------------------------------------

    def enter(self, document: QTextDocument, font: QFont) -> None:
        """Activate composition mode on *document* with *font*."""
        self._text.setDocument(document)
        self._text.setFont(font)
        # Refresh visibility from settings in case it changed in the
        # main window since this object was last shown.
        self._toolbar.setVisible(
            bool(self._settings.get(Keys.VIEW_FORMAT_BAR_VISIBLE))
        )
        self._ruler.setVisible(
            bool(self._settings.get(Keys.VIEW_RULER_VISIBLE))
        )
        self.showFullScreen()
        self._text.setFocus()
        self._sync_ruler()
        self._sync_toolbar_state()

    # --- internals ---------------------------------------------------

    def _leave(self) -> None:
        self._text.setDocument(QTextDocument())
        self.hide()
        self.closed.emit()

    def _toggle_format_bar(self) -> None:
        visible = self._toolbar.isHidden()
        self._toolbar.setVisible(visible)
        self._settings.set(Keys.VIEW_FORMAT_BAR_VISIBLE, visible)

    def _toggle_ruler(self) -> None:
        visible = self._ruler.isHidden()
        self._ruler.setVisible(visible)
        self._settings.set(Keys.VIEW_RULER_VISIBLE, visible)
        if visible:
            self._sync_ruler()

    def _sync_ruler(self) -> None:
        if self._ruler.isHidden():
            return
        block_fmt = self._text.textCursor().blockFormat()
        self._ruler.set_block_indent(
            block_fmt.textIndent(),
            block_fmt.leftMargin(),
        )

    def _apply_page_palette(self) -> None:
        pal = self._text.palette()
        pal.setColor(QPalette.Base, QColor(_PAGE_BG))
        pal.setColor(QPalette.Text, QColor(_PAGE_TEXT))
        pal.setColor(QPalette.Highlight, QColor(_SELECTION_BG))
        pal.setColor(QPalette.HighlightedText, QColor(_SELECTION_TEXT))
        self._text.setPalette(pal)
        self._text.viewport().setPalette(pal)

    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar("Format", self)
        tb.setMovable(False)
        tb.setStyleSheet(
            f"QToolBar {{ background: {_TOOLBAR_BG}; border: none; }}"
            f"QToolBar QToolButton {{ color: {_TOOLBAR_TEXT}; }}"
        )

        self._act_bold = QAction("B", tb)
        self._act_bold.setCheckable(True)
        self._act_bold.setShortcut(QKeySequence.Bold)
        f = self._act_bold.font(); f.setBold(True); self._act_bold.setFont(f)
        self._act_bold.triggered.connect(self._toggle_bold)
        tb.addAction(self._act_bold)

        self._act_italic = QAction("I", tb)
        self._act_italic.setCheckable(True)
        self._act_italic.setShortcut(QKeySequence.Italic)
        f = self._act_italic.font(); f.setItalic(True); self._act_italic.setFont(f)
        self._act_italic.triggered.connect(self._toggle_italic)
        tb.addAction(self._act_italic)

        self._act_underline = QAction("U", tb)
        self._act_underline.setCheckable(True)
        self._act_underline.setShortcut(QKeySequence.Underline)
        f = self._act_underline.font(); f.setUnderline(True); self._act_underline.setFont(f)
        self._act_underline.triggered.connect(self._toggle_underline)
        tb.addAction(self._act_underline)

        tb.addSeparator()

        act_left = QAction("⬅", tb)
        act_left.setToolTip("Align left")
        act_left.triggered.connect(lambda: self._text.setAlignment(Qt.AlignLeft))
        tb.addAction(act_left)

        act_center = QAction("↔", tb)
        act_center.setToolTip("Align center")
        act_center.triggered.connect(lambda: self._text.setAlignment(Qt.AlignCenter))
        tb.addAction(act_center)

        act_right = QAction("➡", tb)
        act_right.setToolTip("Align right")
        act_right.triggered.connect(lambda: self._text.setAlignment(Qt.AlignRight))
        tb.addAction(act_right)

        act_justify = QAction("≡", tb)
        act_justify.setToolTip("Justify")
        act_justify.triggered.connect(lambda: self._text.setAlignment(Qt.AlignJustify))
        tb.addAction(act_justify)

        return tb

    # --- formatting ---------------------------------------------------

    def _merge_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self._text.mergeCurrentCharFormat(fmt)

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        current = self._text.currentCharFormat().font().weight()
        fmt.setFontWeight(QFont.Normal if current >= QFont.Bold else QFont.Bold)
        self._merge_char_format(fmt)

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self._text.currentCharFormat().font().italic())
        self._merge_char_format(fmt)

    def _toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self._text.currentCharFormat().font().underline())
        self._merge_char_format(fmt)

    def _sync_toolbar_state(self) -> None:
        fmt = self._text.currentCharFormat()
        font = fmt.font()
        self._act_bold.setChecked(font.weight() >= QFont.Bold)
        self._act_italic.setChecked(font.italic())
        self._act_underline.setChecked(font.underline())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F11:
            self._leave()
            return
        super().keyPressEvent(event)
