"""Application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from skribe.main_window import MainWindow
from skribe.settings import Keys, app_settings
from skribe.themes import apply_theme, theme_for
from skribe.ui.first_run import maybe_run_first_run

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "skribe.svg"


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Skribe")
    app.setOrganizationName("Skribe")
    if ICON_PATH.is_file():
        # Setting the icon on QApplication propagates to every top-level
        # window — covers the title bar, dock/taskbar, and Alt-Tab.
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Offer the first-run setup before we build the main window so any
    # chosen defaults (theme, font, indent…) are already in place when
    # the editor is constructed.
    maybe_run_first_run()

    # Apply saved theme before building the main window so the chrome
    # comes up in the right palette from the first frame.
    apply_theme(theme_for(str(app_settings().get(Keys.THEME))))

    window = MainWindow()
    window.show()
    window.maybe_reopen_last_project()
    return app.exec()
