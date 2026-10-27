"""Filesystem, asset, and persistent data storage resolution helpers.

Runtime data (JSON stores, received files, logs, CSV exports) is kept next to
the running application instead of in the user home directory:

* Frozen PyInstaller build: ``<folder containing IR_OWC_Lab_Terminal.exe>/data``
  (works for both one-file and one-folder builds).
* Development checkout: ``<project root>/data``.

``IR_DATA_DIR`` can override the base directory (used by the test suite and
portable deployments).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DATA_DIR_ENV = "IR_DATA_DIR"

INSTANCE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,16}$"
_INSTANCE_ID_RE = re.compile(INSTANCE_ID_PATTERN)


class InvalidInstanceIdError(ValueError):
    """Raised when a dual-mock instance id is not a safe filesystem label."""


def normalize_instance_id(value: str | None) -> str | None:
    """Validates and upper-cases a dual-mock instance id.

    The id becomes a directory name under ``data/instances/<ID>`` so it must be
    a portable filesystem label (no ``.``, ``/``, ``\\`` or ``..`` traversal).

    Args:
        value: Raw instance id from the CLI/env, or None.

    Returns:
        Upper-cased instance id, or None when empty.

    Raises:
        InvalidInstanceIdError: If the value contains unsafe characters.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if not _INSTANCE_ID_RE.match(candidate):
        raise InvalidInstanceIdError(
            f"Invalid instance id {value!r}: expected 1-16 chars matching {INSTANCE_ID_PATTERN}"
        )
    return candidate.upper()


def get_instance_name(explicit: str | None = None) -> str | None:
    """Returns dual-mock instance name (A/B) from arg or IR_INSTANCE env."""
    if explicit:
        return explicit.strip().upper() or None
    env = os.environ.get("IR_INSTANCE", "").strip().upper()
    return env or None


def get_project_root() -> Path:
    """Returns the absolute root directory of the application bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent.parent.parent


def get_app_root() -> Path:
    """Returns the directory the application was launched from.

    PyInstaller sets ``sys.frozen`` and ``sys.executable`` points at the
    generated executable. For a one-folder build that is the folder containing
    ``IR_OWC_Lab_Terminal.exe``; for a one-file build it is the temporary
    extraction *source* executable path, which is exactly where the user placed
    the ``.exe``. In development the repository root is used.

    Returns:
        Absolute directory used as the parent of the ``data/`` tree.
    """
    if getattr(sys, "frozen", False):
        executable = getattr(sys, "executable", "") or sys.argv[0]
        return Path(executable).resolve().parent
    return get_project_root()


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
    raise FileNotFoundError(f"index.html not found in any bundle path: {candidates}")


def get_assets_dir() -> Path:
    """Returns the directory containing static UI assets and stylesheets."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        bundled = Path(str(meipass)) / "ir_terminal" / "ui" / "web" / "assets"
        if bundled.exists():
            return bundled
    return get_project_root() / "src" / "ir_terminal" / "ui" / "web" / "assets"


def get_app_data_dir(instance: str | None = None) -> Path:
    """Returns the persistent application data directory and creates it.

    Default layout::

        <app root>/data/
            ├── chat_history.json
            ├── file_transfers.json
            ├── telemetry_samples.json
            ├── settings.json
            ├── transfers/   (received optical files)
            └── logs/        (rotating tagged log files)

    ``IR_DATA_DIR`` replaces ``<app root>/data`` when set. When instance is set
    (dual-mock A/B), an isolated ``instances/<A|B>/`` subtree is used so two
    apps on one PC never share JSON stores, transfers, or logs.

    Args:
        instance: Optional dual-mock instance name (A/B).

    Returns:
        Existing data directory path.
    """
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        base_dir = Path(override).expanduser()
    else:
        base_dir = get_app_root() / "data"

    raw_instance = instance if instance is not None else os.environ.get("IR_INSTANCE")
    inst = normalize_instance_id(raw_instance)
    if inst:
        base_dir = base_dir / "instances" / inst
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_data_dir(instance: str | None = None) -> Path:
    """Returns the directory holding JSON file stores (same as app data root)."""
    return get_app_data_dir(instance)


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


def get_webengine_dirs(instance: str | None = None) -> tuple[Path, Path]:
    """Returns per-instance (cache_dir, storage_dir) for QWebEngineProfile.

    Two app processes sharing the default Chromium profile corrupt the cache
    and cookie SQLite databases. Each instance (and each data root) therefore
    gets its own profile directory under ``<app data>/webengine/``.

    Args:
        instance: Optional dual-mock instance name (A/B).

    Returns:
        Tuple of (http_cache_dir, persistent_storage_dir) that both exist.
    """
    base = get_app_data_dir(instance) / "webengine"
    cache_dir = base / "cache"
    storage_dir = base / "storage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, storage_dir


def is_dual_mock_enabled() -> bool:
    """Returns True when LINK_A/B virtual ports should be listed in UI."""
    return os.environ.get("IR_ENABLE_DUAL_MOCK", "").strip() in ("1", "true", "TRUE", "yes")
