"""Rich-text editor widget with formatting toolbar.

Honors app settings for default font family/size, first-line paragraph
indent, and auto-indent on new paragraphs. Auto-indent works by applying
the configured block-level text indent to every block when a document is
loaded — Qt inherits the block format on paragraph split, so new lines
get the indent for free.
"""
from __future__ import annotations

from typing import Optional

import uuid as _uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
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
from skribe.spellcheck import SpellChecker, is_available as spell_is_available
from skribe.themes import Theme, editor_palette
from skribe.ui.spell_highlighter import SpellHighlighter

HEADING_CHOICES = [
    ("Body", 0),
    ("Heading 1", 1),
    ("Heading 2", 2),
    ("Heading 3", 3),
]

# Custom QTextCharFormat property: UUID of the Comment anchored to this run.
COMMENT_ID_PROP = QTextFormat.UserProperty + 1
COMMENT_HIGHLIGHT_COLOR = QColor("#FFF59D")  # soft yellow

# Search-hit overlay color — distinct from the comment yellow so the
# two can coexist on the same run without ambiguity.
SEARCH_HIGHLIGHT_COLOR = QColor("#80DEEA")  # soft cyan


class _Editor(QTextEdit):
    """QTextEdit subclass that belt-and-suspenders the text-indent on new blocks."""

    def __init__(self, owner: "EditorWidget"):
        super().__init__(owner)
        self._owner = owner
        self.setAcceptRichText(True)
        self.setTabStopDistance(32)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        text = event.text()
        is_newline = event.key() in (Qt.Key_Return, Qt.Key_Enter)

        # Return on an empty, indented paragraph: Qt's default handler wipes
        # the block's first-line indent instead of inserting a new block. That
        # swallows the keystroke (cursor appears to jump to column 0) and the
        # lost indent cascades to every subsequent paragraph. Do the insert
        # ourselves so the indent carries over.
        if (
            is_newline
            and not (event.modifiers() & Qt.ShiftModifier)
            and self._owner.auto_indent_enabled()
        ):
            cursor = self.textCursor()
            if not cursor.hasSelection():
                block = cursor.block()
                block_fmt = cursor.blockFormat()
                if block.text() == "" and block_fmt.textIndent() > 0:
                    cursor.beginEditBlock()
                    cursor.insertBlock(block_fmt)
                    cursor.endEditBlock()
                    self.setTextCursor(cursor)
                    event.accept()
                    return

        # Auto-indent: belt-and-suspenders, applied *before* a printable
        # character lands on a block that somehow lost its indent.
        if (
            text
            and not is_newline
            and text.isprintable()
            and self._owner.auto_indent_enabled()
        ):
            cursor = self.textCursor()
            block_fmt = cursor.blockFormat()
            if block_fmt.headingLevel() == 0:
                target = self._owner.current_indent_px()
                if target > 0 and abs(block_fmt.textIndent() - target) > 0.5:
                    block_fmt.setTextIndent(target)
                    cursor.setBlockFormat(block_fmt)
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = self.createStandardContextMenu(event.pos())
        cursor = self.cursorForPosition(event.pos())
        first = menu.actions()[0] if menu.actions() else None

        # Spell suggestions go *first* in the menu — a misspelled word is
        # almost always why the user right-clicked, and burying corrections
        # under Cut/Copy/Paste would be a worse UX than the standard menu.
        self._owner.populate_spell_menu(menu, first, cursor.position())

        cid = _comment_id_at(self.document(), cursor.position())
        if cid:
            act_remove = QAction("Remove Comment", menu)
            act_remove.triggered.connect(
                lambda: self._owner.comment_remove_requested.emit(cid)
            )
            menu.insertAction(first, act_remove)
            menu.insertSeparator(first)
        elif self.textCursor().hasSelection():
            act_add = QAction("Add Comment", menu)
            act_add.triggered.connect(self._owner.comment_add_requested.emit)
            menu.insertAction(first, act_add)
            menu.insertSeparator(first)
        menu.exec(event.globalPos())


def _comment_id_at(document, position: int) -> Optional[str]:
    """Return the comment UUID of whatever fragment covers ``position``."""
    block = document.findBlock(position)
    if not block.isValid():
        return None
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        start = frag.position()
        end = start + frag.length()
        if start <= position < end:
            fmt = frag.charFormat()
            cid = fmt.property(COMMENT_ID_PROP)
            if isinstance(cid, str) and cid:
                return cid
            return None
        it += 1
    return None


