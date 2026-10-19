"""Single-instance lock guarding one application data directory.

Two processes pointed at the same ``data/instances/<id>/`` would fight over the
same JSON stores and serial/TCP port. A per-data-directory :class:`QLockFile`
prevents accidentally opening the same node twice while still allowing Node A
and Node B (different data dirs) to run side by side.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile

# A lock older than this is treated as stale (previous process crashed).
DEFAULT_STALE_LOCK_MS: int = 30_000


class InstanceLockError(RuntimeError):
    """Raised when another process already holds the instance lock."""


def acquire_instance_lock(
    lock_path: Path,
    *,
    allow_multiple: bool = False,
    stale_ms: int = DEFAULT_STALE_LOCK_MS,
    timeout_ms: int = 200,
) -> QLockFile | None:
    """Acquires the per-instance lock file.

    Args:
        lock_path: Path to ``<data_dir>/app.lock``.
        allow_multiple: When True, locking is disabled (``--allow-multiple``).
        stale_ms: Age after which a lock left by a crashed process is stolen.
        timeout_ms: How long to wait for the lock before failing.

    Returns:
        The held QLockFile, or None when locking is disabled.

    Raises:
        InstanceLockError: If another process currently holds the lock.
    """
    if allow_multiple:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(stale_ms)
    if not lock.tryLock(timeout_ms):
        raise InstanceLockError(
            "Another instance is already running for this data directory "
            f"({lock_path.parent}). Close it, or start with --allow-multiple."
        )
    return lock
