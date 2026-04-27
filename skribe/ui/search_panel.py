"""Project-wide search UI mounted in the left-side tab area.

Modeled on Scrivener 3's Project Search: a term field, scope and
operator dropdowns, two option toggles, and a results list. Editing
the term re-runs the search after a short debounce. Activating a
result asks the main window to navigate to that document; the editor
overlays match highlights via QTextEdit ExtraSelections, so the
underlying document is never modified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Scope: which item field(s) the search probes.
SCOPE_ALL = "All"
SCOPE_TITLE = "Title"
SCOPE_TEXT = "Text"
SCOPE_SYNOPSIS = "Synopsis"

# Operator: how the term itself is interpreted.
OP_ANY = "Any Word"
OP_ALL = "All Words"
OP_PHRASE = "Exact Phrase"
OP_WHOLE = "Whole Word"
OP_REGEX = "RegEx"


@dataclass(frozen=True)
class SearchQuery:
    term: str
    scope: str
    operator: str
    case_sensitive: bool
    invert: bool

    def is_active(self) -> bool:
        return bool(self.term)


def _compile_pattern(query: SearchQuery) -> Optional[re.Pattern]:
    """Build the regex used to *find* hits.

    For All-Words mode this still returns a "match any token" pattern;
    the all-tokens-present check is done separately in
    :func:`matches_document`. The same pattern drives editor highlights,
    so every individual token gets painted.
    """
    if not query.term:
        return None
    flags = 0 if query.case_sensitive else re.IGNORECASE
    op = query.operator
    if op == OP_REGEX:
        try:
            return re.compile(query.term, flags)
        except re.error:
            return None
    if op == OP_PHRASE:
        return re.compile(re.escape(query.term), flags)
    tokens = [t for t in query.term.split() if t]
    if not tokens:
        return None
    if op == OP_WHOLE:
        body = "|".join(rf"\b{re.escape(t)}\b" for t in tokens)
    else:  # OP_ANY, OP_ALL
        body = "|".join(re.escape(t) for t in tokens)
    return re.compile(body, flags)


def find_match_ranges(text: str, query: SearchQuery) -> list[tuple[int, int]]:
    """All non-overlapping (start, end) match positions in ``text``."""
    pat = _compile_pattern(query)
    if pat is None or not text:
        return []
    return [(m.start(), m.end()) for m in pat.finditer(text)]


def matches_document(text: str, query: SearchQuery) -> bool:
    """Predicate driving the result list.

    Honors All-Words (every token must appear) and Invert.
    """
    if not query.term:
        return False
    pat = _compile_pattern(query)
    if pat is None:
        return query.invert  # invalid regex: only invert can still produce hits
    matched = pat.search(text or "") is not None
    if matched and query.operator == OP_ALL:
        flags = 0 if query.case_sensitive else re.IGNORECASE
        tokens = [t for t in query.term.split() if t]
        for tok in tokens:
            if not re.search(re.escape(tok), text or "", flags):
                matched = False
                break
    return matched != query.invert


def count_matches(text: str, query: SearchQuery) -> int:
    pat = _compile_pattern(query)
    if pat is None or not text:
        return 0
    return sum(1 for _ in pat.finditer(text))


def plain_text_from_html(html: str) -> str:
    """Strip an HTML body to plain text using Qt's parser.

    Identical traversal to QTextEdit, so character positions found here
    line up with positions in the live editor when the same HTML loads.
    """
    doc = QTextDocument()
    doc.setHtml(html or "")
    return doc.toPlainText()


class SearchPanel(QWidget):
    """Left-rail search pane.

    Emits ``query_changed`` after a short debounce so the main window
    can refresh editor highlights and re-run the project sweep, and
    ``result_activated`` when a result row is double-clicked or Enter'd.
    """

    query_changed = Signal(object)        # SearchQuery
    result_activated = Signal(str)        # item uuid

    def __init__(self, parent=None):
        super().__init__(parent)
        self._term = QLineEdit(self)
        self._term.setPlaceholderText("Search the project…")
        self._term.setClearButtonEnabled(True)

        self._scope = QComboBox(self)
        for s in (SCOPE_ALL, SCOPE_TITLE, SCOPE_TEXT, SCOPE_SYNOPSIS):
            self._scope.addItem(s)

        self._op = QComboBox(self)
        for o in (OP_ANY, OP_ALL, OP_PHRASE, OP_WHOLE, OP_REGEX):
            self._op.addItem(o)

        self._case = QCheckBox("Match case", self)
        self._invert = QCheckBox("Invert", self)

        self._summary = QLabel("", self)
        self._summary.setStyleSheet("color: gray;")

        self._results = QListWidget(self)
        self._results.setUniformItemSizes(True)
        self._results.setSelectionMode(QListWidget.SingleSelection)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(QLabel("In:"))
        row1.addWidget(self._scope, 1)
        row1.addWidget(QLabel("Op:"))
        row1.addWidget(self._op, 1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(self._case)
        row2.addWidget(self._invert)
        row2.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._term)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addWidget(self._summary)
        layout.addWidget(self._results, 1)

        # Debounce: re-run after the user pauses to avoid hammering the
        # filesystem on every keystroke.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._emit_query)

        self._term.textChanged.connect(self._on_term_changed)
        self._term.returnPressed.connect(self._emit_query)
        self._scope.currentIndexChanged.connect(lambda _i: self._emit_query())
        self._op.currentIndexChanged.connect(lambda _i: self._emit_query())
        self._case.toggled.connect(lambda _b: self._emit_query())
        self._invert.toggled.connect(lambda _b: self._emit_query())

        self._results.itemActivated.connect(self._on_activated)
        self._results.itemClicked.connect(self._on_activated)

    # --- public API --------------------------------------------------

    def focus_term(self) -> None:
        self._term.setFocus()
        self._term.selectAll()

    def query(self) -> SearchQuery:
        return SearchQuery(
            term=self._term.text().strip(),
            scope=self._scope.currentText(),
            operator=self._op.currentText(),
            case_sensitive=self._case.isChecked(),
            invert=self._invert.isChecked(),
        )

    def set_results(self, hits: list[tuple[str, str, int]], total_docs: int) -> None:
        """Populate the results list.

        ``hits`` is ``[(uuid, title, match_count), …]``. ``match_count``
        is 0 for invert-mode rows (where there are no in-doc hits to
        count, by definition).
        """
        self._results.clear()
        for uuid, title, count in hits:
            label = title or "(untitled)"
            if count > 0:
                label = f"{label}    [{count}]"
            row = QListWidgetItem(label, self._results)
            row.setData(Qt.UserRole, uuid)
        if not self._term.text().strip():
            self._summary.setText("")
        elif not hits:
            self._summary.setText(f"No matches in {total_docs} item(s).")
        else:
            self._summary.setText(f"{len(hits)} of {total_docs} item(s) match.")

    # --- internals ---------------------------------------------------

    def _on_term_changed(self, _text: str) -> None:
        # Re-run after the user pauses; clearing the field still emits
        # so the editor's highlights drop to nothing immediately.
        if not self._term.text().strip():
            self._timer.stop()
            self._emit_query()
        else:
            self._timer.start()

    def _emit_query(self) -> None:
        self._timer.stop()
        self.query_changed.emit(self.query())

    def _on_activated(self, item: QListWidgetItem) -> None:
        uuid = item.data(Qt.UserRole)
        if isinstance(uuid, str) and uuid:
            self.result_activated.emit(uuid)
