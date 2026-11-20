"""Hàm trợ giúp phân giải đường dẫn hệ thống tệp, tài nguyên và lưu trữ dữ liệu bền vững.

Dữ liệu chạy thời gian thực (các tệp JSON, file nhận được, logs, xuất CSV) được lưu trữ
cục bộ ngay cạnh ứng dụng thay vì ghi vào thư mục home của người dùng:
- Bản đóng gói PyInstaller: <thư mục chứa IR_OWC_Lab_Terminal.exe>/data
- Môi trường phát triển: <gốc dự án>/data
- Biến môi trường IR_DATA_DIR có thể ghi đè thư mục cơ sở phục vụ kiểm thử tự động.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DATA_DIR_ENV = "IR_DATA_DIR"

DEV_MODE_ENV = "IR_DEV_MODE"

INSTANCE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,16}$"
_INSTANCE_ID_RE = re.compile(INSTANCE_ID_PATTERN)


class InvalidInstanceIdError(ValueError):
    """Ngoại lệ phát sinh khi định danh instance chứa các ký tự không hợp lệ hoặc không an toàn."""


def normalize_instance_id(value: str | None) -> str | None:
    """Xác thực và chuyển đổi định danh instance thành chữ in hoa an toàn cho hệ thống tệp.

    Args:
        value: Chuỗi định danh instance truyền từ CLI hoặc biến môi trường.

    Returns:
        Chuỗi định danh đã viết hoa, hoặc None nếu rỗng.

    Raises:
        InvalidInstanceIdError: Nếu chuỗi chứa các ký tự không hợp lệ.
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
    """Lấy tên định danh instance (A/B) từ tham số hoặc biến môi trường IR_INSTANCE."""
    if explicit:
        return explicit.strip().upper() or None
    env = os.environ.get("IR_INSTANCE", "").strip().upper()
    return env or None


def get_project_root() -> Path:
    """Trả về đường dẫn tuyệt đối tới thư mục gốc của dự án."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass))
    return Path(__file__).resolve().parent.parent.parent.parent


def get_app_root() -> Path:
    """Trả về thư mục thực thi của ứng dụng (nơi đặt tệp thực thi hoặc gốc mã nguồn)."""
    if getattr(sys, "frozen", False):
        executable = getattr(sys, "executable", "") or sys.argv[0]
        return Path(executable).resolve().parent
    return get_project_root()


def get_web_index_path() -> Path:
    """Phân giải đường dẫn tuyệt đối tới tệp index.html trong cả môi trường dev và PyInstaller.

    Returns:
        Đường dẫn Path tới tệp index.html hợp lệ.

    Raises:
        FileNotFoundError: Nếu không tìm thấy tệp index.html trong bất kỳ đường dẫn nào.
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
    """Trả về đường dẫn thư mục chứa các tài nguyên tĩnh giao diện và stylesheets."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        bundled = Path(str(meipass)) / "ir_terminal" / "ui" / "web" / "assets"
        if bundled.exists():
            return bundled
    return get_project_root() / "src" / "ir_terminal" / "ui" / "web" / "assets"


def get_app_data_dir(instance: str | None = None) -> Path:
    """Trả về và tự động tạo thư mục lưu trữ dữ liệu bền vững của ứng dụng.

    Args:
        instance: Tên instance tùy chọn (A hoặc B) để phân tách thư mục con riêng biệt.

    Returns:
        Đường dẫn thư mục dữ liệu đã sẵn sàng sử dụng.
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
    """Trả về thư mục lưu trữ các tệp JSON dữ liệu (tương đương get_app_data_dir)."""
    return get_app_data_dir(instance)


def get_transfers_dir(instance: str | None = None) -> Path:
    """Trả về thư mục lưu trữ các tệp tin nhận được qua kênh truyền quang."""
    transfers_dir = get_app_data_dir(instance) / "transfers"
    transfers_dir.mkdir(parents=True, exist_ok=True)
    return transfers_dir


def get_logs_dir(instance: str | None = None) -> Path:
    """Trả về thư mục lưu trữ các tệp tin nhật ký xoay vòng."""
    logs_dir = get_app_data_dir(instance) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_webengine_dirs(instance: str | None = None) -> tuple[Path, Path]:
    """Trả về cặp thư mục (cache_dir, storage_dir) cho QWebEngineProfile theo từng instance.

    Args:
        instance: Tên định danh instance (A hoặc B).

    Returns:
        Tuple gồm (thư_mục_cache, thư_mục_storage) đã được tạo sẵn trên đĩa.
    """
    base = get_app_data_dir(instance) / "webengine"
    cache_dir = base / "cache"
    storage_dir = base / "storage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, storage_dir


def is_dev_mode() -> bool:
    """Kiểm tra xem chế độ phát triển (Developer Mode) có đang được kích hoạt hay không."""
    return os.environ.get(DEV_MODE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def is_dual_mock_enabled() -> bool:
    """Kiểm tra xem chế độ mô phỏng hai trạm ảo LINK_A/B có được bật hay không."""
    return os.environ.get("IR_ENABLE_DUAL_MOCK", "").strip() in ("1", "true", "TRUE", "yes")
