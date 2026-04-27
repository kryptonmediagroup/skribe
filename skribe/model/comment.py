"""Inline comments attached to document passages.

A comment anchors to a character range [anchor_start, anchor_end) inside
a single document's QTextDocument. The range drifts naturally as the
user edits; the editor rescans ranges on save to write the current
positions back to each comment.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_uuid() -> str:
    return str(_uuid.uuid4()).upper()


@dataclass
class Comment:
    uuid: str = field(default_factory=_new_uuid)
    author_name: str = ""
    author_initials: str = ""
    created: str = field(default_factory=_now_iso)
    modified: str = field(default_factory=_now_iso)
    body: str = ""
    anchor_start: int = 0
    anchor_end: int = 0
    anchor_text: str = ""  # snippet captured at creation, for display/fallback

    def touch(self) -> None:
        self.modified = _now_iso()

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "author_name": self.author_name,
            "author_initials": self.author_initials,
            "created": self.created,
            "modified": self.modified,
            "body": self.body,
            "anchor_start": int(self.anchor_start),
            "anchor_end": int(self.anchor_end),
            "anchor_text": self.anchor_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Comment":
        return cls(
            uuid=data.get("uuid") or _new_uuid(),
            author_name=data.get("author_name", ""),
            author_initials=data.get("author_initials", ""),
            created=data.get("created", _now_iso()),
            modified=data.get("modified", _now_iso()),
            body=data.get("body", ""),
            anchor_start=int(data.get("anchor_start", 0)),
            anchor_end=int(data.get("anchor_end", 0)),
            anchor_text=data.get("anchor_text", ""),
        )


def comments_to_list(comments: Iterable[Comment]) -> list[dict]:
    return [c.to_dict() for c in comments]


def comments_from_list(raw: Iterable[dict]) -> list[Comment]:
    return [Comment.from_dict(d) for d in raw or []]
