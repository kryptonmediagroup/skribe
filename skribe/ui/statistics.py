"""Statistics dialog: word / character / page counts for the manuscript.

Two tabs — *Compiled* aggregates everything in the Draft folder marked
Include in Compile, *Selected Documents* aggregates only what's
currently selected in the binder. Computation runs synchronously when
the dialog opens; for projects in the size range Skribe targets (one
novel, ~hundreds of documents) this is fast enough to not need a
progress dialog.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from skribe.model.project import BinderItem
from skribe.stats import Stats, compute_stats

# Visual groups in the dialog — each tuple is (label, attribute path or
# computed key). The renderer inserts a separator line between groups.
_GROUPS: list[list[tuple[str, str]]] = [
    [
        ("Words", "words"),
        ("Characters", "characters"),
        ("Characters (No Spaces)", "characters_no_spaces"),
    ],
    [
        ("Paragraphs", "paragraphs"),
        ("Sentences", "sentences"),
        ("Average Paragraph Length", "avg_paragraph_words"),
        ("Average Sentence Length", "avg_sentence_words"),
    ],
    [
        ("Documents", "documents"),
        ("Average document length", "avg_doc_words_fmt"),
        ("Longest document", "longest_doc_fmt"),
        ("Shortest document", "shortest_doc_fmt"),
    ],
    [
        ("Pages (paperback)", "pages_paperback"),
        ("Reading Time", "reading_time"),
    ],
]


def _value_for(stats: Stats, key: str) -> str:
    if key == "reading_time":
        return stats.reading_time_hms()
    if key == "avg_doc_words_fmt":
        return f"{stats.avg_doc_words:,} words" if stats.documents else "—"
    if key == "longest_doc_fmt":
        return f"{stats.longest_doc_words:,} words" if stats.documents else "—"
    if key == "shortest_doc_fmt":
        return f"{stats.shortest_doc_words:,} words" if stats.documents else "—"
    val = getattr(stats, key, 0)
    return f"{int(val):,}"


class StatisticsDialog(QDialog):
    """Modeless-friendly stats dialog with Compiled and Selected tabs."""

    def __init__(
        self,
        compiled: list[BinderItem],
        selected: list[BinderItem],
        read_body: Callable[[str], Optional[str]],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Statistics")
        self.setMinimumWidth(360)

        self._read_body = read_body
        compiled_stats = compute_stats(compiled, read_body)
        selected_stats = compute_stats(selected, read_body)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_tab(compiled_stats, empty_msg=None), "Compiled")
        sel_empty = "No documents selected." if not selected else None
        tabs.addTab(
            self._build_tab(selected_stats, empty_msg=sel_empty),
            "Selected Documents",
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=self)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _build_tab(self, stats: Stats, empty_msg: Optional[str]) -> QWidget:
        w = QWidget(self)
        outer = QVBoxLayout(w)

        if empty_msg is not None:
            note = QLabel(empty_msg, w)
            note.setAlignment(Qt.AlignCenter)
            note.setStyleSheet("color: gray; padding: 24px;")
            outer.addWidget(note)
            outer.addStretch(1)
            return w

        for i, group in enumerate(_GROUPS):
            if i > 0:
                sep = QFrame(w)
                sep.setFrameShape(QFrame.HLine)
                sep.setFrameShadow(QFrame.Sunken)
                outer.addWidget(sep)
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight)
            for label, key in group:
                value = _value_for(stats, key)
                v_label = QLabel(value, w)
                v_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addRow(f"{label}:", v_label)
            outer.addLayout(form)

        outer.addStretch(1)
        return w
