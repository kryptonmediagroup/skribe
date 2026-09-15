"""Native .skribe project bundle I/O.

Bundle layout::

    MyProject.skribe/
    ├── project.json            # manifest + binder tree (no document bodies)
    ├── project.json.bak        # previous manifest (backup)
    ├── documents/
    │   └── {UUID}/
    │       └── content.html    # rich-text body (QTextDocument HTML)
    ├── snapshots/              # reserved
    └── settings/               # reserved
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from skribe.model.comment import Comment, comments_from_list, comments_to_list
from skribe.model.project import Project

PROJECT_FILE = "project.json"
PROJECT_BACKUP = "project.json.bak"
PROJECT_TMP = "project.json.tmp"
UI_STATE_FILE = "ui_state.json"
DOCS_DIR = "documents"
BODY_FILE = "content.html"
COMMENTS_FILE = "comments.json"


def project_paths(bundle: Path) -> dict[str, Path]:
    return {
        "manifest": bundle / PROJECT_FILE,
        "docs": bundle / DOCS_DIR,
        "snapshots": bundle / "snapshots",
        "settings": bundle / "settings",
    }


def document_body_path(bundle: Path, uuid: str) -> Path:
    return bundle / DOCS_DIR / uuid / BODY_FILE


def is_skribe_bundle(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (
        (path / PROJECT_FILE).is_file()
        or (path / PROJECT_BACKUP).is_file()
        or (path / PROJECT_TMP).is_file()
    )


def ensure_bundle_layout(bundle: Path) -> None:
    paths = project_paths(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    paths["docs"].mkdir(exist_ok=True)
    paths["snapshots"].mkdir(exist_ok=True)
    paths["settings"].mkdir(exist_ok=True)


def _atomic_write_text(target: Path, data: str) -> None:
    """Write *data* atomically with fsync.

    Writes to a ``.tmp`` file next to *target* using a low-level file
    descriptor, fsyncs the data (and, on POSIX, the directory entry), then
    ``os.replace``\\s into place. The temp file lives on the same filesystem
    as target, so the rename is atomic. Fsync reduces the chance of
    partially written data surviving an unmount/power loss on flash media.

    Directory fsync is a POSIX concept — Windows has no equivalent (you
    cannot open a directory with ``os.open``, and there's no ``O_DIRECTORY``
    flag), so that step is skipped there. The file-data fsync and the
    atomic rename are what matter and both work on every platform.
    """
    tmp = target.with_name(target.name + ".tmp")
    # Write to a file descriptor so we can fsync reliably.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Flush the directory entry too — POSIX only. Windows doesn't
        # support opening a directory as a file descriptor at all.
        dir_flag = getattr(os, "O_DIRECTORY", None)
        if dir_flag is not None:
            try:
                dir_fd = os.open(target.parent, dir_flag)
            except OSError:
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
    except BaseException:
        # Best effort cleanup on failure.
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # Atomic rename on same filesystem. On Windows, os.replace() already
    # handles replacing an existing destination file (unlike os.rename()).
    os.replace(tmp, target)


def save_project(project: Project, path: Optional[Path] = None) -> Path:
    """Persist the project manifest with backup rotation.

    Write order: .tmp → rename old → .bak → rename .tmp → project.json.
    If interrupted at any point, at least one of project.json or
    project.json.bak will survive intact.
    """
    bundle = Path(path) if path is not None else project.path
    if bundle is None:
        raise ValueError("No path given and project has no path set")
    ensure_bundle_layout(bundle)
    project.touch()
    manifest = bundle / PROJECT_FILE
    backup = bundle / PROJECT_BACKUP
    # Serialize manifest.
    data = json.dumps(project.to_dict(), indent=2, ensure_ascii=False)
    # Rotate existing manifest to .bak before overwriting.
    if manifest.is_file():
        shutil.copy2(manifest, backup)
    # Atomic write with fsync.
    _atomic_write_text(manifest, data)
    project.path = bundle
    return bundle


def _read_manifest(bundle: Path) -> dict:
    """Try project.json, then .bak, then .tmp. Raises FileNotFoundError."""
    for name in (PROJECT_FILE, PROJECT_BACKUP, PROJECT_TMP):
        candidate = bundle / name
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8")
                if text.strip():
                    return json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # corrupt — try next fallback
    raise FileNotFoundError(
        f"No valid project manifest in {bundle} "
        f"(checked {PROJECT_FILE}, {PROJECT_BACKUP}, {PROJECT_TMP})"
    )


def load_project(path: Path) -> Project:
    bundle = Path(path)
    if not is_skribe_bundle(bundle):
        raise FileNotFoundError(f"Not a Skribe bundle: {bundle}")
    data = _read_manifest(bundle)
    return Project.from_dict(data, path=bundle)


def read_document_body(bundle: Path, uuid: str) -> str:
    """Return the raw HTML body, or an empty string if the document has none yet."""
    body = document_body_path(bundle, uuid)
    if not body.is_file():
        return ""
    return body.read_text(encoding="utf-8")


def write_document_body(bundle: Path, uuid: str, html: str) -> None:
    body = document_body_path(bundle, uuid)
    body.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(body, html)

def copy_document_body(bundle: Path, src_uuid: str, dst_uuid: str) -> None:
    """Copy a document's on-disk artifacts (body + comments) under a new UUID.

    Used by the binder's "Copy To" action: after the model has cloned a
    BinderItem subtree with fresh UUIDs, every TEXT node in the clone needs
    its body file relocated to the new UUID so the editor can load it.
    Missing source files are silently ignored — a brand-new document that
    was never saved has nothing to copy.
    """
    src_body = document_body_path(bundle, src_uuid)
    dst_body = document_body_path(bundle, dst_uuid)
    if src_body.is_file():
        dst_body.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_body, dst_body)
    src_comments = comments_path(bundle, src_uuid)
    dst_comments = comments_path(bundle, dst_uuid)
    if src_comments.is_file():
        dst_comments.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_comments, dst_comments)


def comments_path(bundle: Path, uuid: str) -> Path:
    return bundle / DOCS_DIR / uuid / COMMENTS_FILE


def read_comments(bundle: Path, uuid: str) -> list[Comment]:
    p = comments_path(bundle, uuid)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("comments", [])
    if not isinstance(data, list):
        return []
    return comments_from_list(data)


def write_comments(bundle: Path, uuid: str, comments: list[Comment]) -> None:
    p = comments_path(bundle, uuid)
    if not comments:
        # Clean up an empty file rather than leaving an empty array behind.
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "comments": comments_to_list(comments)}
    data = json.dumps(payload, indent=2, ensure_ascii=False)
    _atomic_write_text(p, data)


def delete_document_body(bundle: Path, uuid: str) -> None:
    body_dir = bundle / DOCS_DIR / uuid
    if body_dir.is_dir():
        for p in body_dir.iterdir():
            p.unlink()
        body_dir.rmdir()


# --- per-project UI state -------------------------------------------------

_UI_STATE_VERSION = 1


def read_ui_state(bundle: Path) -> dict:
    """Return the bundle's ui_state.json as a dict (empty if missing/invalid)."""
    p = Path(bundle) / UI_STATE_FILE
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def write_ui_state(bundle: Path, state: dict) -> None:
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    payload = {"version": _UI_STATE_VERSION, **state}
    p = bundle / UI_STATE_FILE
    data = json.dumps(payload, indent=2, ensure_ascii=False)
    _atomic_write_text(p, data)
