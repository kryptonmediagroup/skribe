"""Compile dialog — the project-wide "build a release file" workflow.

Patterned after Scrivener's Compile Overview, deliberately stripped of
its Section Layouts machinery: a Compile-For format dropdown, a
Manuscript-rooted item tree with Include checkboxes, optional Front
Matter title-page fields, and a Compile button that runs the pipeline
in :mod:`skribe.ioformat.compile_export`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from skribe.ioformat.compile_export import (
    CompileOptions,
    FrontMatter,
    supported_formats,
)
from skribe.model.project import BinderItem, ItemType, Project


@dataclass
class CompileResult:
    """What the dialog hands back to the caller on Accept."""
    fmt: str
    options: CompileOptions
    persist_includes: dict[str, bool]   # {uuid: include_in_compile} — to write back


class CompileDialog(QDialog):
    """Modal "Compile Overview" — choose format, scope, options."""

    def __init__(
        self,
        project: Project,
        read_body: Callable[[str], str],
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._read_body = read_body  # only used if we ever preview
        self.setWindowTitle("Compile")
        self.setModal(True)
        self.resize(680, 640)

        # --- Format row -------------------------------------------------
        self._format_combo = QComboBox(self)
        for fmt in supported_formats():
            self._format_combo.addItem(fmt)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Compile For:"))
        format_row.addWidget(self._format_combo, 1)

        # --- Scope label ------------------------------------------------
        # Scrivener's Compile dropdown lets the user pick a folder root;
        # we hard-pin to Manuscript for now since that's what 99% of
        # Skribe users want — and it keeps the UI uncluttered.
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Compile group:"))
        scope_row.addWidget(QLabel("Manuscript"))
        scope_row.addStretch(1)

        # --- Item tree --------------------------------------------------
        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Title", "Type"])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setColumnWidth(0, 380)
        self._populate_tree()

        # --- Front matter ----------------------------------------------
        self._front_group = QGroupBox("Add front matter (title page)", self)
        self._front_group.setCheckable(True)
        self._front_group.setChecked(False)

        self._title_edit = QLineEdit(self)
        self._title_edit.setText(self._project.name or "")
        self._subtitle_edit = QLineEdit(self)
        self._author_edit = QLineEdit(self)

        front_form = QFormLayout()
        front_form.addRow("Title:", self._title_edit)
        front_form.addRow("Subtitle:", self._subtitle_edit)
        front_form.addRow("Author:", self._author_edit)
        self._front_group.setLayout(front_form)

        # --- Buttons ----------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel,
            parent=self,
        )
        self._compile_btn = buttons.addButton("Compile", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # --- Assembly ---------------------------------------------------
        layout = QVBoxLayout(self)
        layout.addLayout(format_row)
        layout.addLayout(scope_row)
        layout.addWidget(QLabel("Items to include:"))
        layout.addWidget(self._tree, 1)
        layout.addWidget(self._front_group)
        layout.addWidget(buttons)

    # --- public API -------------------------------------------------

    def chosen_format(self) -> str:
        return self._format_combo.currentText()

    def build_result(self) -> Optional[CompileResult]:
        """Materialize the user's choices into a CompileResult.

        Returns None if no items are checked — caller should treat this
        as a no-op rather than running an empty compile.
        """
        ordered_items, persist = self._collect_checked_items()
        if not ordered_items:
            return None
        front = FrontMatter(
            enabled=self._front_group.isChecked(),
            title=self._title_edit.text().strip(),
            subtitle=self._subtitle_edit.text().strip(),
            author=self._author_edit.text().strip(),
        )
        opts = CompileOptions(
            items=ordered_items,
            project_title=self._project.name or "",
            front=front,
        )
        return CompileResult(
            fmt=self.chosen_format(),
            options=opts,
            persist_includes=persist,
        )

    # --- internals ---------------------------------------------------

    def _populate_tree(self) -> None:
        """Build a tree mirroring the Manuscript subtree.

        Each row stores its BinderItem in ``UserRole``. Checkboxes
        default to the item's current ``include_in_compile`` flag (true
        when missing — same convention as :func:`stats.compiled_items`).
        """
        draft = self._project.root_draft()
        if draft is None:
            return
        for child in draft.children:
            self._tree.addTopLevelItem(self._make_row(child))
        self._tree.expandAll()

    def _make_row(self, item: BinderItem) -> QTreeWidgetItem:
        included = bool(item.metadata.get("include_in_compile", True))
        type_label = "Folder" if item.type.is_container else "Section"
        row = QTreeWidgetItem([item.title or "(untitled)", type_label])
        row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
        row.setCheckState(0, Qt.Checked if included else Qt.Unchecked)
        row.setData(0, Qt.UserRole, item.uuid)
        for child in item.children:
            row.addChild(self._make_row(child))
        return row

    def _collect_checked_items(self) -> tuple[list[BinderItem], dict[str, bool]]:
        """Walk the tree depth-first, returning checked items in order.

        Also accumulates {uuid: bool} so the caller can persist any
        toggles the user made back to the project.
        """
        ordered: list[BinderItem] = []
        persist: dict[str, bool] = {}
        for i in range(self._tree.topLevelItemCount()):
            self._collect(self._tree.topLevelItem(i), ordered, persist)
        return ordered, persist

    def _collect(
        self,
        row: QTreeWidgetItem,
        ordered: list[BinderItem],
        persist: dict[str, bool],
    ) -> None:
        uuid = row.data(0, Qt.UserRole)
        item = self._project.find(uuid) if isinstance(uuid, str) else None
        checked = row.checkState(0) == Qt.Checked
        if item is not None:
            persist[item.uuid] = checked
            if checked:
                ordered.append(item)
        for i in range(row.childCount()):
            self._collect(row.child(i), ordered, persist)
