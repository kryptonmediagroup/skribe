"""Native .skribe project bundle I/O.

Bundle layout::

    MyProject.skribe/
    ├── project.json            # manifest + binder tree (no document bodies)
    ├── documents/
    │   └── {UUID}/
    │       └── content.html    # rich-text body (QTextDocument HTML)
    ├── snapshots/              # reserved
    └── settings/               # reserved
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from skribe.model.comment import Comment, comments_from_list, comments_to_list
from skribe.model.project import Project

PROJECT_FILE = "project.json"
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
    return path.is_dir() and (path / PROJECT_FILE).is_file()


def ensure_bundle_layout(bundle: Path) -> None:
    paths = project_paths(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    paths["docs"].mkdir(exist_ok=True)
    paths["snapshots"].mkdir(exist_ok=True)
    paths["settings"].mkdir(exist_ok=True)


def save_project(project: Project, path: Optional[Path] = None) -> Path:
    """Persist the project manifest. Body files are written by the editor."""
    bundle = Path(path) if path is not None else project.path
    if bundle is None:
        raise ValueError("No path given and project has no path set")
    ensure_bundle_layout(bundle)
    project.touch()
    manifest = bundle / PROJECT_FILE
    tmp = manifest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(manifest)
    project.path = bundle
    return bundle


def load_project(path: Path) -> Project:
    bundle = Path(path)
    if not is_skribe_bundle(bundle):
        raise FileNotFoundError(f"Not a Skribe bundle: {bundle}")
    data = json.loads((bundle / PROJECT_FILE).read_text(encoding="utf-8"))
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
    tmp = body.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(body)


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
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(p)


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
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(p)
