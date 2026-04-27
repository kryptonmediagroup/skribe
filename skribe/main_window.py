"""Skribe main window.

Three-pane layout: binder (left) | editor (center) | inspector (right).
Save-on-switch semantics: when the binder selection changes, the current
document is flushed to disk before the new one is loaded, and its cursor
position is recorded in the per-project ``ui_state.json``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QWidget,
)

from skribe.ioformat.scriv_import import import_scriv
from skribe.ioformat.skribe_io import load_project, save_project
from skribe.ioformat.doc_export import (
    EXPORT_EXT,
    DocExportError,
    export_document,
    supported_export_formats,
)
from skribe.ioformat.doc_import import (
    IMPORT_EXTENSIONS,
    DocImportError,
    import_document,
)
from skribe.ioformat.compile_export import (
    CompileError,
    CompileOptions,
    FMT_PRINT,
    build_compile_html,
    file_extension,
    render_html_to_printer,
    write_compile,
)
from skribe.ioformat.scriv_export import ScrivExportError, export_scriv
from skribe.ioformat.skribe_io import (
    is_skribe_bundle,
    read_comments,
    read_document_body,
    read_ui_state,
    write_comments,
    write_document_body,
    write_ui_state,
)
from skribe.model.binder_model import BinderModel
from skribe.model.comment import Comment
from skribe.model.project import BinderItem, ItemType, Project
from skribe.settings import Keys, app_settings, derive_initials
from skribe.stats import compiled_items
from skribe.themes import theme_for
from skribe.ui.binder_view import BinderView
from skribe.ui.comments_panel import CommentsPanel
from skribe.ui.corkboard_view import CorkboardView
from skribe.ui.editor import EditorWidget
from skribe.ui.inspector import InspectorWidget
from skribe.ui.compile_dialog import CompileDialog
from skribe.ui.preferences import PreferencesDialog
from skribe.ui.search_panel import (
    SCOPE_ALL,
    SCOPE_SYNOPSIS,
    SCOPE_TEXT,
    SCOPE_TITLE,
    SearchPanel,
    SearchQuery,
    count_matches,
    find_match_ranges,
    matches_document,
    plain_text_from_html,
)
from skribe.ui.statistics import StatisticsDialog

VIEW_EDITOR = "editor"
VIEW_CORKBOARD = "corkboard"

APP_ORG = "Skribe"
APP_NAME = "Skribe"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Skribe")
        self.resize(1400, 900)

        self._settings = app_settings()
        self._project: Optional[Project] = None
        self._current_item: Optional[BinderItem] = None
        self._dirty_editor = False
        self._ui_state: dict = {}   # per-project UI state (cursors etc.)
        self._recent_actions: list[QAction] = []
        # Comments belonging to the currently loaded document, keyed by uuid.
        self._current_comments: dict[str, Comment] = {}
        self._comments_dirty = False

        self._view_mode = str(self._settings.get(Keys.VIEW_MODE) or VIEW_EDITOR)
        if self._view_mode not in (VIEW_EDITOR, VIEW_CORKBOARD):
            self._view_mode = VIEW_EDITOR

        self._binder_view = BinderView(self)
        self._editor = EditorWidget(self)
        self._corkboard = CorkboardView(self)
        self._inspector = InspectorWidget(self)
        self._comments_panel = CommentsPanel(self)
        self._model = BinderModel(parent=self)
        self._binder_view.setModel(self._model)
        self._corkboard.setModel(self._model)

        # Search state. ``_search_panel`` is created lazily the first
        # time the user opens the Search tab; closing the tab tears it
        # down and resets ``_search_query``. ``_jump_to_first_match``
        # is a one-shot flag the search panel sets so the next binder
        # selection lands the caret on the first hit instead of the
        # remembered cursor position.
        self._left_tabs = QTabWidget(self)
        self._left_tabs.setDocumentMode(True)
        self._left_tabs.setTabsClosable(True)
        self._left_tabs.addTab(self._binder_view, "Binder")
        # Strip the close button off the Binder tab — only the Search
        # tab is meant to be dismissable.
        self._left_tabs.tabBar().setTabButton(0, QTabBar.RightSide, None)
        self._left_tabs.tabBar().setTabButton(0, QTabBar.LeftSide, None)
        self._left_tabs.tabCloseRequested.connect(self._on_left_tab_close)

        # Magnifier shortcut in the corner of the tab bar — third entry
        # point alongside the menu item and Ctrl+Shift+F. We skip the
        # freedesktop ``edit-find`` icon on purpose: when the system
        # ships a dark-styled icon set, the resulting glyph is rendered
        # white-on-light-gray and is effectively invisible against the
        # tab bar. The U+2315 glyph picks up the palette's text color,
        # which Qt adapts to whichever theme is active.
        search_btn = QToolButton(self._left_tabs)
        search_btn.setText("⌕")
        glyph_font = search_btn.font()
        glyph_font.setPointSize(max(glyph_font.pointSize() + 4, 14))
        glyph_font.setBold(True)
        search_btn.setFont(glyph_font)
        search_btn.setToolTip("Search in Project (Ctrl+Shift+F)")
        search_btn.setAutoRaise(True)
        search_btn.clicked.connect(self._action_find_in_project)
        self._left_tabs.setCornerWidget(search_btn, Qt.TopRightCorner)

        self._search_panel: Optional[SearchPanel] = None
        self._search_query: Optional[SearchQuery] = None
        self._jump_to_first_match = False

        self._center_stack = QStackedWidget(self)
        self._center_stack.addWidget(self._editor)     # index 0
        self._center_stack.addWidget(self._corkboard)  # index 1

        right_split = QSplitter(Qt.Vertical, self)
        right_split.addWidget(self._inspector)
        right_split.addWidget(self._comments_panel)
        right_split.setStretchFactor(0, 1)
        right_split.setStretchFactor(1, 2)
        right_split.setSizes([260, 420])

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._left_tabs)
        splitter.addWidget(self._center_stack)
        splitter.addWidget(right_split)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([280, 900, 320])
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().setSizeGripEnabled(False)
        self._word_count_label = QLabel("", self)
        # Center the word-count label by flanking it with stretch spacers in
        # the permanent (right-hand) area. When no transient message is shown,
        # the permanent area spans the full status bar, so the label sits in
        # the middle. Brief showMessage() popups may push it slightly while
        # they're visible — acceptable.
        left_spacer = QWidget(self)
        left_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right_spacer = QWidget(self)
        right_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.statusBar().addPermanentWidget(left_spacer, 1)
        self.statusBar().addPermanentWidget(self._word_count_label)
        self.statusBar().addPermanentWidget(right_spacer, 1)

        self._build_menus()
        self._wire_signals()
        self._restore_window_state()
        self._apply_current_theme()
        self._rebuild_recent_menu()
        self._update_ui_for_project()

    # --- public API ---------------------------------------------------

    def maybe_reopen_last_project(self) -> None:
        """Called once on startup if the setting is on."""
        if not self._settings.get(Keys.REOPEN_LAST):
            return
        last = str(self._settings.get(Keys.LAST_PROJECT) or "")
        if not last:
            return
        path = Path(last)
        if not is_skribe_bundle(path):
            return
        try:
            project = load_project(path)
        except Exception:  # noqa: BLE001
            return
        self._load_project(project)

    # --- wiring -------------------------------------------------------

    def _wire_signals(self) -> None:
        self._binder_view.selectionModel().currentChanged.connect(self._on_binder_selection)
        self._binder_view.add_requested.connect(self._on_add_requested)
        self._binder_view.delete_requested.connect(self._on_delete_requested)
        self._editor.contents_changed.connect(self._on_editor_changed)
        self._editor.selection_changed.connect(self._update_word_count)
        self._editor.comment_anchor_requested.connect(self._on_comment_anchor_requested)
        self._editor.comment_remove_requested.connect(self._on_comment_delete_requested)
        self._editor.comment_add_requested.connect(self._action_add_comment)
        self._inspector.synopsis_changed.connect(self._on_synopsis_changed)
        self._inspector.include_in_compile_changed.connect(self._on_include_changed)
        self._comments_panel.comment_body_changed.connect(self._on_comment_body_changed)
        self._comments_panel.comment_delete_requested.connect(self._on_comment_delete_requested)
        self._comments_panel.comment_selected.connect(self._on_comment_selected)
        self._corkboard.card_activated.connect(self._on_corkboard_activated)
        self._corkboard.context_menu_requested.connect(self._on_corkboard_context_menu)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")

        act_new = QAction("&New Project…", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self._action_new)
        file_menu.addAction(act_new)

        act_open = QAction("&Open Project…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._action_open)
        file_menu.addAction(act_open)

        self._recent_menu = file_menu.addMenu("Open &Recent")

        file_menu.addSeparator()

        act_save = QAction("&Save", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self._action_save)
        file_menu.addAction(act_save)

        act_close = QAction("&Close Project", self)
        act_close.triggered.connect(self._action_close)
        file_menu.addAction(act_close)

        file_menu.addSeparator()

        import_menu = file_menu.addMenu("&Import")
        act_import_scriv = QAction("&Scrivener Project…", self)
        act_import_scriv.triggered.connect(self._action_import_scriv)
        import_menu.addAction(act_import_scriv)
        act_import_doc = QAction("&Document…", self)
        act_import_doc.triggered.connect(self._action_import_document)
        import_menu.addAction(act_import_doc)

        export_menu = file_menu.addMenu("&Export")
        self._act_export_doc = QAction("Current &Document…", self)
        self._act_export_doc.triggered.connect(self._action_export_document)
        export_menu.addAction(self._act_export_doc)
        self._act_export_scriv = QAction("To &Scrivener…", self)
        self._act_export_scriv.triggered.connect(self._action_export_scriv)
        export_menu.addAction(self._act_export_scriv)

        file_menu.addSeparator()
        act_compile = QAction("&Compile…", self)
        act_compile.setShortcut(QKeySequence("Ctrl+Shift+E"))
        act_compile.triggered.connect(self._action_compile)
        file_menu.addAction(act_compile)
        self._act_compile = act_compile

        file_menu.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        edit_menu = mb.addMenu("&Edit")
        edit_menu.addAction(self._make_edit_action("&Undo", QKeySequence.Undo, "undo"))
        edit_menu.addAction(self._make_edit_action("&Redo", QKeySequence.Redo, "redo"))
        edit_menu.addSeparator()
        edit_menu.addAction(self._make_edit_action("Cu&t", QKeySequence.Cut, "cut"))
        edit_menu.addAction(self._make_edit_action("&Copy", QKeySequence.Copy, "copy"))
        edit_menu.addAction(self._make_edit_action("&Paste", QKeySequence.Paste, "paste"))
        edit_menu.addSeparator()

        edit_menu.addSeparator()
        act_find_project = QAction("&Search in Project…", self)
        act_find_project.setShortcut(QKeySequence("Ctrl+Shift+F"))
        act_find_project.triggered.connect(self._action_find_in_project)
        edit_menu.addAction(act_find_project)

        edit_menu.addSeparator()
        act_add_comment = QAction("Add &Comment", self)
        act_add_comment.setShortcut(QKeySequence("Ctrl+Alt+C"))
        act_add_comment.triggered.connect(self._action_add_comment)
        edit_menu.addAction(act_add_comment)
        self._act_add_comment = act_add_comment

        edit_menu.addSeparator()
        act_prefs = QAction("&Preferences…", self)
        act_prefs.setShortcut(QKeySequence("Ctrl+,"))
        act_prefs.triggered.connect(self._action_preferences)
        edit_menu.addAction(act_prefs)

        view_menu = mb.addMenu("&View")
        self._view_action_group = QActionGroup(self)
        self._view_action_group.setExclusive(True)

        self._act_view_editor = QAction("&Editor", self, checkable=True)
        self._act_view_editor.setShortcut(QKeySequence("Ctrl+1"))
        self._act_view_editor.triggered.connect(lambda: self._set_view_mode(VIEW_EDITOR))
        self._view_action_group.addAction(self._act_view_editor)
        view_menu.addAction(self._act_view_editor)

        self._act_view_corkboard = QAction("&Corkboard", self, checkable=True)
        self._act_view_corkboard.setShortcut(QKeySequence("Ctrl+2"))
        self._act_view_corkboard.triggered.connect(lambda: self._set_view_mode(VIEW_CORKBOARD))
        self._view_action_group.addAction(self._act_view_corkboard)
        view_menu.addAction(self._act_view_corkboard)

        if self._view_mode == VIEW_CORKBOARD:
            self._act_view_corkboard.setChecked(True)
        else:
            self._act_view_editor.setChecked(True)

        view_menu.addSeparator()
        self._act_spellcheck = QAction("Check &Spelling", self, checkable=True)
        self._act_spellcheck.setShortcut(QKeySequence("Ctrl+Shift+;"))
        self._act_spellcheck.setChecked(bool(self._settings.get(Keys.SPELLCHECK_ENABLED)))
        self._act_spellcheck.toggled.connect(self._toggle_spellcheck)
        from skribe.spellcheck import is_available as _spell_available
        if not _spell_available():
            self._act_spellcheck.setEnabled(False)
            self._act_spellcheck.setToolTip(
                "Spell check unavailable — install pyenchant and a hunspell "
                "dictionary, then restart Skribe."
            )
        view_menu.addAction(self._act_spellcheck)

        project_menu = mb.addMenu("&Project")
        act_add_text = QAction("Add &Text", self)
        act_add_text.triggered.connect(lambda: self._add_under_current(ItemType.TEXT))
        project_menu.addAction(act_add_text)

        act_add_folder = QAction("Add &Folder", self)
        act_add_folder.triggered.connect(lambda: self._add_under_current(ItemType.FOLDER))
        project_menu.addAction(act_add_folder)

        project_menu.addSeparator()
        act_stats = QAction("&Statistics…", self)
        act_stats.triggered.connect(self._action_statistics)
        project_menu.addAction(act_stats)

        help_menu = mb.addMenu("&Help")
        act_about = QAction("&About Skribe", self)
        act_about.triggered.connect(self._action_about)
        help_menu.addAction(act_about)

    def _make_edit_action(self, label: str, shortcut, method: str) -> QAction:
        act = QAction(label, self)
        act.setShortcut(shortcut)
        act.triggered.connect(lambda: self._editor_method(method))
        return act

    def _editor_method(self, name: str) -> None:
        te = self._editor.findChild(QTextEdit)
        if te is not None and hasattr(te, name):
            getattr(te, name)()

    # --- recent projects ----------------------------------------------

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        self._recent_actions.clear()
        recents = self._settings.recent_projects()
        if not recents:
            empty = self._recent_menu.addAction("(No recent projects)")
            empty.setEnabled(False)
            return
        for p in recents:
            act = QAction(self._recent_display(p), self)
            act.triggered.connect(lambda checked=False, path=p: self._open_recent(path))
            self._recent_menu.addAction(act)
            self._recent_actions.append(act)
        self._recent_menu.addSeparator()
        act_clear = QAction("Clear Recent", self)
        act_clear.triggered.connect(self._clear_recent)
        self._recent_menu.addAction(act_clear)

    def _recent_display(self, path: str) -> str:
        p = Path(path)
        return f"{p.name}    ({p.parent})"

    def _open_recent(self, path: str) -> None:
        if not self._confirm_discard():
            return
        p = Path(path)
        if not is_skribe_bundle(p):
            QMessageBox.warning(
                self, "Open Recent", f"Project not found or not a Skribe bundle:\n{p}"
            )
            self._settings.remove_recent_project(path)
            self._rebuild_recent_menu()
            return
        try:
            project = load_project(p)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open Recent", f"Failed to open:\n{exc}")
            return
        self._load_project(project)

    def _clear_recent(self) -> None:
        self._settings.clear_recent_projects()
        self._rebuild_recent_menu()

    def _register_recent(self, path: Path) -> None:
        self._settings.push_recent_project(str(path))
        self._settings.set(Keys.LAST_PROJECT, str(path))
        self._rebuild_recent_menu()

    # --- file actions -------------------------------------------------

    def _action_new(self) -> None:
        if not self._confirm_discard():
            return
        dlg = QFileDialog(self, "New Project")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setFileMode(QFileDialog.AnyFile)
        dlg.setNameFilter("Skribe projects (*.skribe)")
        dlg.setDefaultSuffix("skribe")
        if dlg.exec() != QFileDialog.Accepted:
            return
        selected = Path(dlg.selectedFiles()[0])
        if selected.suffix != ".skribe":
            selected = selected.with_suffix(".skribe")
        if selected.exists():
            QMessageBox.warning(self, "New Project", f"{selected} already exists.")
            return
        project = Project.new(name=selected.stem)
        project.path = selected
        save_project(project, selected)
        self._load_project(project)

    def _action_open(self) -> None:
        if not self._confirm_discard():
            return
        path_str = QFileDialog.getExistingDirectory(self, "Open Project", "", QFileDialog.ShowDirsOnly)
        if not path_str:
            return
        path = Path(path_str)
        if not is_skribe_bundle(path):
            QMessageBox.warning(self, "Open Project", f"{path} is not a Skribe bundle.")
            return
        try:
            project = load_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open Project", f"Failed to open:\n{exc}")
            return
        self._load_project(project)

    def _action_save(self) -> None:
        if self._project is None:
            return
        self._flush_current_editor()
        save_project(self._project)
        self._persist_ui_state()
        self.statusBar().showMessage("Saved", 2000)

    def _action_close(self) -> None:
        if not self._confirm_discard():
            return
        self._project = None
        self._current_item = None
        self._ui_state = {}
        self._current_comments = {}
        self._comments_dirty = False
        self._model.set_project(Project.new())
        self._editor.clear()
        self._editor.clear_search_highlights()
        self._inspector.set_item(None)
        self._comments_panel.set_comments([])
        if self._search_panel is not None:
            self._search_panel.set_results([], 0)
        self._update_ui_for_project()

    def _action_import_scriv(self) -> None:
        if not self._confirm_discard():
            return
        src_str = QFileDialog.getExistingDirectory(
            self, "Import Scrivener Project (.scriv)", "", QFileDialog.ShowDirsOnly
        )
        if not src_str:
            return
        src = Path(src_str)
        if src.suffix.lower() != ".scriv":
            QMessageBox.warning(self, "Import", f"{src} is not a .scriv bundle.")
            return
        dlg = QFileDialog(self, "Save Imported Project As…")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilter("Skribe projects (*.skribe)")
        dlg.setDefaultSuffix("skribe")
        dlg.selectFile(src.stem + ".skribe")
        if dlg.exec() != QFileDialog.Accepted:
            return
        out = Path(dlg.selectedFiles()[0])
        if out.suffix != ".skribe":
            out = out.with_suffix(".skribe")
        try:
            project = import_scriv(src, out)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import", f"Failed to import:\n{exc}")
            return
        self._load_project(project)
        self.statusBar().showMessage(f"Imported {src.name}", 3000)

    def _action_import_document(self) -> None:
        if self._project is None or self._project.path is None:
            QMessageBox.information(self, "Import Document", "Open or create a project first.")
            return
        self._flush_current_editor()
        pattern = " ".join(f"*{ext}" for ext in IMPORT_EXTENSIONS)
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import Document", "", f"Documents ({pattern});;All files (*)"
        )
        if not path_str:
            return
        src = Path(path_str)
        try:
            title, html = import_document(src)
        except DocImportError as exc:
            QMessageBox.critical(self, "Import Document", str(exc))
            return

        # Insert as a new TEXT under the current selection (or Draft root).
        current_idx = self._binder_view.currentIndex()
        new_idx = self._model.add_item(current_idx, ItemType.TEXT, title=title)
        if not new_idx.isValid():
            QMessageBox.critical(self, "Import Document", "Couldn't insert the new document.")
            return
        new_item = self._model.item_from_index(new_idx)
        if new_item is None:
            return
        write_document_body(self._project.path, new_item.uuid, html)
        save_project(self._project)
        self._binder_view.setCurrentIndex(new_idx)
        self.statusBar().showMessage(f"Imported {src.name}", 3000)

    def _action_export_document(self) -> None:
        if (
            self._project is None
            or self._project.path is None
            or self._current_item is None
            or self._current_item.type.is_container
        ):
            QMessageBox.information(
                self, "Export Document", "Select a text document to export."
            )
            return
        self._flush_current_editor()

        formats = supported_export_formats()
        if not formats:
            QMessageBox.critical(self, "Export Document", "No export formats available on this system.")
            return
        filters = ";;".join(f"{fmt} (*{EXPORT_EXT[fmt]})" for fmt in formats)
        default_name = (self._current_item.title or "Untitled") + EXPORT_EXT[formats[0]]
        dlg = QFileDialog(self, "Export Document")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilters([f"{fmt} (*{EXPORT_EXT[fmt]})" for fmt in formats])
        dlg.selectFile(default_name)
        if dlg.exec() != QFileDialog.Accepted:
            return
        chosen_filter = dlg.selectedNameFilter()
        # Name filters look like "DOCX (*.docx)"; recover the format token.
        fmt = chosen_filter.split(" ", 1)[0] if chosen_filter else formats[0]
        target = Path(dlg.selectedFiles()[0])
        expected_ext = EXPORT_EXT.get(fmt, target.suffix)
        if target.suffix.lower() != expected_ext:
            target = target.with_suffix(expected_ext)

        body = read_document_body(self._project.path, self._current_item.uuid)
        comments = read_comments(self._project.path, self._current_item.uuid)
        try:
            export_document(body, comments, target, fmt)
        except DocExportError as exc:
            QMessageBox.critical(self, "Export Document", str(exc))
            return
        self.statusBar().showMessage(f"Exported {target.name}", 3000)

    def _action_export_scriv(self) -> None:
        if self._project is None or self._project.path is None:
            QMessageBox.information(self, "Export to Scrivener", "Open or create a project first.")
            return
        self._flush_current_editor()
        save_project(self._project)

        dlg = QFileDialog(self, "Export to Scrivener (.scriv)")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilter("Scrivener projects (*.scriv)")
        dlg.setDefaultSuffix("scriv")
        dlg.selectFile((self._project.name or "Skribe Project") + ".scriv")
        if dlg.exec() != QFileDialog.Accepted:
            return
        target = Path(dlg.selectedFiles()[0])
        if target.suffix.lower() != ".scriv":
            target = target.with_suffix(".scriv")
        if target.exists():
            resp = QMessageBox.question(
                self, "Export to Scrivener",
                f"{target.name} already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        try:
            export_scriv(self._project, target)
        except ScrivExportError as exc:
            QMessageBox.critical(self, "Export to Scrivener", str(exc))
            return
        self.statusBar().showMessage(f"Exported to {target.name}", 3000)

    def _action_compile(self) -> None:
        """Open the Compile dialog and dispatch to the chosen target."""
        if self._project is None or self._project.path is None:
            QMessageBox.information(self, "Compile", "Open or create a project first.")
            return
        # Flush so unsaved edits land in the compile body.
        self._flush_current_editor()

        dlg = CompileDialog(self._project, self._read_body_for_compile, self)
        if dlg.exec() != QDialog.Accepted:
            return
        result = dlg.build_result()
        if result is None:
            QMessageBox.information(
                self, "Compile",
                "No items are checked. Select at least one section to compile.",
            )
            return

        # Persist the user's Include toggles back to the project so the
        # next compile remembers them and the inspector stays in sync.
        self._persist_include_changes(result.persist_includes)

        html = build_compile_html(result.options, self._read_body_for_compile)

        if result.fmt == FMT_PRINT:
            self._compile_to_printer(html)
            return
        self._compile_to_file(result.fmt, html, result.options)

    def _compile_to_printer(self, html: str) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            render_html_to_printer(html, printer)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Compile", f"Print failed:\n{exc}")
            return
        self.statusBar().showMessage("Sent to printer.", 3000)

    def _compile_to_file(self, fmt: str, html: str, options: CompileOptions) -> None:
        ext = file_extension(fmt) or ""
        suggested = (self._project.name or "Manuscript") + ext
        dlg = QFileDialog(self, f"Compile — {fmt}")
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilter(f"{fmt} (*{ext})" if ext else fmt)
        if ext:
            dlg.setDefaultSuffix(ext.lstrip("."))
        dlg.selectFile(suggested)
        if dlg.exec() != QFileDialog.Accepted:
            return
        target = Path(dlg.selectedFiles()[0])
        if ext and target.suffix.lower() != ext:
            target = target.with_suffix(ext)
        try:
            write_compile(fmt, html, target, options)
        except CompileError as exc:
            QMessageBox.critical(self, "Compile", str(exc))
            return
        self.statusBar().showMessage(f"Compiled to {target.name}", 3000)

    def _persist_include_changes(self, persist: dict[str, bool]) -> None:
        """Write back any Include toggles the user changed in the dialog."""
        if self._project is None or not persist:
            return
        changed = False
        for uuid, included in persist.items():
            item = self._project.find(uuid)
            if item is None:
                continue
            current = bool(item.metadata.get("include_in_compile", True))
            if current != included:
                item.metadata["include_in_compile"] = included
                item.touch()
                changed = True
        if changed:
            self._project.touch()
            try:
                save_project(self._project)
            except Exception:  # noqa: BLE001
                pass
            # Refresh the inspector if it's pointed at one of the toggled
            # items so the checkbox reflects the new state immediately.
            self._inspector.set_item(self._current_item)

    def _read_body_for_compile(self, uuid: str) -> str:
        """Body lookup for the compile pipeline.

        Prefers the live editor when the requested item is the one the
        user is currently editing — that way an in-flight edit shows up
        in the compile output without forcing a save first.
        """
        if self._project is None or self._project.path is None:
            return ""
        if (
            self._current_item is not None
            and self._current_item.uuid == uuid
            and not self._current_item.type.is_container
        ):
            return self._editor.html()
        try:
            return read_document_body(self._project.path, uuid)
        except Exception:  # noqa: BLE001
            return ""

    def _action_add_comment(self) -> None:
        if self._current_item is None or self._current_item.type.is_container:
            return
        if not self._editor.has_selection():
            self.statusBar().showMessage("Select some text to comment on.", 3000)
            return
        hit = self._editor.new_comment_from_selection()
        if hit is None:
            return
        comment_id, start, end, snippet = hit
        name = str(self._settings.get(Keys.AUTHOR_NAME) or "").strip()
        initials = str(self._settings.get(Keys.AUTHOR_INITIALS) or "").strip().upper()
        if not initials:
            initials = derive_initials(name) or "?"
        comment = Comment(
            uuid=comment_id,
            author_name=name,
            author_initials=initials,
            body="",
            anchor_start=start,
            anchor_end=end,
            anchor_text=snippet,
        )
        self._current_comments[comment_id] = comment
        self._comments_dirty = True
        self._comments_panel.add_comment(comment)
        self.statusBar().showMessage(f"Comment added ({initials}).", 2000)

    def _action_preferences(self) -> None:
        dlg = PreferencesDialog(self)
        dlg.applied.connect(self._apply_current_settings)
        dlg.exec()
        # Preferences may have flipped spellcheck on/off — keep the View
        # menu's checkbox in lockstep so the two widgets never disagree.
        self._act_spellcheck.blockSignals(True)
        self._act_spellcheck.setChecked(bool(self._settings.get(Keys.SPELLCHECK_ENABLED)))
        self._act_spellcheck.blockSignals(False)

    def _toggle_spellcheck(self, enabled: bool) -> None:
        self._editor.set_spellcheck_enabled(enabled)

    def _action_statistics(self) -> None:
        if self._project is None:
            QMessageBox.information(
                self, "Statistics", "Open or create a project first.",
            )
            return
        # Flush whatever's in the editor so the stats see the user's
        # in-progress edits, not whatever was last written to disk.
        self._flush_current_editor()

        compiled = compiled_items(self._project.roots)
        selected = self._selected_binder_items()

        bundle = self._project.path

        def _read_body(uuid: str) -> Optional[str]:
            if bundle is None:
                return None
            try:
                return read_document_body(bundle, uuid)
            except Exception:
                return None

        dlg = StatisticsDialog(compiled, selected, _read_body, self)
        dlg.exec()

    def _selected_binder_items(self) -> list[BinderItem]:
        """Items currently selected in the binder, de-duplicated.

        Falls back to the current item if the selection model is empty —
        which happens when the user opened Statistics straight after
        clicking an item without holding Shift/Ctrl.
        """
        sm = self._binder_view.selectionModel()
        items: list[BinderItem] = []
        seen: set[str] = set()
        for idx in sm.selectedIndexes():
            it = self._model.item_from_index(idx)
            if it and it.uuid not in seen:
                seen.add(it.uuid)
                items.append(it)
        if not items and self._current_item is not None:
            items = [self._current_item]
        return items

    def _action_about(self) -> None:
        QMessageBox.about(
            self,
            "About Skribe",
            "Skribe — a personal Scrivener-style writing environment.\n"
            "Built with PySide6 / Qt 6.",
        )

    # --- settings application ----------------------------------------

    def _apply_current_theme(self) -> None:
        theme = theme_for(str(self._settings.get(Keys.THEME)))
        from skribe.themes import apply_theme
        apply_theme(theme)
        self._editor.apply_theme(theme)

    def _apply_current_settings(self) -> None:
        """Called after the Preferences dialog writes new values."""
        self._apply_current_theme()
        self._editor.reload_settings()
        self._rebuild_recent_menu()

    # --- project lifecycle -------------------------------------------

    def _load_project(self, project: Project) -> None:
        self._flush_current_editor()
        self._persist_ui_state()

        self._project = project
        self._current_item = None
        self._model.set_project(project)
        self._editor.clear()
        self._inspector.set_item(None)
        self._binder_view.expandAll()
        self._update_ui_for_project()

        if project.path is not None:
            self._ui_state = read_ui_state(project.path)
            self._register_recent(project.path)
        else:
            self._ui_state = {}

        # If the search tab survived from a previous project, re-run
        # the query against the new content so its result list is
        # never showing stale UUIDs.
        if self._search_panel is not None:
            self._populate_search_results()

        # Pick an item to open: last-selected if it still exists, else first text.
        target: Optional[BinderItem] = None
        last_uuid = str(self._ui_state.get("last_selected_uuid") or "")
        if last_uuid:
            target = project.find(last_uuid)
        if target is None:
            draft = project.root_draft()
            if draft is not None:
                target = next((i for i in draft.walk() if i.type is ItemType.TEXT), None)
        if target is not None:
            idx = self._model.index_for_item(target)
            self._binder_view.setCurrentIndex(idx)

    def _update_ui_for_project(self) -> None:
        has_project = self._project is not None
        self.setWindowTitle(
            f"Skribe — {self._project.name}" if has_project else "Skribe"
        )
        self._binder_view.setEnabled(has_project)
        self._editor.set_editable(False)
        self._word_count_label.setText("")
        # Reset which center-pane page is shown to match current selection.
        # On close (item=None), this falls back to the editor; on load, the
        # binder selection that follows will re-apply the right page.
        self._apply_view_for_current_item()

    # --- selection / editing -----------------------------------------

    def _on_binder_selection(self, current, previous) -> None:  # noqa: ARG002
        # Flush body + cursor for the outgoing document before swapping.
        self._flush_current_editor()
        self._record_current_cursor()

        item = self._model.item_from_index(current) if current.isValid() else None
        self._current_item = item
        self._inspector.set_item(item)
        if item is None or item.type.is_container:
            self._editor.clear()
            self._editor.set_editable(False)
            self._word_count_label.setText("")
            self._current_comments = {}
            self._comments_dirty = False
            self._comments_panel.set_comments([])
            self._apply_view_for_current_item()
            return

        # Text item — always edit in the editor regardless of view mode.
        self._center_stack.setCurrentWidget(self._editor)

        body = ""
        comments: list[Comment] = []
        if self._project is not None and self._project.path is not None:
            body = read_document_body(self._project.path, item.uuid)
            comments = read_comments(self._project.path, item.uuid)
        self._editor.set_html(body)
        self._editor.set_editable(True)
        self._dirty_editor = False
        # Re-apply yellow highlights for any comments on this document, and
        # populate the right-rail panel with matching cards.
        self._current_comments = {c.uuid: c for c in comments}
        for c in comments:
            self._editor.apply_comment_highlight(c.anchor_start, c.anchor_end, c.uuid)
        self._comments_panel.set_comments(comments)
        self._comments_dirty = False
        # Restore cursor if we remember one.
        cursors = self._ui_state.get("cursors") or {}
        pos = cursors.get(item.uuid)
        if isinstance(pos, int):
            self._editor.set_cursor_position(pos)
        self._editor.set_focus()
        self._update_word_count()

        # Remember this as last-selected.
        self._ui_state["last_selected_uuid"] = item.uuid

        # Apply (or clear) search overlays for the newly loaded doc.
        # ``_jump_to_first_match`` is one-shot: it's set when the user
        # activates a row in the search results and consumed here so a
        # later binder click lands on the saved cursor instead.
        self._refresh_search_highlights(jump_to_first=self._jump_to_first_match)
        self._jump_to_first_match = False

    # --- project-wide search ----------------------------------------

    def _action_find_in_project(self) -> None:
        """Reveal (creating if needed) the Search tab and focus its term field."""
        if self._search_panel is None:
            self._search_panel = SearchPanel(self)
            self._search_panel.query_changed.connect(self._on_search_query_changed)
            self._search_panel.result_activated.connect(self._on_search_result_activated)
            idx = self._left_tabs.addTab(self._search_panel, "Search")
        else:
            idx = self._left_tabs.indexOf(self._search_panel)
        self._left_tabs.setCurrentIndex(idx)
        self._search_panel.focus_term()

    def _on_left_tab_close(self, index: int) -> None:
        widget = self._left_tabs.widget(index)
        if widget is not self._search_panel or self._search_panel is None:
            return
        # Closing the Search tab terminates the search session entirely:
        # drop the active query, wipe overlays, and return focus to the
        # binder so the user keeps editing without the search context.
        self._left_tabs.removeTab(index)
        self._search_panel.deleteLater()
        self._search_panel = None
        self._search_query = None
        self._editor.clear_search_highlights()
        self._left_tabs.setCurrentIndex(0)

    def _on_search_query_changed(self, query: SearchQuery) -> None:
        # Flush the open editor before sweeping bodies so the in-flight
        # edits are visible to the search.
        self._flush_current_editor()
        self._search_query = query if query.is_active() else None
        self._populate_search_results()
        # Re-paint overlays on whatever doc is currently open. No jump:
        # the user is still editing the query, not asking to navigate.
        self._refresh_search_highlights(jump_to_first=False)

    def _on_search_result_activated(self, uuid: str) -> None:
        if self._project is None:
            return
        item = self._project.find(uuid)
        if item is None:
            return
        idx = self._model.index_for_item(item)
        if not idx.isValid():
            return
        # Expand ancestors so the binder reveals the row even though
        # we're staying on the Search tab. Binder selection still
        # happens behind the scenes — useful when the user toggles back.
        parent = idx.parent()
        while parent.isValid():
            self._binder_view.expand(parent)
            parent = parent.parent()
        self._jump_to_first_match = True
        self._binder_view.setCurrentIndex(idx)

    def _populate_search_results(self) -> None:
        if self._search_panel is None:
            return
        if self._project is None or self._search_query is None:
            self._search_panel.set_results([], 0)
            return

        query = self._search_query
        hits: list[tuple[str, str, int]] = []
        total = 0
        for item in self._project.walk():
            # Root containers are structural shells with no content of
            # their own — skip them but keep their descendants.
            if item.type.is_root_container:
                continue
            total += 1
            haystack = self._build_search_haystack(item, query)
            if matches_document(haystack, query):
                count = 0 if query.invert else count_matches(haystack, query)
                hits.append((item.uuid, item.title or "", count))
        self._search_panel.set_results(hits, total)

    def _build_search_haystack(self, item: BinderItem, query: SearchQuery) -> str:
        """Concatenate the fields the current scope says to search."""
        scope = query.scope
        parts: list[str] = []
        if scope in (SCOPE_ALL, SCOPE_TITLE):
            parts.append(item.title or "")
        if scope in (SCOPE_ALL, SCOPE_SYNOPSIS):
            parts.append(item.synopsis or "")
        if scope in (SCOPE_ALL, SCOPE_TEXT):
            parts.append(self._plain_body_for_item(item))
        return "\n".join(parts)

    def _plain_body_for_item(self, item: BinderItem) -> str:
        if item.type is not ItemType.TEXT:
            return ""
        # Prefer the live editor for the open doc so the user sees the
        # effect of un-flushed edits in their results immediately.
        if self._current_item is not None and item.uuid == self._current_item.uuid:
            return self._editor.plain_text()
        if self._project is None or self._project.path is None:
            return ""
        try:
            html = read_document_body(self._project.path, item.uuid)
        except Exception:  # noqa: BLE001 — a single bad doc shouldn't kill the search
            return ""
        return plain_text_from_html(html)

    def _refresh_search_highlights(self, *, jump_to_first: bool) -> None:
        """Re-paint cyan overlays in the editor for the active query.

        Called both when the query changes and after a binder selection
        load. ``jump_to_first`` is true only when the user just clicked
        a row in the search results, so the caret lands on the first
        hit; otherwise the saved cursor position is preserved.
        """
        query = self._search_query
        if (
            query is None
            or not query.is_active()
            or self._current_item is None
            or self._current_item.type.is_container
        ):
            self._editor.clear_search_highlights()
            return
        if query.invert:
            # In invert mode the listed docs *don't* contain the term —
            # nothing to paint, by definition.
            self._editor.clear_search_highlights()
            return
        text = self._editor.plain_text()
        ranges = find_match_ranges(text, query)
        self._editor.set_search_highlights(ranges)
        if jump_to_first and ranges:
            self._editor.reveal_position(ranges[0][0])
            self._editor.set_focus()

    # --- view mode / corkboard --------------------------------------

    def _set_view_mode(self, mode: str) -> None:
        if mode not in (VIEW_EDITOR, VIEW_CORKBOARD):
            return
        if mode == self._view_mode:
            return
        self._view_mode = mode
        self._settings.set(Keys.VIEW_MODE, mode)
        self._apply_view_for_current_item()

    def _apply_view_for_current_item(self) -> None:
        """Pick the right stack page based on selection + view mode.

        Text items always use the editor. Containers use the corkboard if
        view mode is ``corkboard``; otherwise the (blank, disabled) editor.
        """
        item = self._current_item
        if item is None or not item.type.is_container:
            self._center_stack.setCurrentWidget(self._editor)
            return
        if self._view_mode == VIEW_CORKBOARD:
            self._corkboard.setRootIndex(self._model.index_for_item(item))
            self._corkboard.clearSelection()
            self._center_stack.setCurrentWidget(self._corkboard)
        else:
            self._center_stack.setCurrentWidget(self._editor)

    def _on_corkboard_activated(self, index) -> None:
        if not index.isValid():
            return
        # Setting the binder selection routes through _on_binder_selection,
        # which opens texts in the editor or descends folders in the corkboard.
        self._binder_view.setCurrentIndex(index)

    def _on_corkboard_context_menu(self, index, global_pos: QPoint) -> None:
        menu = QMenu(self)

        add_menu = menu.addMenu("Add Item")
        act_new_text = add_menu.addAction("New Text")
        act_new_text.triggered.connect(lambda: self._corkboard_add(ItemType.TEXT))
        act_new_folder = add_menu.addAction("New Folder")
        act_new_folder.triggered.connect(lambda: self._corkboard_add(ItemType.FOLDER))

        if index.isValid():
            menu.addSeparator()
            act_open = menu.addAction("Open")
            act_open.triggered.connect(lambda: self._on_corkboard_activated(index))
            act_reveal = menu.addAction("Reveal in Binder")
            act_reveal.triggered.connect(lambda: self._corkboard_reveal_in_binder(index))

            menu.addSeparator()
            act_rename = menu.addAction("Rename")
            act_rename.triggered.connect(lambda: self._corkboard.edit(index))

            menu.addSeparator()
            act_trash = menu.addAction("Move to Trash")
            act_trash.triggered.connect(lambda: self._on_delete_requested(index))

        menu.exec(global_pos)

    def _corkboard_add(self, item_type: ItemType) -> None:
        parent_idx = self._corkboard.rootIndex()
        if not parent_idx.isValid():
            return
        new_idx = self._model.add_item(parent_idx, item_type)
        if new_idx.isValid():
            self._corkboard.setCurrentIndex(new_idx)
            self._corkboard.edit(new_idx)

    def _corkboard_reveal_in_binder(self, index) -> None:
        if not index.isValid():
            return
        parent = index.parent()
        while parent.isValid():
            self._binder_view.expand(parent)
            parent = parent.parent()
        self._binder_view.setCurrentIndex(index)
        self._binder_view.scrollTo(index)
        self._binder_view.setFocus()

    def _on_editor_changed(self) -> None:
        self._dirty_editor = True
        self._update_word_count()
        if self._current_comments:
            present = set(self._editor.scan_comment_ranges().keys())
            vanished = [cid for cid in list(self._current_comments) if cid not in present]
            for cid in vanished:
                self._current_comments.pop(cid, None)
                self._comments_panel.remove_comment(cid)
            if vanished:
                self._comments_dirty = True

    def _on_comment_anchor_requested(self, comment_id: str) -> None:
        self._comments_panel.highlight_comment(comment_id)

    def _on_comment_body_changed(self, comment_id: str, body: str) -> None:
        c = self._current_comments.get(comment_id)
        if c is None or c.body == body:
            return
        c.body = body
        c.touch()
        self._comments_dirty = True

    def _on_comment_delete_requested(self, comment_id: str) -> None:
        c = self._current_comments.pop(comment_id, None)
        if c is None:
            return
        self._editor.remove_comment_highlight(comment_id)
        self._comments_panel.remove_comment(comment_id)
        self._comments_dirty = True

    def _on_comment_selected(self, comment_id: str) -> None:
        self._editor.select_comment_range(comment_id)

    def _on_synopsis_changed(self, text: str) -> None:
        if self._current_item is None:
            return
        self._current_item.synopsis = text
        self._current_item.touch()
        if self._project is not None:
            self._project.touch()
        # Refresh any view (e.g. the corkboard) showing this item's synopsis.
        self._model.notify_item_changed(self._current_item)

    def _on_include_changed(self, value: bool) -> None:
        if self._current_item is None:
            return
        self._current_item.metadata["include_in_compile"] = bool(value)
        self._current_item.touch()
        if self._project is not None:
            self._project.touch()

    def _flush_current_editor(self) -> None:
        if (
            self._current_item is None
            or self._project is None
            or self._project.path is None
            or self._current_item.type.is_container
        ):
            self._dirty_editor = False
            self._comments_dirty = False
            return
        bundle = self._project.path
        uuid = self._current_item.uuid
        if self._dirty_editor:
            write_document_body(bundle, uuid, self._editor.html())
            self._current_item.touch()
            self._project.touch()
        # If anything changed that could have moved anchors, rescan positions
        # and persist. Comments whose highlight has been fully deleted are
        # dropped (no range in the document → no anchor to save).
        if self._comments_dirty or (self._dirty_editor and self._current_comments):
            ranges = self._editor.scan_comment_ranges()
            kept: list[Comment] = []
            for cid, c in list(self._current_comments.items()):
                hit = ranges.get(cid)
                if hit is None:
                    # Whole highlight deleted by the user — drop the card too.
                    self._comments_panel.remove_comment(cid)
                    continue
                start, end = hit
                if (start, end) != (c.anchor_start, c.anchor_end):
                    c.anchor_start = start
                    c.anchor_end = end
                    c.touch()
                kept.append(c)
            self._current_comments = {c.uuid: c for c in kept}
            write_comments(bundle, uuid, kept)
        self._dirty_editor = False
        self._comments_dirty = False

    def _record_current_cursor(self) -> None:
        if self._current_item is None or self._current_item.type.is_container:
            return
        cursors = self._ui_state.setdefault("cursors", {})
        cursors[self._current_item.uuid] = int(self._editor.cursor_position())

    def _persist_ui_state(self) -> None:
        if self._project is None or self._project.path is None:
            return
        self._record_current_cursor()
        try:
            write_ui_state(self._project.path, self._ui_state)
        except OSError:
            pass

    def _update_word_count(self) -> None:
        if self._current_item is None or self._current_item.type.is_container:
            self._word_count_label.setText("")
            return
        total = self._editor.word_count()
        if self._editor.has_selection():
            selected = self._editor.selection_word_count()
            self._word_count_label.setText(f"Selected: {selected} words (of {total})")
        else:
            self._word_count_label.setText(f"{total} words")

    # --- add/delete ---------------------------------------------------

    def _add_under_current(self, item_type: ItemType) -> None:
        self._on_add_requested(self._binder_view.currentIndex(), item_type)

    def _on_add_requested(self, parent_index, item_type: ItemType) -> None:
        new_idx = self._model.add_item(parent_index, item_type)
        if new_idx.isValid():
            self._binder_view.setCurrentIndex(new_idx)
            self._binder_view.edit(new_idx)

    def _on_delete_requested(self, index) -> None:
        item = self._model.item_from_index(index)
        if item is None:
            return
        if item is self._current_item:
            self._current_item = None
            self._editor.clear()
            self._inspector.set_item(None)
        self._model.remove_item(index)

    # --- window state / close handling -------------------------------

    def _confirm_discard(self) -> bool:
        if self._project is None:
            return True
        self._flush_current_editor()
        self._persist_ui_state()
        return True

    def closeEvent(self, event) -> None:
        self._flush_current_editor()
        if self._project is not None:
            try:
                save_project(self._project)
            except Exception:  # noqa: BLE001
                pass
            self._persist_ui_state()
        self._save_window_state()
        super().closeEvent(event)

    def _restore_window_state(self) -> None:
        s = QSettings(APP_ORG, APP_NAME)
        geom = s.value(Keys.MAIN_GEOMETRY)
        if geom:
            self.restoreGeometry(geom)
        state = s.value(Keys.MAIN_STATE)
        if state:
            self.restoreState(state)

    def _save_window_state(self) -> None:
        s = QSettings(APP_ORG, APP_NAME)
        s.setValue(Keys.MAIN_GEOMETRY, self.saveGeometry())
        s.setValue(Keys.MAIN_STATE, self.saveState())
