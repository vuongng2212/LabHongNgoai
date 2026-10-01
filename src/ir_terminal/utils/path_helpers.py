"""Filesystem, asset, and persistent data storage resolution helpers."""

from __future__ import annotations

import sys
from pathlib import Path


def get_project_root() -> Path:
    """Returns the absolute root directory of the application."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent.parent.parent


def get_assets_dir() -> Path:
    """Returns the directory containing static UI assets and stylesheets."""
    return get_project_root() / "src" / "ir_terminal" / "ui" / "styles"


def get_app_data_dir() -> Path:
    """Returns the persistent application data directory on hard drive.

    Structure:
        ~/.ir_terminal/
            ├── data/ (SQLite database)
            ├── transfers/ (Received & saved optical files)
            └── logs/ (Tagged rotating log files)
    """
    base_dir = Path.home() / ".ir_terminal"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_db_path() -> Path:
    """Returns the SQLite database file path."""
    data_dir = get_app_data_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "ir_terminal.db"


def get_transfers_dir() -> Path:
    """Returns the directory where received optical files are stored permanently."""
    transfers_dir = get_app_data_dir() / "transfers"
    transfers_dir.mkdir(parents=True, exist_ok=True)
    return transfers_dir


def get_logs_dir() -> Path:
    """Returns the directory where rotating system logs are stored."""
    logs_dir = get_app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir
