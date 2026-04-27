"""Inspector panel (right side). MVP: synopsis + read-only metadata.

Later iterations will add label/status pickers, keywords, custom metadata,
and per-document notes.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from skribe.model.project import BinderItem


class InspectorWidget(QWidget):
    synopsis_changed = Signal(str)
    include_in_compile_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._title = QLabel("No document selected", self)
        f = self._title.font(); f.setBold(True); self._title.setFont(f)
        layout.addWidget(self._title)

        self._type_label = QLabel("", self)
        self._type_label.setStyleSheet("color: gray;")
        layout.addWidget(self._type_label)

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)

        self._synopsis = QPlainTextEdit(self)
        self._synopsis.setPlaceholderText("Synopsis (index-card text)")
        self._synopsis.setMaximumHeight(160)
        self._synopsis.textChanged.connect(
            lambda: self.synopsis_changed.emit(self._synopsis.toPlainText())
        )
        form.addRow(QLabel("Synopsis:"), self._synopsis)

        self._include = QCheckBox("Include in compile", self)
        self._include.toggled.connect(self.include_in_compile_changed.emit)
        form.addRow(self._include)

        layout.addLayout(form)
        layout.addStretch(1)

        self._current: Optional[BinderItem] = None
        self.set_item(None)

    def set_item(self, item: Optional[BinderItem]) -> None:
        self._current = None  # suppress signals during repopulate
        self._synopsis.blockSignals(True)
        self._include.blockSignals(True)

        if item is None:
            self._title.setText("No document selected")
            self._type_label.setText("")
            self._synopsis.clear()
            self._synopsis.setEnabled(False)
            self._include.setChecked(False)
            self._include.setEnabled(False)
        else:
            self._title.setText(item.title or "(untitled)")
            self._type_label.setText(item.type.value)
            self._synopsis.setPlainText(item.synopsis or "")
            self._synopsis.setEnabled(True)
            self._include.setChecked(bool(item.metadata.get("include_in_compile", True)))
            self._include.setEnabled(True)

        self._synopsis.blockSignals(False)
        self._include.blockSignals(False)
        self._current = item
