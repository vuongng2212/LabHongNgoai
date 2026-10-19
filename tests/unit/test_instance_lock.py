"""Unit tests for the per-instance single-instance lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from ir_terminal.utils.instance_lock import InstanceLockError, acquire_instance_lock


def test_second_acquire_is_rejected(tmp_path: Path) -> None:
    """A second process-equivalent lock on the same file must fail."""
    lock_path = tmp_path / "app.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None
    try:
        with pytest.raises(InstanceLockError):
            acquire_instance_lock(lock_path)
    finally:
        first.unlock()


def test_lock_can_be_reacquired_after_unlock(tmp_path: Path) -> None:
    """Releasing the lock lets the next launch start normally."""
    lock_path = tmp_path / "app.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None
    first.unlock()

    second = acquire_instance_lock(lock_path)
    assert second is not None
    second.unlock()


def test_allow_multiple_skips_locking(tmp_path: Path) -> None:
    """--allow-multiple returns None without touching the lock file."""
    lock_path = tmp_path / "app.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None
    try:
        assert acquire_instance_lock(lock_path, allow_multiple=True) is None
    finally:
        first.unlock()


def test_different_instance_lock_paths_do_not_conflict(tmp_path: Path) -> None:
    """Node A and Node B lock different files and can run together."""
    lock_a = acquire_instance_lock(tmp_path / "A" / "app.lock")
    lock_b = acquire_instance_lock(tmp_path / "B" / "app.lock")
    try:
        assert lock_a is not None
        assert lock_b is not None
    finally:
        if lock_a is not None:
            lock_a.unlock()
        if lock_b is not None:
            lock_b.unlock()
