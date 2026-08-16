"""Automated project backups.

A backup is a *standard* .skribe bundle — a full recursive copy of the
live project directory — written into a user-chosen destination folder
and named ``<project name>.skribe``. Because it is an ordinary bundle,
the user can open it in Skribe directly with no import step.

The copy is staged into a temporary sibling directory inside the
destination and then swapped into place, so a crash or power loss
midway through a backup can never leave a half-written bundle at the
final path (the failure mode that corrupted a live project once).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from skribe.model.project import Project

# Characters that are illegal in file names on Windows (and awkward on
# any platform). Replaced with '_' when deriving the backup folder name.
_ILLEGAL = '<>:"/\\|?*'


def _safe_name(name: str) -> str:
    """Return a filesystem-safe folder stem for ``name``.

    Strips illegal characters and trailing dots/spaces (which Windows
    rejects), and falls back to a default when nothing usable remains.
    """
    cleaned = "".join("_" if c in _ILLEGAL else c for c in (name or "")).strip()
    # Windows disallows names ending in '.' or ' '.
    cleaned = cleaned.rstrip(". ")
    return cleaned or "Skribe Project"


def backup_folder_name(project_name: str) -> str:
    """Public helper: the ``.skribe`` folder name a backup will use."""
    return f"{_safe_name(project_name)}.skribe"


def backup_project(project: Project, dest_dir: Path) -> Path:
    """Copy ``project``'s bundle into ``dest_dir`` as a standard .skribe.

    Returns the path of the written backup bundle. Raises on failure
    (caller decides whether to surface or swallow the error).

    The destination folder is created if it does not exist. An existing
    backup with the same name is replaced atomically: the fresh copy is
    staged in a temp directory first, the old backup is moved aside, the
    new one is swapped in, and only then is the old copy removed. If any
    step fails, the previous backup is left intact.
    """
    src = project.path
    if src is None:
        raise ValueError("Project has no path; save it before backing up.")
    src = Path(src)
    if not src.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {src}")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    final = dest_dir / backup_folder_name(project.name)

    # Refuse to back a project up onto itself.
    if final.resolve() == src.resolve():
        raise ValueError("Backup destination is the project itself.")

    # Stage the copy in a temp dir inside dest_dir so the final swap is a
    # same-filesystem rename (fast and atomic where the OS allows).
    staging = Path(tempfile.mkdtemp(prefix=".skribe-backup-", dir=dest_dir))
    staged_bundle = staging / final.name
    try:
        shutil.copytree(src, staged_bundle)

        # Swap: move any existing backup aside, move the new one in,
        # then delete the old one. dirs_exist_ok is avoided so a partial
        # old copy can never bleed into the new one.
        old_aside: Optional[Path] = None
        if final.exists():
            old_aside = staging / (final.name + ".old")
            os.replace(final, old_aside)
        os.replace(staged_bundle, final)
        if old_aside is not None and old_aside.exists():
            shutil.rmtree(old_aside, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return final
