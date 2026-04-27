"""Spell-check syntax highlighter for the editor.

Paints a red ``SpellCheckUnderline`` under any whitespace-delimited token
the :class:`SpellChecker` doesn't recognize. We deliberately keep the
tokenizer crude (regex over letters + simple apostrophes) — Qt's word
boundary primitives don't agree with hunspell's, so the simple regex
gives more predictable results.

Highlighting respects existing character formats: we only set the
underline style/color, not the foreground or background, so this layers
cleanly on top of comment highlights, bold/italic, etc.
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat

from skribe.spellcheck import SpellChecker

# Words: letters (incl. unicode) plus internal apostrophes. The hyphen is
# left out on purpose — hunspell judges "well-being" as two words, so we
# split the same way.
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


class SpellHighlighter(QSyntaxHighlighter):
    """Underlines unknown words red. Toggle via ``setEnabled``."""

    def __init__(self, document, checker: SpellChecker):
        super().__init__(document)
        self._checker = checker
        self._enabled = True
        self._fmt = QTextCharFormat()
        # Both flags are needed: setUnderlineStyle alone leaves
        # fontUnderline=False on Qt 6, and the renderer's gate is the
        # boolean — without it the wavy line is never drawn.
        # SpellCheckUnderline is also unreliable on Linux because several
        # Qt styles map SH_SpellCheckUnderlineStyle to NoUnderline; using
        # WaveUnderline explicitly avoids that platform lottery.
        self._fmt.setFontUnderline(True)
        self._fmt.setUnderlineStyle(QTextCharFormat.WaveUnderline)
        self._fmt.setUnderlineColor(QColor(Qt.red))

    # --- public toggles ----------------------------------------------

    def setEnabled(self, enabled: bool) -> None:
        if self._enabled == bool(enabled):
            return
        self._enabled = bool(enabled)
        self.rehighlight()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_checker(self, checker: SpellChecker) -> None:
        self._checker = checker
        if self._enabled:
            self.rehighlight()

    def refresh(self) -> None:
        """Re-run the highlighter — call after the personal dictionary changes."""
        if self._enabled:
            self.rehighlight()

    # --- block-level highlight pass ---------------------------------

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        if not self._enabled or not self._checker.is_ready or not text:
            return
        for match in _WORD_RE.finditer(text):
            word = match.group(0)
            if not self._checker.check(word):
                self.setFormat(match.start(), match.end() - match.start(), self._fmt)

    # --- queries used by the right-click menu -----------------------

    def word_at(self, text: str, position: int) -> Optional[tuple[int, int, str]]:
        """Return ``(start, end, word)`` covering ``position`` in ``text``, or None.

        ``position`` is relative to the block. Used to find the misspelled
        word under the right-click point so the menu can offer suggestions.
        """
        for match in _WORD_RE.finditer(text):
            if match.start() <= position <= match.end():
                return match.start(), match.end(), match.group(0)
        return None
