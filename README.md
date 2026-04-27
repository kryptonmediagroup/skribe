# Skribe

A personal, cross-platform writing environment in the spirit of Scrivener 3. Built with Python 3 and PySide6 (Qt 6), so it runs on Linux, macOS, Windows, and the Raspberry Pi 5.

Skribe is deliberately a small subset of Scrivener — the features one writer actually reaches for, day in and day out, with none of the screenplay/teleplay, format-preset, or section-layout machinery. When something needs that level of polish, the **Export to Scrivener** path round-trips the project so the heavy lifting can happen there.

## Capabilities at a glance

### Project structure
- Three-pane layout: **binder** on the left, **editor** in the middle, **inspector + comments** on the right.
- Standard binder containers — *Manuscript*, *Research*, *Trash* — plus arbitrary nested folders and text items.
- Drag-and-drop reorder/reparent inside the binder.
- **Corkboard** view for any folder (Ctrl+2), showing children as synopsis cards.

### Rich-text editor
- Headings, bold/italic/underline, bulleted/numbered lists, alignment.
- Configurable default font, first-line paragraph indent, and paragraph spacing.
- Auto-indent: every new paragraph inherits the configured indent.
- Per-document cursor position is saved on every project switch.

### Spell-check
- Inline spell-check via pyenchant + hunspell (red-underline highlighter).
- Right-click for suggestions, *Add to Dictionary*, and *Ignore*.
- Per-user personal dictionary; language is configurable.

### Comments
- Anchored, ranged comments with author/initials, body text, and a yellow highlight.
- Right-rail comments panel is in lockstep with the editor; deleting the highlight deletes the card.
- DOCX export carries comments as native Word annotations; RTF export uses standard RTF annotation groups.

### Project-wide search (Ctrl+Shift+F)
- Search tab in the binder area with a magnifier shortcut in the corner of the tab bar.
- **Scope:** All / Title / Text / Synopsis.
- **Operator:** Any Word / All Words / Exact Phrase / Whole Word / RegEx.
- **Options:** Match case, Invert results.
- Results list shows match counts; clicking a result opens the document with the cursor on the first hit and every match overlaid in cyan via QTextEdit ExtraSelections (the document is never modified — closing the search tab clears the overlay).

### Compile (Ctrl+Shift+E)
A "Compile Overview"-style dialog that walks the Manuscript subtree, concatenates each item with its title as an `<h1>` and CSS page breaks between chapters, then dispatches the resulting HTML to:
- **Print** and **PDF** via Qt's QPrinter.
- **Web Page (.html)** as a direct write.
- **Plain Text (.txt)** via Qt's plain-text projection.
- **Rich Text (.rtf)**, **DOCX**, **ODT**, **ePub 2 Ebook**, **ePub 3 Ebook** via pandoc.

Optional title-page front matter (Title / Subtitle / Author). Per-item *Include in Compile* checkboxes persist back to the project.

### Per-document import/export
- **Import:** Markdown, HTML, plain text, DOCX, ODT, RTF (via pandoc), and legacy `.doc` (via LibreOffice).
- **Export:** TXT, RTF, DOCX, ODT, and legacy DOC.

### Scrivener round-trip
- **Import** any Scrivener 3 `.scriv` bundle (Windows or Mac), preserving the binder tree, UUIDs, synopses, IncludeInCompile / Label / Status metadata, and document bodies (RTF → HTML via pandoc, with a striprtf fallback).
- **Export** any Skribe project back out to a Scrivener 3-compatible `.scriv` bundle. Bodies render as standalone RTF documents Scrivener reads correctly; comments are emitted as standard RTF annotation groups.

### Statistics
Word count, character count (with and without spaces), paragraphs, sentences, document count, longest/shortest/average document, paperback page estimate, and reading-time projection — for the whole project, current selection, or any subtree.

### Themes & settings
- Light and dark themes that propagate to the editor palette.
- Persistent settings (recent projects, last-opened, fonts, indents, theme, spellcheck language, etc.).
- First-run dialog establishes baseline preferences; the Preferences dialog mirrors them afterward.

## Requirements

- **Python 3.11+**
- **PySide6** ≥ 6.7
- **lxml**, **striprtf**, **pyenchant** (see `requirements.txt`)
- **pandoc** on the system `PATH` for DOCX/ODT/RTF/EPUB conversion (Print, PDF, HTML, and TXT compile work without it).
- **LibreOffice** (`soffice`) for legacy `.doc` import/export.
- **hunspell** dictionaries for spell-check (e.g., `hunspell-en-us`).

## Installation

```bash
# Create a venv at the location run.sh expects
python3 -m venv "$HOME/skribe/.venv"
"$HOME/skribe/.venv/bin/pip" install -r requirements.txt
```

Override with `SKRIBE_VENV=/path/to/venv` if you'd rather keep the venv elsewhere.

## Running

```bash
./run.sh
```

`run.sh` simply execs `python -m skribe` from the configured venv.

## Project layout

```
skribe/
├── app.py                  # entry point; sets up QApplication and icon
├── main_window.py          # three-pane shell, menus, action wiring
├── settings.py             # QSettings-backed preferences
├── themes.py               # light/dark palettes
├── stats.py                # word/sentence/page metrics
├── spellcheck.py           # pyenchant wrapper
├── model/                  # Project, BinderItem, BinderModel, Comment
├── ioformat/               # all import/export pipelines
│   ├── doc_convert.py      # pandoc / soffice wrappers
│   ├── doc_import.py       # per-document import
│   ├── doc_export.py       # per-document export (TXT/RTF/DOCX/ODT/DOC)
│   ├── compile_export.py   # whole-project compile pipeline
│   ├── scriv_import.py     # .scriv → .skribe importer
│   ├── scriv_export.py     # .skribe → .scriv exporter
│   └── skribe_io.py        # the .skribe bundle on-disk format
├── ui/                     # Qt widgets and dialogs
│   ├── editor.py           # rich-text editor + format toolbar
│   ├── binder_view.py      # left-rail tree
│   ├── corkboard_view.py   # synopsis-card grid
│   ├── inspector.py        # synopsis + Include-in-Compile
│   ├── comments_panel.py   # right-rail comment cards
│   ├── search_panel.py     # project-wide search tab
│   ├── compile_dialog.py   # "Compile Overview" dialog
│   ├── statistics.py       # stats dialog
│   ├── preferences.py      # preferences dialog
│   ├── first_run.py        # first-run setup
│   └── spell_highlighter.py
└── resources/icons/
    └── skribe.svg          # application icon
```

## Status

Skribe is built for one writer's workflow on his own machines. There's no test suite, no plugin API, no release schedule. Bugs get fixed when they get in the way of writing.
