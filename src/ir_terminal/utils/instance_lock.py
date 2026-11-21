"""Khóa chống mở đè ứng dụng trên cùng một cổng COM và thư mục dữ liệu (Instance Lock).

Ngăn người dùng vô tình bấm mở 2 lần cùng một trạm, dẫn đến xung đột cổng COM phần cứng (báo lỗi 'Access is denied' hoặc 'Port already open') và tranh chấp file dữ liệu:
- Sử dụng cơ chế khóa tệp tin app.lock.
- Vẫn cho phép mở đồng thời Node A và Node B song song vì mỗi node có thư mục và cổng COM riêng.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile

DEFAULT_STALE_LOCK_MS: int = 30_000


class InstanceLockError(RuntimeError):
    """Ngoại lệ phát sinh khi một tiến trình khác đã chiếm giữ khóa thư mục dữ liệu."""


def acquire_instance_lock(
    lock_path: Path,
    *,
    allow_multiple: bool = False,
    stale_ms: int = DEFAULT_STALE_LOCK_MS,
    timeout_ms: int = 200,
) -> QLockFile | None:
    """Chiếm giữ khóa tệp tin QLockFile cho phiên tiến trình hiện hành.

    Args:
        lock_path: Đường dẫn tệp tin khóa (ví dụ: <data_dir>/app.lock).
        allow_multiple: Khi là True, bỏ qua kiểm tra khóa (--allow-multiple).
        stale_ms: Thời gian tính bằng ms để tự động thu hồi khóa nếu tiến trình trước bị crash bất thường.
        timeout_ms: Thời gian chờ tối đa để chiếm giữ khóa trước khi báo lỗi.

    Returns:
        Đối tượng QLockFile đang giữ khóa, hoặc None nếu cho phép chạy nhiều tiến trình.

    Raises:
        InstanceLockError: Nếu tiến trình khác hiện đang nắm giữ khóa của thư mục này.
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
