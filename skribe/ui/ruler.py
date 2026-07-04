"""Horizontal ruler widget for the text editor.

Draws a graduated ruler above the editor that reflects the text area's
pixel width.  Major ticks at every inch, minor ticks at quarter-inches.
The ruler aligns with the QTextEdit's viewport so the graduation matches
the text flow area exactly.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QTextEdit, QWidget

# Ruler height in pixels.
_RULER_HEIGHT = 24
# Logical DPI assumed for inch marks.  Qt reports the physical screen DPI
# via QScreen, but for a document ruler the logical 96 dpi convention
# gives a result that matches what the user expects on screen.
_LOGICAL_DPI = 96
# Tick heights (px from bottom of ruler).
_MAJOR_TICK = 12
_HALF_TICK = 8
_QUARTER_TICK = 5
# Colors.
_RULER_BG = QColor("#e8e8e8")
_RULER_BORDER = QColor("#c0c0c0")
_TICK_COLOR = QColor("#555555")
_LABEL_COLOR = QColor("#444444")
# First-line indent marker color.
_INDENT_COLOR = QColor("#3080c0")


class RulerWidget(QWidget):
    """Graduated inch-ruler that sits above a QTextEdit."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text_edit: Optional[QTextEdit] = None
        self._indent_px: float = 0.0
        self._left_margin_px: float = 0.0
        self.setFixedHeight(_RULER_HEIGHT)
        self._label_font = QFont(self.font())
        self._label_font.setPointSize(7)

    # --- public API --------------------------------------------------

    def attach(self, text_edit: QTextEdit) -> None:
        """Bind to a QTextEdit so the ruler tracks its viewport geometry."""
        self._text_edit = text_edit
        self.update()

    def set_block_indent(self, indent_px: float, left_margin_px: float = 0.0) -> None:
        """Update the first-line indent and left margin to draw."""
        if indent_px != self._indent_px or left_margin_px != self._left_margin_px:
            self._indent_px = indent_px
            self._left_margin_px = left_margin_px
            self.update()

    # --- painting ----------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._text_edit is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width()
        h = self.height()

        # Background.
        p.fillRect(0, 0, w, h, _RULER_BG)

        # Bottom border line.
        p.setPen(QPen(_RULER_BORDER, 1))
        p.drawLine(0, h - 1, w, h - 1)

        # Map the text area's viewport rect into ruler coordinates.
        vp = self._text_edit.viewport()
        vp_left = vp.mapTo(self._text_edit, vp.rect().topLeft()).x()
        text_edit_left = self._text_edit.mapTo(self.parent(), self._text_edit.rect().topLeft()).x()
        ruler_left = self.mapFrom(self.parent(), self.rect().topLeft()).x()
        origin_x = text_edit_left - ruler_left + vp_left
        doc = self._text_edit.document()
        doc_margin = doc.documentMargin()
        text_start = origin_x + doc_margin
        text_width = vp.width() - 2 * doc_margin

        if text_width <= 0:
            p.end()
            return

        # Draw tick marks every quarter inch across the text area.
        quarter_inch = _LOGICAL_DPI / 4.0
        p.setPen(QPen(_TICK_COLOR, 1))
        p.setFont(self._label_font)
        fm = QFontMetrics(self._label_font)

        tick = 0
        while True:
            x = text_start + tick * quarter_inch
            if x > text_start + text_width + 1:
                break
            tick_h = _QUARTER_TICK
            if tick % 4 == 0:
                tick_h = _MAJOR_TICK
            elif tick % 2 == 0:
                tick_h = _HALF_TICK
            ix = int(round(x))
            p.drawLine(ix, h - 1 - tick_h, ix, h - 2)

            # Label every inch.
            if tick % 4 == 0 and tick > 0:
                label = str(tick // 4)
                lw = fm.horizontalAdvance(label)
                p.setPen(QPen(_LABEL_COLOR, 1))
                p.drawText(ix - lw // 2, h - 1 - tick_h - 2, label)
                p.setPen(QPen(_TICK_COLOR, 1))
            tick += 1

        # First-line indent marker — small downward-pointing triangle.
        if self._indent_px > 0:
            indent_x = int(round(text_start + self._left_margin_px + self._indent_px))
            p.setPen(Qt.NoPen)
            p.setBrush(_INDENT_COLOR)
            tri_size = 5
            from PySide6.QtGui import QPolygon
            from PySide6.QtCore import QPoint
            triangle = QPolygon([
                QPoint(indent_x, h - 2),
                QPoint(indent_x - tri_size, h - 2 - tri_size * 2),
                QPoint(indent_x + tri_size, h - 2 - tri_size * 2),
            ])
            p.drawPolygon(triangle)

        p.end()