class EditorWidget(QWidget):
    """Rich-text editor: toolbar + QTextEdit.

    Emits ``contents_changed`` when the user edits the body.
    """

    contents_changed = Signal()
    selection_changed = Signal()            # selection range or hasSelection() flipped
    comment_anchor_requested = Signal(str)  # comment uuid — emitted on caret/selection
    comment_remove_requested = Signal(str)  # comment uuid — from right-click menu
    comment_add_requested = Signal()        # from right-click menu on a selection

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._settings = app_settings()

        self._text = _Editor(self)
        self._text.setFont(self._default_font())

        # Spell-check pipeline. The checker is None when pyenchant/hunspell
        # is unavailable; the highlighter handles that quietly.
        self._spell_checker: Optional[SpellChecker] = None
        self._spell_highlighter: Optional[SpellHighlighter] = None
        if spell_is_available():
            lang = str(self._settings.get(Keys.SPELLCHECK_LANGUAGE)) or "en_US"
            self._spell_checker = SpellChecker(lang)
            self._spell_highlighter = SpellHighlighter(
                self._text.document(), self._spell_checker,
            )
            self._spell_highlighter.setEnabled(
                bool(self._settings.get(Keys.SPELLCHECK_ENABLED))
            )

        self._toolbar = self._build_toolbar()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._text)

        self._text.textChanged.connect(self.contents_changed.emit)
        self._text.cursorPositionChanged.connect(self._sync_toolbar_state)
        self._text.cursorPositionChanged.connect(self._emit_comment_at_cursor)
        self._text.selectionChanged.connect(self.selection_changed.emit)

        self.clear()

    # --- public API --------------------------------------------------

    def set_html(self, html: str) -> None:
        self._text.blockSignals(True)
        self._text.setHtml(html or "")
        self._apply_default_block_format_to_all()
        self._text.blockSignals(False)
        self._sync_toolbar_state()

    def html(self) -> str:
        return self._text.toHtml()

    def plain_text(self) -> str:
        return self._text.toPlainText()

    def word_count(self) -> int:
        text = self._text.toPlainText().strip()
        return len(text.split()) if text else 0

    def selection_word_count(self) -> int:
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            return 0
        text = cursor.selectedText().strip()
        return len(text.split()) if text else 0

    def clear(self) -> None:
        self.set_html("")
        self._text.setEnabled(False)

    def set_editable(self, editable: bool) -> None:
        self._text.setEnabled(editable)
        self._text.setReadOnly(not editable)
        self._toolbar.setEnabled(editable)

    def set_focus(self) -> None:
        self._text.setFocus()

    def cursor_position(self) -> int:
        return self._text.textCursor().position()

    def set_cursor_position(self, position: int) -> None:
        doc = self._text.document()
        pos = max(0, min(int(position), doc.characterCount() - 1))
        cursor = self._text.textCursor()
        cursor.setPosition(pos)
        self._text.setTextCursor(cursor)

    def reload_settings(self) -> None:
        """Re-read settings (font, indent) and re-apply to the current document."""
        self._text.setFont(self._default_font())
        # Update toolbar font widgets to match.
        self._font_combo.blockSignals(True)
        self._size_spin.blockSignals(True)
        self._font_combo.setCurrentFont(self._default_font())
        self._size_spin.setValue(int(self._settings.get(Keys.EDITOR_FONT_SIZE)))
        self._font_combo.blockSignals(False)
        self._size_spin.blockSignals(False)
        self._apply_default_block_format_to_all()
        self._reload_spell_settings()

    def _reload_spell_settings(self) -> None:
        if self._spell_checker is None or self._spell_highlighter is None:
            return
        lang = str(self._settings.get(Keys.SPELLCHECK_LANGUAGE)) or "en_US"
        self._spell_checker.set_language(lang)
        self._spell_highlighter.setEnabled(
            bool(self._settings.get(Keys.SPELLCHECK_ENABLED))
        )
        self._spell_highlighter.refresh()

    def set_spellcheck_enabled(self, enabled: bool) -> None:
        if self._spell_highlighter is None:
            return
        self._spell_highlighter.setEnabled(enabled)
        self._settings.set(Keys.SPELLCHECK_ENABLED, bool(enabled))

    def is_spellcheck_enabled(self) -> bool:
        return self._spell_highlighter is not None and self._spell_highlighter.is_enabled()

    def populate_spell_menu(self, menu, before, position: int) -> None:
        """Insert spell-check actions for the word at ``position`` into ``menu``.

        ``before`` is the action to insert before — passed in by the
        right-click handler so we land at the top of the menu.
        """
        if self._spell_highlighter is None or self._spell_checker is None:
            return
        if not self._spell_highlighter.is_enabled():
            return
        block = self._text.document().findBlock(position)
        if not block.isValid():
            return
        block_pos = position - block.position()
        hit = self._spell_highlighter.word_at(block.text(), block_pos)
        if hit is None:
            return
        start_in_block, end_in_block, word = hit
        if self._spell_checker.check(word):
            return  # not a misspelling — nothing to offer
        abs_start = block.position() + start_in_block
        abs_end = block.position() + end_in_block

        suggestions = self._spell_checker.suggest(word)
        if suggestions:
            for sug in suggestions:
                act = QAction(sug, menu)
                # bind sug at definition time — late binding in a loop
                # would have every action use the last-iterated value
                act.triggered.connect(
                    lambda _checked=False, s=sug: self._replace_range(abs_start, abs_end, s)
                )
                menu.insertAction(before, act)
        else:
            no_sug = QAction("(no suggestions)", menu)
            no_sug.setEnabled(False)
            menu.insertAction(before, no_sug)

        menu.insertSeparator(before)
        act_add = QAction("Add to Dictionary", menu)
        act_add.triggered.connect(lambda: self._spell_add(word))
        menu.insertAction(before, act_add)
        act_ignore = QAction("Ignore", menu)
        act_ignore.triggered.connect(lambda: self._spell_ignore(word))
        menu.insertAction(before, act_ignore)
        menu.insertSeparator(before)

    def _replace_range(self, start: int, end: int, replacement: str) -> None:
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(replacement)
        cursor.endEditBlock()

    def _spell_add(self, word: str) -> None:
        if self._spell_checker is None or self._spell_highlighter is None:
            return
        self._spell_checker.add_to_personal(word)
        self._spell_highlighter.refresh()

    def _spell_ignore(self, word: str) -> None:
        if self._spell_checker is None or self._spell_highlighter is None:
            return
        self._spell_checker.ignore_session(word)
        self._spell_highlighter.refresh()

    def apply_theme(self, theme: Theme) -> None:
        # QTextEdit is a QAbstractScrollArea — the text area is its viewport.
        # Setting the palette on the outer widget alone does not reliably
        # repaint the viewport's Base/Text on a live theme switch (only on
        # first show), so push the palette to the viewport and request a
        # repaint explicitly.
        new_pal = editor_palette(theme, self._text.palette())
        self._text.setPalette(new_pal)
        self._text.viewport().setPalette(new_pal)
        self._text.viewport().update()

    # --- comments API -----------------------------------------------

    def has_selection(self) -> bool:
        return self._text.textCursor().hasSelection()

    def new_comment_from_selection(self) -> Optional[tuple[str, int, int, str]]:
        """Highlight the current selection as a new comment anchor.

        Returns ``(comment_id, start, end, anchor_text)`` or None if there
        is no selection. The caller owns the resulting :class:`Comment`
        object; the editor only tracks the highlight + UUID on the text.
        """
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            return None
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        if end <= start:
            return None
        comment_id = str(_uuid.uuid4()).upper()
        snippet = cursor.selectedText().replace(" ", "\n")
        self.apply_comment_highlight(start, end, comment_id)
        return comment_id, start, end, snippet

    def apply_comment_highlight(self, start: int, end: int, comment_id: str) -> None:
        """Paint [start, end) yellow and tag it with ``comment_id``."""
        if end <= start:
            return
        doc = self._text.document()
        end = min(end, doc.characterCount() - 1)
        if end <= start:
            return
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(COMMENT_HIGHLIGHT_COLOR)
        fmt.setProperty(COMMENT_ID_PROP, comment_id)
        cursor.mergeCharFormat(fmt)
        cursor.endEditBlock()

    def remove_comment_highlight(self, comment_id: str) -> None:
        """Strip the yellow background + UUID property for ``comment_id``."""
        doc = self._text.document()
        block = doc.firstBlock()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        try:
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    fmt = frag.charFormat()
                    if fmt.property(COMMENT_ID_PROP) == comment_id:
                        cursor.setPosition(frag.position())
                        cursor.setPosition(
                            frag.position() + frag.length(),
                            QTextCursor.KeepAnchor,
                        )
                        clear = QTextCharFormat()
                        clear.setBackground(Qt.transparent)
                        clear.setProperty(COMMENT_ID_PROP, "")
                        cursor.mergeCharFormat(clear)
                    it += 1
                block = block.next()
        finally:
            cursor.endEditBlock()

    def scan_comment_ranges(self) -> dict[str, tuple[int, int]]:
        """Return ``{comment_id: (start, end)}`` by walking the document.

        Fragments with the same comment_id across block boundaries are
        coalesced into a single (min start, max end) range.
        """
        ranges: dict[str, tuple[int, int]] = {}
        doc = self._text.document()
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                cid = fmt.property(COMMENT_ID_PROP)
                if isinstance(cid, str) and cid:
                    start = frag.position()
                    end = start + frag.length()
                    cur = ranges.get(cid)
                    if cur is None:
                        ranges[cid] = (start, end)
                    else:
                        ranges[cid] = (min(cur[0], start), max(cur[1], end))
                it += 1
            block = block.next()
        return ranges

    def comment_id_at_cursor(self) -> Optional[str]:
        """Return the comment UUID under the caret, if any."""
        cursor = self._text.textCursor()
        fmt = cursor.charFormat()
        cid = fmt.property(COMMENT_ID_PROP)
        return cid if isinstance(cid, str) and cid else None

    def select_comment_range(self, comment_id: str) -> bool:
        """Move the caret to the comment's range and select it. Returns True if found."""
        ranges = self.scan_comment_ranges()
        hit = ranges.get(comment_id)
        if hit is None:
            return False
        start, end = hit
        cursor = self._text.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self._text.setTextCursor(cursor)
        self._text.setFocus()
        return True

    def _emit_comment_at_cursor(self) -> None:
        cid = self.comment_id_at_cursor()
        if cid:
            self.comment_anchor_requested.emit(cid)

    # --- search-hit overlays ----------------------------------------

    def set_search_highlights(self, ranges) -> None:
        """Paint a non-destructive cyan overlay on each ``(start, end)`` range.

        Implemented via QTextEdit ExtraSelections so the document itself
        is never modified — clearing the overlay leaves no trace, and
        the editor stays "clean" for save-on-switch semantics.
        """
        selections = []
        doc = self._text.document()
        max_pos = max(0, doc.characterCount() - 1)
        for start, end in ranges:
            start = max(0, min(int(start), max_pos))
            end = max(0, min(int(end), max_pos))
            if end <= start:
                continue
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format.setBackground(SEARCH_HIGHLIGHT_COLOR)
            selections.append(sel)
        self._text.setExtraSelections(selections)

    def clear_search_highlights(self) -> None:
        self._text.setExtraSelections([])

    def reveal_position(self, position: int) -> None:
        """Move caret to ``position`` and scroll it into view."""
        self.set_cursor_position(position)
        self._text.ensureCursorVisible()

    # --- helpers used by _Editor -------------------------------------

    def auto_indent_enabled(self) -> bool:
        return bool(self._settings.get(Keys.EDITOR_AUTO_INDENT))

    def current_indent_px(self) -> float:
        em = float(self._settings.get(Keys.EDITOR_FIRST_LINE_INDENT_EM))
        if em <= 0:
            return 0.0
        metrics = QFontMetricsF(self._text.font())
        em_width = metrics.horizontalAdvance("M") or metrics.averageCharWidth() or 8.0
        return em * em_width

    def current_paragraph_spacing_px(self) -> float:
        lines = float(self._settings.get(Keys.EDITOR_PARAGRAPH_SPACING_LINES))
        if lines <= 0:
            return 0.0
        return lines * QFontMetricsF(self._text.font()).lineSpacing()

    # --- internals ---------------------------------------------------

    def _default_font(self) -> QFont:
        family = str(self._settings.get(Keys.EDITOR_FONT_FAMILY))
        size = int(self._settings.get(Keys.EDITOR_FONT_SIZE))
        f = QFont(family)
        f.setPointSize(size)
        return f

    def _apply_default_block_format_to_all(self) -> None:
        """Stamp configured first-line indent + paragraph spacing on every block.

        Zeros each block's topMargin and uses bottomMargin alone to control
        inter-paragraph spacing. Qt's HTML parser sets a default topMargin
        (~12 px) on every block, which would otherwise stack on top of our
        bottomMargin and double the gap.
        """
        auto_indent = self.auto_indent_enabled()
        indent = self.current_indent_px() if auto_indent else 0.0
        spacing = self.current_paragraph_spacing_px()
        cursor = QTextCursor(self._text.document())
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.Start)
        while True:
            block_fmt = cursor.blockFormat()
            if auto_indent and block_fmt.headingLevel() == 0:
                block_fmt.setTextIndent(indent)
            block_fmt.setTopMargin(0.0)
            block_fmt.setBottomMargin(spacing)
            cursor.setBlockFormat(block_fmt)
            if not cursor.movePosition(QTextCursor.NextBlock):
                break
        cursor.endEditBlock()

    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar("Format", self)
        tb.setMovable(False)

        self._heading_combo = QComboBox(tb)
        for label, _ in HEADING_CHOICES:
            self._heading_combo.addItem(label)
        self._heading_combo.currentIndexChanged.connect(self._on_heading_changed)
        tb.addWidget(self._heading_combo)
        tb.addSeparator()

        self._font_combo = QFontComboBox(tb)
        self._font_combo.setCurrentFont(self._default_font())
        self._font_combo.setMinimumContentsLength(14)
        self._font_combo.currentFontChanged.connect(self._on_font_family_changed)
        tb.addWidget(self._font_combo)

        self._size_spin = QSpinBox(tb)
        self._size_spin.setRange(6, 72)
        self._size_spin.setValue(int(self._settings.get(Keys.EDITOR_FONT_SIZE)))
        self._size_spin.valueChanged.connect(self._on_font_size_changed)
        tb.addWidget(self._size_spin)

        tb.addSeparator()

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

        act_bullet = QAction("•", tb)
        act_bullet.setToolTip("Bulleted list")
        act_bullet.triggered.connect(lambda: self._set_list(QTextListFormat.ListDisc))
        tb.addAction(act_bullet)

        act_number = QAction("1.", tb)
        act_number.setToolTip("Numbered list")
        act_number.triggered.connect(lambda: self._set_list(QTextListFormat.ListDecimal))
        tb.addAction(act_number)

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

    # --- character-format helpers ------------------------------------

    def _merge_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self._text.mergeCurrentCharFormat(fmt)

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold if self._act_bold.isChecked() else QFont.Normal)
        self._merge_char_format(fmt)

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(self._act_italic.isChecked())
        self._merge_char_format(fmt)

    def _toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self._act_underline.isChecked())
        self._merge_char_format(fmt)

    def _on_font_family_changed(self, font: QFont) -> None:
        fmt = QTextCharFormat()
        fmt.setFontFamilies([font.family()])
        self._merge_char_format(fmt)

    def _on_font_size_changed(self, size: int) -> None:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(size))
        self._merge_char_format(fmt)

    def _set_list(self, style: QTextListFormat.Style) -> None:
        cursor = self._text.textCursor()
        cursor.beginEditBlock()
        fmt = QTextListFormat()
        fmt.setStyle(style)
        cursor.createList(fmt)
        cursor.endEditBlock()

    def _on_heading_changed(self, idx: int) -> None:
        _, level = HEADING_CHOICES[idx]
        cursor = self._text.textCursor()
        block_fmt = cursor.blockFormat()
        char_fmt = QTextCharFormat()
        if level == 0:
            block_fmt.setHeadingLevel(0)
            if self.auto_indent_enabled():
                block_fmt.setTextIndent(self.current_indent_px())
            else:
                block_fmt.setTextIndent(0.0)
            char_fmt.setFontWeight(QFont.Normal)
            char_fmt.setFontPointSize(self._text.font().pointSize())
        else:
            block_fmt.setHeadingLevel(level)
            # Headings should not have a first-line indent.
            block_fmt.setTextIndent(0.0)
            char_fmt.setFontWeight(QFont.Bold)
            char_fmt.setFontPointSize(self._text.font().pointSize() + (6 - level * 2))
        cursor.setBlockFormat(block_fmt)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.mergeCharFormat(char_fmt)

    def _sync_toolbar_state(self) -> None:
        fmt = self._text.currentCharFormat()
        font = fmt.font()
        self._act_bold.setChecked(font.weight() >= QFont.Bold)
        self._act_italic.setChecked(font.italic())
        self._act_underline.setChecked(font.underline())

        self._font_combo.blockSignals(True)
        self._size_spin.blockSignals(True)
        fam = font.family() or self._default_font().family()
        if fam:
            self._font_combo.setCurrentFont(QFont(fam))
        size = font.pointSize()
        if size <= 0:
            size = self._size_spin.value()
        self._size_spin.setValue(size)
        self._font_combo.blockSignals(False)
        self._size_spin.blockSignals(False)

        level = self._text.textCursor().blockFormat().headingLevel()
        self._heading_combo.blockSignals(True)
        self._heading_combo.setCurrentIndex(min(level, len(HEADING_CHOICES) - 1))
        self._heading_combo.blockSignals(False)
