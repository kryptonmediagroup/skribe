# Import/Export Plan

Scope for this round: per-document **import** and **export** for TXT, RTF, DOCX, ODT, DOC, plus whole-project **export** to `.scriv`. FDX deferred to the screenplay phase. Whole-project `.scriv` import already exists in `skribe/ioformat/scriv_import.py`.

## Shared machinery

One new module, `skribe/ioformat/doc_convert.py`, holds:

- A `PandocConverter` class that wraps `subprocess.run(["pandoc", ...])` with the same input/error conventions as the existing RTF importer. Accepts bytes or a path, returns HTML for import or bytes for export. One place for the `pandoc` probe, `--wrap=none`, timeout, logging.
- A `Format` enum: `TXT`, `RTF`, `DOCX`, `ODT`, `DOC`. Each entry carries its pandoc format name, file extension(s), and whether export is supported. (DOC is likely import-only: pandoc can read it but can't write it — old binary format. We don't need to write DOC; nobody targets it as an output.)
- A `detect_format(path)` helper based on file extension.

## Per-document import

One entry point, `import_document(path) -> (title, html)`:

- **TXT** — read file, wrap each line as `<p>`. No pandoc.
- **RTF** — reuse the `_preprocess_scriv_rtf` + pandoc path we already have (extract the shared code from `scriv_import.py` into `doc_convert.py`).
- **DOCX / ODT / DOC** — pandoc, format auto-detected.

Title defaults to the filename stem; user can rename in the binder.

UI: extend File menu with "Import Document…" that opens a file picker filtered to the five extensions and inserts the result as a new TEXT item under the currently-selected folder (or Draft root if none selected).

## Per-document export

One entry point, `export_document(item, path, format)`:

- Source is the document's HTML (from `write_document_body` storage).
- **TXT** — strip HTML to plain text via `QTextDocument.toPlainText()`.
- **RTF / DOCX / ODT** — pandoc, HTML input → target format.
- **DOC** — pandoc writes DOCX, then LibreOffice headless converts DOCX → DOC:
  `soffice --headless --convert-to doc --outdir <tmp> <intermediate>.docx`. LibreOffice 25.8 is already on the target machine (`/usr/bin/soffice`). Probe for it at startup the same way we probe for pandoc; if missing, DOC export is disabled with a clear message.

UI: "Export Document…" on the selected item, with a format dropdown in the file-save dialog.

**Comments on export**: preserve as RTF annotation destination groups so Scrivener (and other RTF-aware editors) can see them on the other side. Scrivener uses `{\*\Scrv_annot ...}` for its inline annotations and `{\*\Scrv_cmnt ...}` for linked comments — we target `\Scrv_cmnt` since that matches our data model (anchored range + body). Pandoc's HTML → RTF pass doesn't emit these, so we post-process the RTF output: walk the document, find each anchored range by its `COMMENT_ID_PROP`, inject the annotation group at the right offset. For non-RTF exports (DOCX/ODT), use the format's native comment feature; for TXT, strip silently.

## Whole-project `.scriv` export

The big one. A new module `skribe/ioformat/scriv_export.py` that writes a Scrivener 3–compatible bundle:

- `<ProjectName>.scrivx` — XML with `<Binder>` mirroring our tree. Each `<BinderItem>` carries `UUID` (preserved from import where possible), `Type`, `Created`, `Modified`, and `<Title>`.
- `Files/Data/<UUID>/content.rtf` — pandoc HTML → RTF per document.
- `Files/Data/<UUID>/synopsis.txt` — plain text.
- `Files/Version.txt`, `Settings/`, `QuickLook/` — minimal placeholders Scrivener tolerates.

**UUID preservation rule**: when we imported a `.scriv`, we kept the original UUIDs on each `BinderItem` (confirmed in `scriv_import.py:172` — `uuid=uuid or BinderItem().uuid`). On export, we use those same UUIDs so a Scrivener → Skribe → Scrivener round-trip doesn't fork identity. For documents that originated in Skribe (no prior UUID), we generate fresh ones.

Metadata round-trip: we currently stash unrecognized scriv metadata in `BinderItem.metadata["_raw"]`. On export, write those fields back verbatim into `<MetaData>` so Label/Status/Keywords/CustomMetaData survive the trip even though Skribe doesn't surface them in UI yet.

UI: "Export to Scrivener…" in the File menu, prompts for target directory, writes `<Project Name>.scriv/` alongside.

## Order of work

1. Extract shared pandoc wrapper into `doc_convert.py`, refactor `scriv_import.py` to use it. No new features — just the refactor, verified by re-importing the existing sample `.scriv`.
2. Per-document **import** — TXT, RTF, DOCX, ODT, DOC. Test each against `sampleDocuments/`.
3. Per-document **export** — TXT, RTF, DOCX, ODT. Round-trip test: import sample → export → diff.
4. `.scriv` export. Round-trip test: import sample `.scriv` → export → open in Scrivener 3 on the Windows box.

Each step is independently mergeable.

## Decisions (locked)

- `.scriv` round-trip verification: user tests manually in Scrivener 3 on Windows.
- `.doc` export: supported via LibreOffice-headless fallback (pandoc → DOCX → `soffice --convert-to doc`).
- Comments on export: preserved as RTF annotation destinations (`{\*\Scrv_cmnt ...}`) for RTF/`.scriv`, native comments for DOCX/ODT, stripped for TXT.
