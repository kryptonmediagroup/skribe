"""Right-rail comments panel — one editable card per Comment.

The panel is passive: it receives a list of comments via :meth:`set_comments`,
renders one :class:`_CommentCard` per entry, and emits signals when the user
edits a body, deletes a card, or selects a card (so the editor can jump to
the anchor). It does not persist or touch the model directly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from skribe.model.comment import Comment

_CARD_SELECTED_CSS = (
    "QFrame#CommentCard { border: 2px solid #E0A800; border-radius: 6px;"
    " background: #FFF59D; padding: 4px; }"
)
_CARD_DEFAULT_CSS = (
    "QFrame#CommentCard { border: 1px solid #E0C97A; border-radius: 6px;"
    " background: #FFFBEA; padding: 4px; }"
)


def _format_created(iso: str) -> str:
    """Compact display format for the card header; falls back to raw string."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%Y-%m-%d %H:%M")


class _CommentCard(QFrame):
    body_changed = Signal(str, str)     # (comment_id, body)
    delete_requested = Signal(str)      # comment_id
    selected = Signal(str)              # comment_id (user clicked the card)

    def __init__(self, comment: Comment, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CommentCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(_CARD_DEFAULT_CSS)
        self._comment_id = comment.uuid

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        initials = (comment.author_initials or "?").upper()
        name = comment.author_name or ""
        tip = f"{name} ({initials})" if name else initials
        self._initials_label = QLabel(initials, self)
        f = self._initials_label.font(); f.setBold(True); self._initials_label.setFont(f)
        self._initials_label.setToolTip(tip)
        self._initials_label.setStyleSheet(
            "background: #FFF59D; color: #444; padding: 1px 6px; border-radius: 8px;"
        )
        header.addWidget(self._initials_label, 0, Qt.AlignLeft)

        self._date_label = QLabel(_format_created(comment.created), self)
        self._date_label.setStyleSheet("color: gray;")
        self._date_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        header.addWidget(self._date_label, 1)

        self._delete_btn = QPushButton("×", self)
        self._delete_btn.setFlat(True)
        self._delete_btn.setFixedWidth(22)
        self._delete_btn.setToolTip("Delete this comment")
        self._delete_btn.clicked.connect(lambda: self.delete_requested.emit(self._comment_id))
        header.addWidget(self._delete_btn, 0, Qt.AlignRight)
        layout.addLayout(header)

        if comment.anchor_text:
            snippet = comment.anchor_text.replace(" ", " ").strip()
            if len(snippet) > 90:
                snippet = snippet[:87] + "…"
            self._snippet = QLabel(f"“{snippet}”", self)
            self._snippet.setWordWrap(True)
            self._snippet.setStyleSheet("color: #777; font-style: italic;")
            layout.addWidget(self._snippet)

        self._body = QPlainTextEdit(self)
        self._body.setPlaceholderText("Comment…")
        self._body.setPlainText(comment.body or "")
        self._body.setFixedHeight(80)
        self._body.textChanged.connect(
            lambda: self.body_changed.emit(self._comment_id, self._body.toPlainText())
        )
        layout.addWidget(self._body)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def comment_id(self) -> str:
        return self._comment_id

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(_CARD_SELECTED_CSS if selected else _CARD_DEFAULT_CSS)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.selected.emit(self._comment_id)
        super().mousePressEvent(event)


class CommentsPanel(QWidget):
    """Scrollable list of comment cards for the current document."""

    comment_body_changed = Signal(str, str)
    comment_delete_requested = Signal(str)
    comment_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cards: dict[str, _CommentCard] = {}
        self._active_id: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QLabel("Comments", self)
        f = header.font(); f.setBold(True); header.setFont(f)
        header.setContentsMargins(8, 6, 8, 4)
        outer.addWidget(header)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self._scroll, 1)

        self._container = QWidget(self._scroll)
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(8, 4, 8, 8)
        self._container_layout.setSpacing(6)
        self._container_layout.addStretch(1)
        self._scroll.setWidget(self._container)

        self._empty_label = QLabel("No comments on this document.", self._container)
        self._empty_label.setStyleSheet("color: gray; font-style: italic;")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._container_layout.insertWidget(0, self._empty_label)

    # --- public API ---

    def set_comments(self, comments: Iterable[Comment]) -> None:
        # Drop existing cards.
        for card in list(self._cards.values()):
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._active_id = None
        # Insert new cards above the trailing stretch (index 0 = empty label,
        # 1..N = cards, last = stretch). Order by created ASC for stability.
        comments_sorted = sorted(comments, key=lambda c: c.created)
        for c in comments_sorted:
            card = _CommentCard(c, self._container)
            card.body_changed.connect(self.comment_body_changed.emit)
            card.delete_requested.connect(self._on_delete_requested)
            card.selected.connect(self._on_selected)
            # Insert above the stretch, below any existing cards.
            insert_at = self._container_layout.count() - 1
            self._container_layout.insertWidget(insert_at, card)
            self._cards[c.uuid] = card
        self._empty_label.setVisible(not self._cards)

    def add_comment(self, comment: Comment) -> None:
        if comment.uuid in self._cards:
            return
        card = _CommentCard(comment, self._container)
        card.body_changed.connect(self.comment_body_changed.emit)
        card.delete_requested.connect(self._on_delete_requested)
        card.selected.connect(self._on_selected)
        insert_at = self._container_layout.count() - 1
        self._container_layout.insertWidget(insert_at, card)
        self._cards[comment.uuid] = card
        self._empty_label.setVisible(False)
        self.highlight_comment(comment.uuid)

    def remove_comment(self, comment_id: str) -> None:
        card = self._cards.pop(comment_id, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        if self._active_id == comment_id:
            self._active_id = None
        self._empty_label.setVisible(not self._cards)

    def highlight_comment(self, comment_id: str) -> None:
        if comment_id == self._active_id:
            return
        if self._active_id and self._active_id in self._cards:
            self._cards[self._active_id].set_selected(False)
        card = self._cards.get(comment_id)
        if card is not None:
            card.set_selected(True)
            self._scroll.ensureWidgetVisible(card, 0, 8)
        self._active_id = comment_id if card is not None else None

    # --- slots ---

    def _on_delete_requested(self, comment_id: str) -> None:
        self.comment_delete_requested.emit(comment_id)

    def _on_selected(self, comment_id: str) -> None:
        self.highlight_comment(comment_id)
        self.comment_selected.emit(comment_id)
