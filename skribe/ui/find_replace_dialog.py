"""Find/Replace dialog with project-wide and current document modes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


@dataclass
class FindReplaceState:
    """Current state of find/replace operations."""
    find_text: str
    replace_text: str
    search_project: bool
    whole_word: bool = False
    current_index: int = 0
    total_matches: int = 0


class FindReplaceDialog(QDialog):
    """Modal find/replace dialog with navigation and scope options."""

    find_next = Signal(str, bool, bool)  # find_text, search_forward, whole_word
    replace_one = Signal(str, str, bool)  # find_text, replace_text, whole_word
    replace_all = Signal(str, str, bool, bool)  # find_text, replace_text, search_project, whole_word
    navigate_to_doc = Signal(str)  # uuid of document to navigate to

    def __init__(
        self,
        get_current_text: Callable[[], str],
        search_project: Callable[[str, bool], list[tuple[str, str, int]]],
        parent=None,
    ):
        super().__init__(parent)
        self._get_current_text = get_current_text
        self._search_project = search_project
        self._state = FindReplaceState(find_text="", replace_text="", search_project=False)
        self._matches: list[tuple[str, str, int]] = []

        self.setWindowTitle("Find and Replace")
        self.setModal(False)
        self.setMinimumWidth(400)
        self.resize(450, 200)

        self._find_edit = QLineEdit(self)
        self._find_edit.setPlaceholderText("Find...")
        self._find_edit.textChanged.connect(self._on_find_text_changed)

        self._replace_edit = QLineEdit(self)
        self._replace_edit.setPlaceholderText("Replace with...")

        self._project_check = QCheckBox("Search entire project", self)
        self._project_check.stateChanged.connect(self._on_scope_changed)

        self._whole_word_check = QCheckBox("Match whole words", self)
        self._whole_word_check.stateChanged.connect(self._on_whole_word_changed)

        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: gray;")

        self._btn_next = QPushButton("Next", self)
        self._btn_next.clicked.connect(self._on_next)
        self._btn_prev = QPushButton("Back", self)
        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_replace = QPushButton("Replace", self)
        self._btn_replace.clicked.connect(self._on_replace)
        self._btn_replace_all = QPushButton("Replace All", self)
        self._btn_replace_all.clicked.connect(self._on_replace_all)
        self._btn_close = QPushButton("Close", self)
        self._btn_close.clicked.connect(self.close)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(self._btn_prev)
        nav_layout.addWidget(self._btn_next)

        action_layout = QHBoxLayout()
        action_layout.addWidget(self._btn_replace)
        action_layout.addWidget(self._btn_replace_all)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(QLabel("Find:"))
        main_layout.addWidget(self._find_edit)
        main_layout.addWidget(QLabel("Replace with:"))
        main_layout.addWidget(self._replace_edit)
        main_layout.addWidget(self._project_check)
        main_layout.addWidget(self._whole_word_check)
        main_layout.addWidget(self._status_label)
        main_layout.addLayout(nav_layout)
        main_layout.addLayout(action_layout)
        main_layout.addWidget(self._btn_close)

        self._find_edit.installEventFilter(self)
        self._replace_edit.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == 6:  # KeyPress
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if obj == self._find_edit:
                    self._on_next()
                    return True
                elif obj == self._replace_edit:
                    self._on_replace()
                    return True
        return super().eventFilter(obj, event)

    def _on_find_text_changed(self, text: str) -> None:
        self._state.find_text = text
        self._perform_search()

    def _on_scope_changed(self, _state: int) -> None:
        self._state.search_project = self._project_check.isChecked()
        self._perform_search()

    def _on_whole_word_changed(self, _state: int) -> None:
        self._state.whole_word = self._whole_word_check.isChecked()
        self._perform_search()

    def _perform_search(self) -> None:
        find_text = self._find_edit.text()
        if not find_text:
            self._status_label.setText("")
            self._update_button_states()
            return

        if self._project_check.isChecked():
            self._matches = self._search_project(find_text, False)
            self._state.total_matches = len(self._matches)
            self._state.current_index = 0
            if self._matches:
                self._status_label.setText(f"1 of {len(self._matches)} matches")
            else:
                self._status_label.setText("No matches")
        else:
            self._status_label.setText("Ready to search in current document")
            self._matches = []
            self._state.current_index = 0
            self._state.total_matches = 0

        self._update_button_states()

    def _update_button_states(self) -> None:
        has_find_text = bool(self._find_edit.text())
        has_replace_text = bool(self._replace_edit.text())
        self._btn_next.setEnabled(has_find_text)
        self._btn_prev.setEnabled(has_find_text)
        self._btn_replace.setEnabled(has_find_text and has_replace_text)
        self._btn_replace_all.setEnabled(has_find_text and has_replace_text)

    def _on_next(self) -> None:
        find_text = self._find_edit.text()
        if not find_text:
            return
        if self._project_check.isChecked() and self._matches:
            self._state.current_index = (self._state.current_index + 1) % len(self._matches)
            uuid, title, _ = self._matches[self._state.current_index]
            self._status_label.setText(f"{self._state.current_index + 1} of {len(self._matches)}: {title}")
            self.navigate_to_doc.emit(uuid)
        else:
            self.find_next.emit(find_text, True, self._state.whole_word)

    def _on_prev(self) -> None:
        find_text = self._find_edit.text()
        if not find_text:
            return
        if self._project_check.isChecked() and self._matches:
            self._state.current_index = (self._state.current_index - 1) % len(self._matches)
            uuid, title, _ = self._matches[self._state.current_index]
            self._status_label.setText(f"{self._state.current_index + 1} of {len(self._matches)}: {title}")
            self.navigate_to_doc.emit(uuid)
        else:
            self.find_next.emit(find_text, False, self._state.whole_word)

    def _on_replace(self) -> None:
        find_text = self._find_edit.text()
        replace_text = self._replace_edit.text()
        if not find_text:
            return
        self.replace_one.emit(find_text, replace_text, self._state.whole_word)

    def _on_replace_all(self) -> None:
        find_text = self._find_edit.text()
        replace_text = self._replace_edit.text()
        if not find_text:
            return
        self.replace_all.emit(find_text, replace_text, self._project_check.isChecked(), self._state.whole_word)

    def update_status(self, message: str) -> None:
        self._status_label.setText(message)

    def show_from_main_window(self, editor_text_edit) -> None:
        self._editor = editor_text_edit
        self._replace_edit.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self._find_edit.setFocus()
        self._find_edit.selectAll()