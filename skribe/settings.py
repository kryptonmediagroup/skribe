"""Typed wrapper around QSettings.

One module-level accessor ``app_settings()`` returns a Settings instance that
reads/writes under the shared Skribe/Skribe organization and application
names. Keys are defined as class attributes so typos fail fast.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from PySide6.QtCore import QSettings

ORG = "Skribe"
APP = "Skribe"

MAX_RECENT_DEFAULT = 10


# --- Keys -----------------------------------------------------------------

class Keys:
    # App-wide. NB: do not use "general/" as a prefix — Qt reserves "General"
    # as the implicit root group, and lowercase "general/" silently fails to
    # round-trip (writes land under %General but reads with the lowercase
    # path return the default). Any prefix other than "general" is fine.
    REOPEN_LAST = "app/reopen_last_on_startup"
    LAST_PROJECT = "app/last_project_path"
    MAX_RECENT = "app/max_recent_projects"
    RECENT_PROJECTS = "app/recent_projects"
    AUTHOR_NAME = "app/author_name"
    AUTHOR_INITIALS = "app/author_initials"
    FIRST_RUN_COMPLETE = "app/first_run_complete"

    # Editor
    EDITOR_FONT_FAMILY = "editor/font_family"
    EDITOR_FONT_SIZE = "editor/font_size"
    EDITOR_FIRST_LINE_INDENT_EM = "editor/first_line_indent_em"
    EDITOR_AUTO_INDENT = "editor/auto_indent_new_paragraphs"
    EDITOR_PARAGRAPH_SPACING_LINES = "editor/paragraph_spacing_lines"

    # Appearance
    THEME = "appearance/theme"

    # Spell check
    SPELLCHECK_ENABLED = "spellcheck/enabled"
    SPELLCHECK_LANGUAGE = "spellcheck/language"

    # View
    VIEW_MODE = "view/center_pane_mode"  # "editor" | "corkboard"

    # Window
    MAIN_GEOMETRY = "mainwindow/geometry"
    MAIN_STATE = "mainwindow/state"


# --- Defaults -------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    Keys.REOPEN_LAST: True,
    Keys.LAST_PROJECT: "",
    Keys.MAX_RECENT: MAX_RECENT_DEFAULT,
    Keys.RECENT_PROJECTS: [],
    Keys.AUTHOR_NAME: "",
    Keys.AUTHOR_INITIALS: "",
    Keys.FIRST_RUN_COMPLETE: False,

    Keys.EDITOR_FONT_FAMILY: "Georgia",
    Keys.EDITOR_FONT_SIZE: 12,
    Keys.EDITOR_FIRST_LINE_INDENT_EM: 2.0,
    Keys.EDITOR_AUTO_INDENT: True,
    Keys.EDITOR_PARAGRAPH_SPACING_LINES: 1.0,

    Keys.THEME: "system",

    Keys.SPELLCHECK_ENABLED: True,
    Keys.SPELLCHECK_LANGUAGE: "en_US",

    Keys.VIEW_MODE: "editor",
}


def derive_initials(name: str) -> str:
    """Uppercase first letter of each whitespace-separated word, max 4 chars."""
    parts = [p for p in (name or "").split() if p]
    initials = "".join(p[0] for p in parts)
    return initials.upper()[:4]


def _coerce(key: str, raw: Any) -> Any:
    default = DEFAULTS.get(key)
    if raw is None:
        return default
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(int(raw)) if isinstance(raw, (int, float)) else bool(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        if raw in ("", None):
            return []
        if isinstance(raw, list):
            return raw
        # QSettings may return a single string for a length-1 list.
        return [raw]
    return raw


@dataclass
class Settings:
    _store: QSettings

    def get(self, key: str) -> Any:
        default = DEFAULTS.get(key)
        return _coerce(key, self._store.value(key, default))

    def set(self, key: str, value: Any) -> None:
        self._store.setValue(key, value)

    def sync(self) -> None:
        self._store.sync()

    # --- Recent projects -------------------------------------------------

    def recent_projects(self) -> list[str]:
        vals = self.get(Keys.RECENT_PROJECTS) or []
        return [str(v) for v in vals if v]

    def push_recent_project(self, path: str) -> None:
        items = [p for p in self.recent_projects() if p != path]
        items.insert(0, path)
        limit = int(self.get(Keys.MAX_RECENT) or MAX_RECENT_DEFAULT)
        items = items[:limit]
        self.set(Keys.RECENT_PROJECTS, items)

    def remove_recent_project(self, path: str) -> None:
        items = [p for p in self.recent_projects() if p != path]
        self.set(Keys.RECENT_PROJECTS, items)

    def clear_recent_projects(self) -> None:
        self.set(Keys.RECENT_PROJECTS, [])


_singleton: Optional[Settings] = None


def _migrate_legacy_general(store: QSettings) -> None:
    """One-time migration: values previously saved under the poisoned
    ``general/`` prefix live on disk under ``General/*`` (Qt's implicit
    root group). Copy them over to the new ``app/*`` keys and wipe the
    originals so we don't keep two copies around.
    """
    legacy_prefix = "General/"
    new_prefix = "app/"
    # Keys we previously used under general/.
    suffixes = [
        "reopen_last_on_startup",
        "last_project_path",
        "max_recent_projects",
        "recent_projects",
        "author_name",
        "author_initials",
        "first_run_complete",
    ]
    touched = False
    for s in suffixes:
        old = legacy_prefix + s
        new = new_prefix + s
        val = store.value(old, None)
        if val is None:
            continue
        if store.value(new, None) is None:
            store.setValue(new, val)
        store.remove(old)
        touched = True
    if touched:
        store.sync()


def app_settings() -> Settings:
    global _singleton
    if _singleton is None:
        store = QSettings(ORG, APP)
        _migrate_legacy_general(store)
        _singleton = Settings(store)
    return _singleton
