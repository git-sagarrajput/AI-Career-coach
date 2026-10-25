from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)


def _windows_install_dirs() -> list[Path]:
    roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]
    dirs = [Path(r) / "Tesseract-OCR" for r in roots if r]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs += [Path(local) / "Programs" / "Tesseract-OCR", Path(local) / "Tesseract-OCR"]
    return dirs


@lru_cache(maxsize=1)
def find_tessdata() -> str | None:
    """Locate Tesseract language data: TESSDATA_PREFIX, tesseract on PATH, or a default Windows install."""
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        return prefix
    candidates: list[Path] = []
    exe = shutil.which("tesseract")
    if exe:
        candidates.append(Path(exe).resolve().parent / "tessdata")
    if sys.platform == "win32":
        candidates += [d / "tessdata" for d in _windows_install_dirs()]
    for candidate in candidates:
        if (candidate / "eng.traineddata").is_file():
            return str(candidate)
    if exe:
        try:
            return pymupdf.get_tessdata()
        except Exception:  # noqa: BLE001
            return None
    return None


@lru_cache(maxsize=1)
def ocr_available() -> bool:
    """Checked once per process: a usable Tesseract language folder was found."""
    return find_tessdata() is not None


def ocr_page_blocks(page: pymupdf.Page) -> list[str]:
    """Run local Tesseract OCR on one page via PyMuPDF and return text blocks in reading order."""
    textpage = page.get_textpage_ocr(language="eng", dpi=200, full=True, tessdata=find_tessdata())
    blocks = page.get_text("blocks", textpage=textpage, sort=True)
    return [b[4] for b in blocks if len(b) > 6 and b[6] == 0 and b[4].strip()]
