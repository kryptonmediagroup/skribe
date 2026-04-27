"""Shared document-conversion machinery.

Wraps ``subprocess.run(["pandoc", ...])`` with consistent timeout, logging,
and error handling. Consumed by the whole-project ``.scriv`` importer and,
once they land, by per-document import/export.

Public surface:
- :func:`has_pandoc` — probe for the external binary.
- :func:`run_pandoc` — generic converter; returns stdout text or ``None``.
- :func:`rtf_to_html_pandoc` — RTF → HTML5 body fragment (or ``None``).
- :func:`rtf_to_html_fallback` — text-only striprtf path for when pandoc is
  unavailable or rejects the input.
"""
from __future__ import annotations

import html
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

from striprtf.striprtf import rtf_to_text

log = logging.getLogger(__name__)
_PANDOC = shutil.which("pandoc")
_SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def has_pandoc() -> bool:
    return _PANDOC is not None


def has_soffice() -> bool:
    return _SOFFICE is not None


def soffice_convert(src: Path, out_fmt: str, out_dir: Optional[Path] = None, timeout: float = 60.0) -> Optional[Path]:
    """Run ``soffice --headless --convert-to <out_fmt>`` and return the output path.

    Bridges formats pandoc can't handle (classic ``.doc`` in either direction).
    Returns ``None`` if soffice is missing, the process fails, or the expected
    output file can't be located.
    """
    if _SOFFICE is None:
        return None
    src = Path(src)
    if not src.is_file():
        return None
    out_dir = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="skribe-soffice-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [_SOFFICE, "--headless", "--convert-to", out_fmt, "--outdir", str(out_dir), str(src)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("soffice failed: %s", exc)
        return None
    if proc.returncode != 0:
        log.warning(
            "soffice returned %d: %s",
            proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip(),
        )
        return None
    # soffice names the output <stem>.<out_fmt_ext>. The extension is the first
    # token of out_fmt (e.g. "docx:MS Word 2007 XML" → "docx").
    ext = out_fmt.split(":", 1)[0].strip()
    candidate = out_dir / f"{src.stem}.{ext}"
    if candidate.is_file():
        return candidate
    # Some soffice builds pick a different suffix; scan the outdir as a fallback.
    matches = sorted(out_dir.glob(f"{src.stem}.*"))
    return matches[0] if matches else None


def run_pandoc_bytes(
    payload: Union[str, bytes],
    from_fmt: str,
    to_fmt: str,
    extra_args: tuple[str, ...] = (),
    timeout: float = 30.0,
) -> Optional[bytes]:
    """Invoke pandoc and return stdout bytes, or ``None`` on failure.

    ``None`` covers pandoc missing, timeout, OS error, or non-zero exit.
    Keep output as bytes so binary formats (DOCX, ODT) aren't corrupted;
    text callers decode at their layer via :func:`run_pandoc`.
    """
    if _PANDOC is None:
        return None
    cmd = [_PANDOC, "-f", from_fmt, "-t", to_fmt, "--wrap=none", *extra_args]
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    try:
        proc = subprocess.run(
            cmd,
            input=payload_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("pandoc failed: %s", exc)
        return None
    if proc.returncode != 0:
        log.warning(
            "pandoc returned %d: %s",
            proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip(),
        )
        return None
    return proc.stdout


def run_pandoc(
    payload: Union[str, bytes],
    from_fmt: str,
    to_fmt: str,
    extra_args: tuple[str, ...] = (),
    timeout: float = 30.0,
) -> Optional[str]:
    """Invoke pandoc for a text-producing conversion. Wraps :func:`run_pandoc_bytes`."""
    raw = run_pandoc_bytes(payload, from_fmt, to_fmt, extra_args, timeout)
    if raw is None:
        return None
    return raw.decode("utf-8", "replace")


def rtf_to_html_pandoc(rtf_text: str) -> Optional[str]:
    """Convert RTF text to a wrapped HTML5 document. ``None`` on failure."""
    out = run_pandoc(rtf_text, "rtf", "html5")
    if out is None:
        return None
    body = out.strip()
    if not body:
        return ""
    return f"<!DOCTYPE html><html><body>{body}</body></html>"


def rtf_to_html_fallback(rtf_text: str) -> str:
    """Text-only conversion via striprtf; preserves paragraph breaks only."""
    text = rtf_to_text(rtf_text, errors="ignore") if rtf_text else ""
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n")]
    body = "".join(
        f"<p>{html.escape(p)}</p>" if p.strip() else "<p><br/></p>"
        for p in paragraphs
    )
    if not body:
        body = "<p></p>"
    return f"<!DOCTYPE html><html><body>{body}</body></html>"
