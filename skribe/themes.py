"""Color-scheme definitions + application helpers.

A theme is a small record of Qt palette colors plus editor-specific page
colors. ``apply_theme`` updates the QApplication palette; ``editor_palette``
produces a palette for a QTextEdit-style widget that wants a distinct page
background.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    # QPalette roles
    window: str
    window_text: str
    base: str
    alternate_base: str
    text: str
    button: str
    button_text: str
    highlight: str
    highlight_text: str
    tooltip_base: str
    tooltip_text: str
    # Editor page
    page_bg: str
    page_text: str
    page_selection_bg: str
    page_selection_text: str


SYSTEM = Theme(
    key="system",
    label="System",
    window="", window_text="", base="", alternate_base="",
    text="", button="", button_text="",
    highlight="", highlight_text="",
    tooltip_base="", tooltip_text="",
    page_bg="", page_text="",
    page_selection_bg="", page_selection_text="",
)

LIGHT = Theme(
    key="light", label="Light",
    window="#f3f3f3", window_text="#1a1a1a",
    base="#ffffff", alternate_base="#f7f7f7",
    text="#1a1a1a",
    button="#ececec", button_text="#1a1a1a",
    highlight="#3b6fb6", highlight_text="#ffffff",
    tooltip_base="#ffffe0", tooltip_text="#1a1a1a",
    page_bg="#ffffff", page_text="#1a1a1a",
    page_selection_bg="#3b6fb6", page_selection_text="#ffffff",
)

DARK = Theme(
    key="dark", label="Dark",
    window="#1f1f21", window_text="#e7e7ea",
    base="#2a2a2d", alternate_base="#333337",
    text="#e7e7ea",
    button="#3a3a3e", button_text="#e7e7ea",
    highlight="#4a7cc9", highlight_text="#ffffff",
    tooltip_base="#2a2a2d", tooltip_text="#e7e7ea",
    page_bg="#262628", page_text="#e6e6e6",
    page_selection_bg="#4a7cc9", page_selection_text="#ffffff",
)

SEPIA = Theme(
    key="sepia", label="Sepia",
    window="#ede3cf", window_text="#3b2f1e",
    base="#faf1dc", alternate_base="#f2e9d0",
    text="#3b2f1e",
    button="#e5d9bc", button_text="#3b2f1e",
    highlight="#a8794a", highlight_text="#fff7e2",
    tooltip_base="#fff7e2", tooltip_text="#3b2f1e",
    page_bg="#faf1dc", page_text="#3b2f1e",
    page_selection_bg="#a8794a", page_selection_text="#fff7e2",
)

SOLARIZED_DARK = Theme(
    key="solarized_dark", label="Solarized Dark",
    window="#002b36", window_text="#eee8d5",
    base="#073642", alternate_base="#0a4352",
    text="#eee8d5",
    button="#0a4352", button_text="#eee8d5",
    highlight="#268bd2", highlight_text="#fdf6e3",
    tooltip_base="#073642", tooltip_text="#eee8d5",
    page_bg="#002b36", page_text="#eee8d5",
    page_selection_bg="#268bd2", page_selection_text="#fdf6e3",
)

THEMES: dict[str, Theme] = {
    SYSTEM.key: SYSTEM,
    LIGHT.key: LIGHT,
    DARK.key: DARK,
    SEPIA.key: SEPIA,
    SOLARIZED_DARK.key: SOLARIZED_DARK,
}


def theme_for(key: Optional[str]) -> Theme:
    return THEMES.get((key or "system").lower(), SYSTEM)


def _palette_from(theme: Theme) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(theme.window))
    p.setColor(QPalette.WindowText, QColor(theme.window_text))
    p.setColor(QPalette.Base, QColor(theme.base))
    p.setColor(QPalette.AlternateBase, QColor(theme.alternate_base))
    p.setColor(QPalette.Text, QColor(theme.text))
    p.setColor(QPalette.Button, QColor(theme.button))
    p.setColor(QPalette.ButtonText, QColor(theme.button_text))
    p.setColor(QPalette.Highlight, QColor(theme.highlight))
    p.setColor(QPalette.HighlightedText, QColor(theme.highlight_text))
    p.setColor(QPalette.ToolTipBase, QColor(theme.tooltip_base))
    p.setColor(QPalette.ToolTipText, QColor(theme.tooltip_text))
    return p


def apply_theme(theme: Theme) -> None:
    """Apply a theme to the global QApplication palette.

    The System theme restores Qt's default (from current style/OS).
    """
    app = QApplication.instance()
    if app is None:
        return
    if theme.key == SYSTEM.key:
        app.setPalette(app.style().standardPalette())
        return
    app.setPalette(_palette_from(theme))


def editor_palette(theme: Theme, base_palette: Optional[QPalette] = None) -> QPalette:
    """Return a QPalette for a QTextEdit honoring the theme's page colors."""
    p = QPalette(base_palette) if base_palette is not None else QPalette()
    if theme.key == SYSTEM.key:
        return p
    p.setColor(QPalette.Base, QColor(theme.page_bg))
    p.setColor(QPalette.Text, QColor(theme.page_text))
    p.setColor(QPalette.Highlight, QColor(theme.page_selection_bg))
    p.setColor(QPalette.HighlightedText, QColor(theme.page_selection_text))
    return p
