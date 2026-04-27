"""Document statistics — pure functions used by the Statistics dialog.

Walks a list of :class:`BinderItem`s, reads each text item's body via a
caller-supplied loader, and produces a :class:`Stats` snapshot. Kept
free of Qt UI imports so it stays unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from PySide6.QtGui import QTextDocument

from skribe.model.project import BinderItem, ItemType

WORDS_PER_PAGE_PAPERBACK = 250
WORDS_PER_MINUTE = 250

_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class Stats:
    words: int = 0
    characters: int = 0
    characters_no_spaces: int = 0
    paragraphs: int = 0
    sentences: int = 0
    documents: int = 0
    longest_doc_words: int = 0
    shortest_doc_words: int = 0
    # Per-doc word counts, kept so we can compute averages without
    # losing precision when caller wants formatted strings later.
    doc_word_counts: list[int] = field(default_factory=list)

    # --- derived metrics ---------------------------------------------

    @property
    def avg_paragraph_words(self) -> int:
        return self.words // self.paragraphs if self.paragraphs else 0

    @property
    def avg_sentence_words(self) -> int:
        return self.words // self.sentences if self.sentences else 0

    @property
    def avg_doc_words(self) -> int:
        return self.words // self.documents if self.documents else 0

    @property
    def pages_paperback(self) -> int:
        # Round up so a half-full page still counts as a page.
        return (self.words + WORDS_PER_PAGE_PAPERBACK - 1) // WORDS_PER_PAGE_PAPERBACK

    def reading_time_hms(self) -> str:
        total_seconds = int(round(self.words / WORDS_PER_MINUTE * 60))
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# --- HTML → plain text ----------------------------------------------

def _html_to_plain(html: str) -> str:
    if not html:
        return ""
    doc = QTextDocument()
    doc.setHtml(html)
    return doc.toPlainText()


# --- text-level counters --------------------------------------------

def _count_words(text: str) -> int:
    if not text:
        return 0
    return len([w for w in _WHITESPACE.split(text.strip()) if w])


def _count_sentences(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    n = len(_SENTENCE_END.findall(text))
    # A trailing fragment without terminal punctuation is still a sentence.
    if text[-1] not in ".!?":
        n += 1
    return max(n, 1)


def _count_paragraphs(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


# --- aggregation -----------------------------------------------------

def _expand(items: Iterable[BinderItem]) -> list[BinderItem]:
    """Yield items + all descendants (folders included), de-duped."""
    seen: set[str] = set()
    out: list[BinderItem] = []
    for top in items:
        for it in top.walk():
            if it.uuid in seen:
                continue
            seen.add(it.uuid)
            out.append(it)
    return out


def compute_stats(
    items: Iterable[BinderItem],
    read_body: Callable[[str], Optional[str]],
) -> Stats:
    """Aggregate stats over ``items`` (and their descendants).

    ``read_body`` takes a text item's UUID and returns its HTML body, or
    None if no body is stored. Folders are walked but contribute no
    text on their own.
    """
    s = Stats()
    for item in _expand(items):
        if item.type is not ItemType.TEXT:
            continue
        html = read_body(item.uuid) or ""
        plain = _html_to_plain(html)
        if not plain.strip():
            continue
        words = _count_words(plain)
        if words == 0:
            continue
        s.words += words
        s.characters += len(plain)
        s.characters_no_spaces += sum(1 for c in plain if not c.isspace())
        s.paragraphs += _count_paragraphs(plain)
        s.sentences += _count_sentences(plain)
        s.documents += 1
        s.doc_word_counts.append(words)

    if s.doc_word_counts:
        s.longest_doc_words = max(s.doc_word_counts)
        s.shortest_doc_words = min(s.doc_word_counts)
    return s


def compiled_items(roots: Iterable[BinderItem]) -> list[BinderItem]:
    """Return every TEXT item under the Draft (Manuscript) folder marked
    Include in Compile, in binder order. Items default to included when
    the metadata flag is missing — matches Scrivener's behavior.
    """
    out: list[BinderItem] = []
    for root in roots:
        if root.type is not ItemType.DRAFT_FOLDER:
            continue
        for it in root.walk():
            if it.type is not ItemType.TEXT:
                continue
            if it.metadata.get("include_in_compile", True):
                out.append(it)
    return out
