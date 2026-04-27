"""Preferences dialog with General / Editor / Appearance tabs."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from skribe.settings import Keys, app_settings, derive_initials
from skribe.spellcheck import available_languages, is_available as spell_is_available
from skribe.themes import THEMES


class PreferencesDialog(QDialog):
    """Modal preferences. Emits ``applied`` when settings change."""

    applied = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(460)
        self._settings = app_settings()

        tabs = QTabWidget(self)
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_editor_tab(), "Editor")
        tabs.addTab(self._build_appearance_tab(), "Appearance")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply,
            parent=self,
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self._load_values()

    # --- tabs ---

    def _build_general_tab(self) -> QWidget:
        w = QWidget(self)
        form = QFormLayout(w)

        self._author_name = QLineEdit(w)
        self._author_name.setPlaceholderText("Jane Q. Writer")
        form.addRow("Author name:", self._author_name)

        self._author_initials = QLineEdit(w)
        self._author_initials.setPlaceholderText("JQW")
        self._author_initials.setMaxLength(6)
        form.addRow("Initials (for comments):", self._author_initials)

        # Auto-derive initials from name until the user edits the field directly.
        self._initials_touched = False
        self._author_initials.textEdited.connect(self._on_initials_edited)
        self._author_name.textChanged.connect(self._on_name_changed)

        self._reopen_last = QCheckBox("Reopen last project on startup", w)
        form.addRow(self._reopen_last)

        self._max_recent = QSpinBox(w)
        self._max_recent.setRange(0, 50)
        form.addRow("Max recent projects:", self._max_recent)
        return w

    def _on_initials_edited(self, _text: str) -> None:
        self._initials_touched = True

    def _on_name_changed(self, text: str) -> None:
        if not self._initials_touched:
            self._author_initials.blockSignals(True)
            self._author_initials.setText(derive_initials(text))
            self._author_initials.blockSignals(False)

    def _build_editor_tab(self) -> QWidget:
        w = QWidget(self)
        form = QFormLayout(w)

        self._font_family = QFontComboBox(w)
        form.addRow("Default font:", self._font_family)

        self._font_size = QSpinBox(w)
        self._font_size.setRange(6, 72)
        form.addRow("Default size:", self._font_size)

        self._indent_em = QDoubleSpinBox(w)
        self._indent_em.setRange(0.0, 10.0)
        self._indent_em.setSingleStep(0.25)
        self._indent_em.setDecimals(2)
        self._indent_em.setSuffix(" em")
        form.addRow("First-line indent:", self._indent_em)

        self._paragraph_spacing = QDoubleSpinBox(w)
        self._paragraph_spacing.setRange(0.0, 5.0)
        self._paragraph_spacing.setSingleStep(0.25)
        self._paragraph_spacing.setDecimals(2)
        self._paragraph_spacing.setSuffix(" lines")
        form.addRow("Paragraph spacing:", self._paragraph_spacing)

        self._auto_indent = QCheckBox("Auto-indent new paragraphs", w)
        form.addRow(self._auto_indent)

        self._spell_enabled = QCheckBox("Check spelling while typing", w)
        self._spell_lang = QComboBox(w)
        if spell_is_available():
            for lang in available_languages():
                self._spell_lang.addItem(lang, userData=lang)
        else:
            self._spell_enabled.setEnabled(False)
            self._spell_lang.setEnabled(False)
            self._spell_lang.addItem("(no dictionaries installed)")
        form.addRow(self._spell_enabled)
        form.addRow("Spelling language:", self._spell_lang)
        return w

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget(self)
        form = QFormLayout(w)
        self._theme = QComboBox(w)
        for key, theme in THEMES.items():
            self._theme.addItem(theme.label, userData=key)
        form.addRow("Color scheme:", self._theme)
        return w

    # --- load/save ---

    def _load_values(self) -> None:
        s = self._settings
        name = str(s.get(Keys.AUTHOR_NAME) or "")
        initials = str(s.get(Keys.AUTHOR_INITIALS) or "")
        self._author_name.setText(name)
        self._author_initials.setText(initials)
        # If an initials value was saved and differs from a pure derivation,
        # treat it as manually set so we don't clobber it as the user types.
        self._initials_touched = bool(initials) and initials != derive_initials(name)

        self._reopen_last.setChecked(bool(s.get(Keys.REOPEN_LAST)))
        self._max_recent.setValue(int(s.get(Keys.MAX_RECENT)))

        fam = str(s.get(Keys.EDITOR_FONT_FAMILY))
        self._font_family.setCurrentFont(QFont(fam))
        self._font_size.setValue(int(s.get(Keys.EDITOR_FONT_SIZE)))
        self._indent_em.setValue(float(s.get(Keys.EDITOR_FIRST_LINE_INDENT_EM)))
        self._paragraph_spacing.setValue(float(s.get(Keys.EDITOR_PARAGRAPH_SPACING_LINES)))
        self._auto_indent.setChecked(bool(s.get(Keys.EDITOR_AUTO_INDENT)))

        theme_key = str(s.get(Keys.THEME))
        idx = self._theme.findData(theme_key)
        self._theme.setCurrentIndex(idx if idx >= 0 else 0)

        self._spell_enabled.setChecked(bool(s.get(Keys.SPELLCHECK_ENABLED)))
        if spell_is_available():
            cur_lang = str(s.get(Keys.SPELLCHECK_LANGUAGE)) or "en_US"
            idx = self._spell_lang.findData(cur_lang)
            if idx < 0:
                idx = self._spell_lang.findData("en_US")
            self._spell_lang.setCurrentIndex(max(idx, 0))

    def _apply(self) -> None:
        s = self._settings
        name = self._author_name.text().strip()
        initials = self._author_initials.text().strip().upper()
        if not initials:
            initials = derive_initials(name)
        s.set(Keys.AUTHOR_NAME, name)
        s.set(Keys.AUTHOR_INITIALS, initials)

        s.set(Keys.REOPEN_LAST, self._reopen_last.isChecked())
        s.set(Keys.MAX_RECENT, int(self._max_recent.value()))

        s.set(Keys.EDITOR_FONT_FAMILY, self._font_family.currentFont().family())
        s.set(Keys.EDITOR_FONT_SIZE, int(self._font_size.value()))
        s.set(Keys.EDITOR_FIRST_LINE_INDENT_EM, float(self._indent_em.value()))
        s.set(Keys.EDITOR_PARAGRAPH_SPACING_LINES, float(self._paragraph_spacing.value()))
        s.set(Keys.EDITOR_AUTO_INDENT, self._auto_indent.isChecked())

        s.set(Keys.THEME, self._theme.currentData())

        s.set(Keys.SPELLCHECK_ENABLED, self._spell_enabled.isChecked())
        if spell_is_available() and self._spell_lang.currentData():
            s.set(Keys.SPELLCHECK_LANGUAGE, self._spell_lang.currentData())

        s.sync()
        self.applied.emit()

    def _on_ok(self) -> None:
        self._apply()
        self.accept()
