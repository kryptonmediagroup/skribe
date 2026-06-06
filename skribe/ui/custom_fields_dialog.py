"""Dialog for managing custom metadata field definitions."""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from skribe.model.project import CustomFieldDef, CustomFieldType

_TYPE_LABELS = [
    ("Text", CustomFieldType.TEXT),
    ("Checkbox", CustomFieldType.CHECKBOX),
    ("List", CustomFieldType.LIST),
    ("Date", CustomFieldType.DATE),
]


class CustomFieldsDialog(QDialog):
    """Modal dialog for adding / removing / editing custom metadata fields."""

    fields_changed = Signal(list)

    def __init__(self, field_defs: list[CustomFieldDef], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Metadata Fields")
        self.resize(500, 400)

        self._defs: list[CustomFieldDef] = deepcopy(field_defs)
        self._updating = False  # guard against feedback loops

        # --- left: field list + add/remove ---
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_row_changed)

        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._on_add)
        self._btn_remove = QPushButton("Remove")
        self._btn_remove.clicked.connect(self._on_remove)

        left_btn_layout = QHBoxLayout()
        left_btn_layout.addWidget(btn_add)
        left_btn_layout.addWidget(self._btn_remove)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self._list)
        left_layout.addLayout(left_btn_layout)

        # --- right: editor panel ---
        self._edit_name = QLineEdit()
        self._edit_name.textChanged.connect(self._on_name_changed)

        self._combo_type = QComboBox()
        for label, _ in _TYPE_LABELS:
            self._combo_type.addItem(label)
        self._combo_type.currentIndexChanged.connect(self._on_type_changed)

        self._edit_default = QLineEdit()
        self._edit_default.textChanged.connect(self._on_default_changed)

        self._lbl_choices = QLabel("Choices (one per line):")
        self._edit_choices = QTextEdit()
        self._edit_choices.setAcceptRichText(False)
        self._edit_choices.textChanged.connect(self._on_choices_changed)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Name:"))
        right_layout.addWidget(self._edit_name)
        right_layout.addWidget(QLabel("Type:"))
        right_layout.addWidget(self._combo_type)
        right_layout.addWidget(QLabel("Default Value:"))
        right_layout.addWidget(self._edit_default)
        right_layout.addWidget(self._lbl_choices)
        right_layout.addWidget(self._edit_choices)

        # --- top row: left + right ---
        top_layout = QHBoxLayout()
        top_layout.addLayout(left_layout, 1)
        top_layout.addLayout(right_layout, 2)

        # --- button box ---
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top_layout)
        main_layout.addWidget(btn_box)

        # Populate list
        for d in self._defs:
            self._list.addItem(d.name)

        # Initial state
        self._set_editor_enabled(False)
        if self._defs:
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _current_def(self) -> Optional[CustomFieldDef]:
        row = self._list.currentRow()
        if 0 <= row < len(self._defs):
            return self._defs[row]
        return None

    def _set_editor_enabled(self, enabled: bool) -> None:
        self._edit_name.setEnabled(enabled)
        self._combo_type.setEnabled(enabled)
        self._edit_default.setEnabled(enabled)
        self._btn_remove.setEnabled(enabled)
        choices_on = enabled and self._combo_type.currentIndex() == 2  # LIST
        self._lbl_choices.setEnabled(choices_on)
        self._edit_choices.setEnabled(choices_on)

    def _sync_choices_enabled(self) -> None:
        is_list = self._combo_type.currentIndex() == 2
        self._lbl_choices.setEnabled(is_list)
        self._edit_choices.setEnabled(is_list)

    # ------------------------------------------------------------------
    # slots
    # ------------------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        fd = self._current_def()
        if fd is None:
            self._set_editor_enabled(False)
            return
        self._updating = True
        self._set_editor_enabled(True)
        self._edit_name.setText(fd.name)
        type_idx = next(i for i, (_, t) in enumerate(_TYPE_LABELS) if t == fd.field_type)
        self._combo_type.setCurrentIndex(type_idx)
        self._edit_default.setText(fd.default)
        self._edit_choices.setPlainText("\n".join(fd.choices))
        self._sync_choices_enabled()
        self._updating = False

    def _on_name_changed(self, text: str) -> None:
        if self._updating:
            return
        fd = self._current_def()
        if fd is None:
            return
        fd.name = text
        item = self._list.currentItem()
        if item is not None:
            item.setText(text)

    def _on_type_changed(self, idx: int) -> None:
        if self._updating:
            return
        fd = self._current_def()
        if fd is None:
            return
        fd.field_type = _TYPE_LABELS[idx][1]
        self._sync_choices_enabled()

    def _on_default_changed(self, text: str) -> None:
        if self._updating:
            return
        fd = self._current_def()
        if fd is not None:
            fd.default = text

    def _on_choices_changed(self) -> None:
        if self._updating:
            return
        fd = self._current_def()
        if fd is not None:
            raw = self._edit_choices.toPlainText()
            fd.choices = [c for c in raw.split("\n") if c]

    def _on_add(self) -> None:
        new_def = CustomFieldDef(
            id=str(uuid.uuid4()),
            name="New Field",
        )
        self._defs.append(new_def)
        self._list.addItem(new_def.name)
        self._list.setCurrentRow(len(self._defs) - 1)

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        fd = self._defs[row]
        ans = QMessageBox.question(
            self,
            "Remove Field",
            f'Remove custom field "{fd.name}"?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        del self._defs[row]
        self._list.takeItem(row)

    def _on_ok(self) -> None:
        self.fields_changed.emit(self._defs)
        self.accept()
