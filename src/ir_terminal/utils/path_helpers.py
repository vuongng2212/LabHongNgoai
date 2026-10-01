"""Filesystem and asset resolution helpers supporting PyInstaller bundles."""

from __future__ import annotations

import sys
from pathlib import Path


def get_project_root() -> Path:
    """Returns the absolute root directory of the application.

    Resolves properly when running in normal Python environment or packaged
    inside PyInstaller executable (_MEIPASS).

    Returns:
        Path pointing to the application root directory.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent.parent.parent


def get_assets_dir() -> Path:
    """Returns the directory containing static UI assets and stylesheets.

    Returns:
        Path pointing to the assets directory.
    """
    return get_project_root() / "src" / "ir_terminal" / "ui" / "styles"
