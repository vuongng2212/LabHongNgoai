"""Filesystem, asset, and persistent data storage resolution helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_instance_name(explicit: str | None = None) -> str | None:
    """Returns dual-mock instance name (A/B) from arg or IR_INSTANCE env."""
    if explicit:
        return explicit.strip().upper() or None
    env = os.environ.get("IR_INSTANCE", "").strip().upper()
    return env or None


def get_project_root() -> Path:
    """Returns the absolute root directory of the application."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent.parent.parent


def get_web_index_path() -> Path:
    """Resolves absolute path to index.html across dev and PyInstaller environments.

    Returns:
        Path pointing to valid index.html file.

    Raises:
        FileNotFoundError: If index.html cannot be located in any bundle path.
    """
    candidates = [
        get_project_root() / "src" / "ir_terminal" / "ui" / "web" / "index.html",
        get_project_root() / "ir_terminal" / "ui" / "web" / "index.html",
        Path(__file__).resolve().parent.parent / "ui" / "web" / "index.html",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return candidates[0]


def get_assets_dir() -> Path:
    """Returns the directory containing static UI assets and stylesheets."""
    return get_project_root() / "src" / "ir_terminal" / "ui" / "web" / "assets"


def get_app_data_dir(instance: str | None = None) -> Path:
    """Returns the persistent application data directory on hard drive.

    Structure:
        ~/.ir_terminal/
            ├── data/ (JSON file stores)
            ├── transfers/ (Received & saved optical files)
            └── logs/ (Tagged rotating log files)

    When instance is set (dual-mock A/B), an isolated subtree
    ~/.ir_terminal/instances/<A|B>/ is used so two apps on one PC
    never share JSON stores, transfers, or logs.
    """
    inst = get_instance_name(instance)
    if inst:
        base_dir = Path.home() / ".ir_terminal" / "instances" / inst
    else:
        base_dir = Path.home() / ".ir_terminal"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_data_dir(instance: str | None = None) -> Path:
    """Returns the persistent application data directory for JSON file stores."""
    data_dir = get_app_data_dir(instance) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_transfers_dir(instance: str | None = None) -> Path:
    """Returns the directory where received optical files are stored permanently."""
    transfers_dir = get_app_data_dir(instance) / "transfers"
    transfers_dir.mkdir(parents=True, exist_ok=True)
    return transfers_dir


def get_logs_dir(instance: str | None = None) -> Path:
    """Returns the directory where rotating system logs are stored."""
    logs_dir = get_app_data_dir(instance) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def is_dual_mock_enabled() -> bool:
    """Returns True when LINK_A/B virtual ports should be listed in UI."""
    return os.environ.get("IR_ENABLE_DUAL_MOCK", "").strip() in ("1", "true", "TRUE", "yes")
