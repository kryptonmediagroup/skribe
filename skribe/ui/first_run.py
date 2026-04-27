"""First-run setup dialog.

Shown once on first launch (or when ``Keys.FIRST_RUN_COMPLETE`` is False).
Surfaces every user-tunable default that appears in the Preferences dialog
so the user can make their initial choices without hunting for them. The
two UIs must stay in sync — see ``feedback_settings_parity``.

The dialog always marks first-run as complete when it closes, even if the
user dismisses it: defaults are already in place, and we don't want to
nag on subsequent launches.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from skribe.settings import Keys, app_settings, derive_initials
from skribe.spellcheck import available_languages, is_available as spell_is_available
from skribe.themes import THEMES


class FirstRunDialog(QDialog):
    """One-page welcome dialog grouping every Preferences default."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Skribe")
        self.setMinimumWidth(520)
        self._settings = app_settings()

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Welcome! Let's set a few defaults — you can change any of these "
            "later from <b>Edit → Preferences</b>.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(self._build_identity_group())
        layout.addWidget(self._build_editor_group())
        layout.addWidget(self._build_appearance_group())
        layout.addWidget(self._build_general_group())

        buttons = QDialogButtonBox(self)
        skip_btn = QPushButton("Use Defaults", self)
        accept_btn = QPushButton("Get Started", self)
        accept_btn.setDefault(True)
        buttons.addButton(skip_btn, QDialogButtonBox.RejectRole)
        buttons.addButton(accept_btn, QDialogButtonBox.AcceptRole)
        skip_btn.clicked.connect(self.reject)
        accept_btn.clicked.connect(self._on_accept)
        layout.addWidget(buttons)

        self._load_values()

    # --- groups ---

    def _build_identity_group(self) -> QGroupBox:
        g = QGroupBox("About You", self)
        form = QFormLayout(g)

        self._author_name = QLineEdit(g)
        self._author_name.setPlaceholderText("Jane Q. Writer")
        form.addRow("Name:", self._author_name)

        self._author_initials = QLineEdit(g)
        self._author_initials.setPlaceholderText("JQW")
        self._author_initials.setMaxLength(6)
        form.addRow("Initials (for comments):", self._author_initials)

        hint = QLabel(
            "Your initials appear on each comment card. If you leave them blank, "
            "we'll derive them from your name.",
            g,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        form.addRow(hint)

        self._initials_touched = False
        self._author_initials.textEdited.connect(lambda _t: self._mark_initials_touched())
        self._author_name.textChanged.connect(self._on_name_changed)
        return g

    def _build_editor_group(self) -> QGroupBox:
        g = QGroupBox("Editor", self)
        form = QFormLayout(g)

        self._font_family = QFontComboBox(g)
        form.addRow("Default font:", self._font_family)

        self._font_size = QSpinBox(g)
        self._font_size.setRange(6, 72)
        form.addRow("Default size:", self._font_size)

        self._indent_em = QDoubleSpinBox(g)
        self._indent_em.setRange(0.0, 10.0)
        self._indent_em.setSingleStep(0.25)
        self._indent_em.setDecimals(2)
        self._indent_em.setSuffix(" em")
        form.addRow("First-line indent:", self._indent_em)

        self._paragraph_spacing = QDoubleSpinBox(g)
        self._paragraph_spacing.setRange(0.0, 5.0)
        self._paragraph_spacing.setSingleStep(0.25)
        self._paragraph_spacing.setDecimals(2)
        self._paragraph_spacing.setSuffix(" lines")
        form.addRow("Paragraph spacing:", self._paragraph_spacing)

        self._auto_indent = QCheckBox("Auto-indent new paragraphs", g)
        form.addRow(self._auto_indent)

        self._spell_enabled = QCheckBox("Check spelling while typing", g)
        self._spell_lang = QComboBox(g)
        if spell_is_available():
            for lang in available_languages():
                self._spell_lang.addItem(lang, userData=lang)
        else:
            self._spell_enabled.setEnabled(False)
            self._spell_lang.setEnabled(False)
            self._spell_lang.addItem("(no dictionaries installed)")
        form.addRow(self._spell_enabled)
        form.addRow("Spelling language:", self._spell_lang)
        return g

    def _build_appearance_group(self) -> QGroupBox:
        g = QGroupBox("Appearance", self)
        form = QFormLayout(g)
        self._theme = QComboBox(g)
        for key, theme in THEMES.items():
            self._theme.addItem(theme.label, userData=key)
        form.addRow("Color scheme:", self._theme)
        return g

    def _build_general_group(self) -> QGroupBox:
        g = QGroupBox("General", self)
        form = QFormLayout(g)

        self._reopen_last = QCheckBox("Reopen last project on startup", g)
        form.addRow(self._reopen_last)

        self._max_recent = QSpinBox(g)
        self._max_recent.setRange(0, 50)
        form.addRow("Max recent projects:", self._max_recent)
        return g

    # --- name/initials linkage ---

    def _mark_initials_touched(self) -> None:
        self._initials_touched = True

    def _on_name_changed(self, text: str) -> None:
        if not self._initials_touched:
            self._author_initials.blockSignals(True)
            self._author_initials.setText(derive_initials(text))
            self._author_initials.blockSignals(False)

    # --- load/save ---

    def _load_values(self) -> None:
        s = self._settings
        name = str(s.get(Keys.AUTHOR_NAME) or "")
        initials = str(s.get(Keys.AUTHOR_INITIALS) or "")
        self._author_name.setText(name)
        self._author_initials.setText(initials)
        self._initials_touched = bool(initials) and initials != derive_initials(name)

        fam = str(s.get(Keys.EDITOR_FONT_FAMILY))
        self._font_family.setCurrentFont(QFont(fam))
        self._font_size.setValue(int(s.get(Keys.EDITOR_FONT_SIZE)))
        self._indent_em.setValue(float(s.get(Keys.EDITOR_FIRST_LINE_INDENT_EM)))
        self._paragraph_spacing.setValue(float(s.get(Keys.EDITOR_PARAGRAPH_SPACING_LINES)))
        self._auto_indent.setChecked(bool(s.get(Keys.EDITOR_AUTO_INDENT)))

        theme_key = str(s.get(Keys.THEME))
        idx = self._theme.findData(theme_key)
        self._theme.setCurrentIndex(idx if idx >= 0 else 0)

        self._reopen_last.setChecked(bool(s.get(Keys.REOPEN_LAST)))
        self._max_recent.setValue(int(s.get(Keys.MAX_RECENT)))

        self._spell_enabled.setChecked(bool(s.get(Keys.SPELLCHECK_ENABLED)))
        if spell_is_available():
            cur_lang = str(s.get(Keys.SPELLCHECK_LANGUAGE)) or "en_US"
            idx = self._spell_lang.findData(cur_lang)
            if idx < 0:
                idx = self._spell_lang.findData("en_US")
            self._spell_lang.setCurrentIndex(max(idx, 0))

    def _on_accept(self) -> None:
        s = self._settings
        name = self._author_name.text().strip()
        initials = self._author_initials.text().strip().upper()
        if not initials:
            initials = derive_initials(name)
        s.set(Keys.AUTHOR_NAME, name)
        s.set(Keys.AUTHOR_INITIALS, initials)

        s.set(Keys.EDITOR_FONT_FAMILY, self._font_family.currentFont().family())
        s.set(Keys.EDITOR_FONT_SIZE, int(self._font_size.value()))
        s.set(Keys.EDITOR_FIRST_LINE_INDENT_EM, float(self._indent_em.value()))
        s.set(Keys.EDITOR_PARAGRAPH_SPACING_LINES, float(self._paragraph_spacing.value()))
        s.set(Keys.EDITOR_AUTO_INDENT, self._auto_indent.isChecked())

        s.set(Keys.THEME, self._theme.currentData())

        s.set(Keys.REOPEN_LAST, self._reopen_last.isChecked())
        s.set(Keys.MAX_RECENT, int(self._max_recent.value()))

        s.set(Keys.SPELLCHECK_ENABLED, self._spell_enabled.isChecked())
        if spell_is_available() and self._spell_lang.currentData():
            s.set(Keys.SPELLCHECK_LANGUAGE, self._spell_lang.currentData())
        self.accept()

    # Any exit path (Accept, Skip, close-box) flips the completion flag so
    # we don't nag on the next launch.
    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._mark_complete()
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        self._mark_complete()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._mark_complete()
        super().reject()

    def _mark_complete(self) -> None:
        s = self._settings
        s.set(Keys.FIRST_RUN_COMPLETE, True)
        s.sync()


def maybe_run_first_run(parent: Optional[QWidget] = None) -> bool:
    """Show the dialog if needed. Returns True if it ran."""
    s = app_settings()
    if bool(s.get(Keys.FIRST_RUN_COMPLETE)):
        return False
    FirstRunDialog(parent).exec()
    return True
