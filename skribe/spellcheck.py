"""Spell-check service: thin wrapper around pyenchant + a personal word list.

Backed by hunspell on Linux/Pi/Mac and the native Windows speller via
``pyenchant``. The service exposes a tiny surface — ``check`` /
``suggest`` / ``add_to_personal`` / ``ignore_session`` — that the
QSyntaxHighlighter and the editor's right-click menu both call into.

Personal dictionary lives at ``<AppConfigLocation>/personal_dict.txt`` —
one word per line, UTF-8. We share it across all languages: a writer's
proper nouns (character names, place names, neologisms) are language-
agnostic, and matching Scrivener's behavior here means people don't have
to retrain the dictionary when they switch locales.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QStandardPaths

log = logging.getLogger(__name__)

try:
    import enchant  # type: ignore[import-untyped]
    _ENCHANT_OK = True
except Exception as exc:  # pragma: no cover — only hit on systems without hunspell
    enchant = None  # type: ignore[assignment]
    _ENCHANT_OK = False
    log.warning("pyenchant unavailable, spell-check disabled: %s", exc)


def is_available() -> bool:
    """True if pyenchant + at least one dictionary loaded successfully."""
    if not _ENCHANT_OK:
        return False
    try:
        return bool(enchant.list_languages())
    except Exception:
        return False


def available_languages() -> list[str]:
    """Sorted list of language tags with installed dictionaries (e.g. ``en_US``)."""
    if not _ENCHANT_OK:
        return []
    try:
        return sorted(enchant.list_languages())
    except Exception:
        return []


def _personal_dict_path() -> Path:
    """Cross-platform user-writable location for the personal dictionary."""
    base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    if not base:
        base = str(Path.home() / ".config" / "Skribe")
    return Path(base) / "personal_dict.txt"


class SpellChecker:
    """Stateful checker for one language plus user word lists.

    ``ignored`` words last only for the lifetime of this instance —
    matches Scrivener's "Ignore" behavior, which is per-session, not
    persisted. ``personal`` words are persisted to disk and reloaded on
    next launch.
    """

    def __init__(self, language: str = "en_US"):
        self._language = language
        self._dict: Optional[object] = None
        self._personal: set[str] = set()
        self._ignored: set[str] = set()
        self._load_personal()
        self._open_dictionary()

    # --- dictionary lifecycle ---------------------------------------

    def _open_dictionary(self) -> None:
        if not _ENCHANT_OK:
            self._dict = None
            return
        try:
            self._dict = enchant.Dict(self._language)
        except Exception as exc:
            log.warning("spell: failed to open dictionary %s: %s", self._language, exc)
            # Fall back to whatever the system thinks is the default.
            try:
                self._dict = enchant.Dict()
            except Exception:
                self._dict = None

    def set_language(self, language: str) -> None:
        if language == self._language:
            return
        self._language = language
        self._open_dictionary()

    @property
    def language(self) -> str:
        return self._language

    @property
    def is_ready(self) -> bool:
        return self._dict is not None

    # --- core check / suggest ---------------------------------------

    def check(self, word: str) -> bool:
        """True if ``word`` is in the dictionary, personal list, or ignore list."""
        if not word or self._dict is None:
            return True
        if word in self._ignored or word in self._personal:
            return True
        # enchant treats numbers as misspellings; spare the user the noise.
        if word.isdigit():
            return True
        try:
            return bool(self._dict.check(word))
        except Exception:
            return True

    def suggest(self, word: str, limit: int = 7) -> list[str]:
        if not word or self._dict is None:
            return []
        try:
            return list(self._dict.suggest(word))[:limit]
        except Exception:
            return []

    # --- user word lists --------------------------------------------

    def add_to_personal(self, word: str) -> None:
        word = word.strip()
        if not word or word in self._personal:
            return
        self._personal.add(word)
        self._save_personal()

    def ignore_session(self, word: str) -> None:
        word = word.strip()
        if word:
            self._ignored.add(word)

    def personal_words(self) -> list[str]:
        return sorted(self._personal)

    def _load_personal(self) -> None:
        path = _personal_dict_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("spell: could not read personal dictionary %s: %s", path, exc)
            return
        for line in text.splitlines():
            w = line.strip()
            if w:
                self._personal.add(w)

    def _save_personal(self) -> None:
        path = _personal_dict_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(sorted(self._personal)) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("spell: could not write personal dictionary %s: %s", path, exc)
