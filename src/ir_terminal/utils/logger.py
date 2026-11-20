"""Hệ thống ghi log có cấu trúc tích hợp thẻ phân loại IoT, xoay vòng file và phát tín hiệu Qt Signal.

Cung cấp các thẻ LogTag giúp lọc và chẩn đoán sự cố phần cứng IoT nối tiếp, lớp vật lý quang,
đóng khung giao thức WinForm, dịch vụ chat, truyền nhận file và cầu nối IPC.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, Signal


class LogTag(StrEnum):
    """Thẻ định danh phân loại hệ thống con phục vụ khoanh vùng sự cố phần cứng IoT."""

    HW_SERIAL = "[HW_SERIAL]"
    OPTICAL_PHY = "[OPTICAL_PHY]"
    PROTO_FRAME = "[PROTO_FRAME]"
    CHAT_SVC = "[CHAT_SVC]"
    FILE_SVC = "[FILE_SVC]"
    STORAGE = "[STORAGE]"
    IPC_BRIDGE = "[IPC_BRIDGE]"
    BAUD_CTRL = "[BAUD_CTRL]"


_LOG_FORMAT = (
    "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] [%(threadName)s] "
    "[%(name)s:%(funcName)s:%(lineno)d] %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

MAX_HEX_PREVIEW_BYTES: int = 32


def format_hex_preview(
    data: bytes | bytearray | memoryview, max_bytes: int = MAX_HEX_PREVIEW_BYTES
) -> str:
    """Định dạng chuỗi byte thành biểu diễn thập lục phân viết hoa rút gọn trên một dòng log.

    Args:
        data: Dữ liệu byte thô cần xem trước.
        max_bytes: Số byte tối đa được hiển thị trước khi cắt bớt (mặc định 32 bytes).

    Returns:
        Chuỗi ký tự dạng: "55 AA 02 ... (len=240B, shown=32B)" hoặc "<empty>".
    """
    total = len(data)
    if total == 0:
        return "<empty>"
    shown = min(total, max_bytes)
    view = bytes(data[:shown]) if not isinstance(data, bytes) else data[:shown]
    hex_part = view.hex(" ").upper()
    if total > shown:
        return f"{hex_part} ... (len={total}B, shown={shown}B)"
    return f"{hex_part} (len={total}B)"


class QtLogEmitter(QObject):
    """Đối tượng Qt phát tín hiệu signal mỗi khi một bản ghi log mới được định dạng."""

    record_logged: ClassVar[Signal] = Signal(str, str, str)


_qt_log_emitter = QtLogEmitter()


class QtSignalLogHandler(logging.Handler):
    """Bộ xử lý log tùy biến chuyển tiếp các thông điệp log trực tiếp tới Qt Signal sink."""

    def __init__(self, emitter: QtLogEmitter) -> None:
        super().__init__()
        self._emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._emitter.record_logged.emit(record.asctime, record.levelname, msg)
        except Exception:
            self.handleError(record)


def _add_file_handler(logger: logging.Logger, log_dir: Path) -> None:
    """Gắn bộ xử lý tệp xoay vòng RotatingFileHandler vào logger cho thư mục tương ứng."""
    from logging.handlers import RotatingFileHandler as _RFH

    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "app.log").resolve()
    for h in logger.handlers:
        base = getattr(h, "baseFilename", "")
        try:
            if base and Path(base).resolve() == target:
                return
        except Exception:
            continue
    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    fh = _RFH(target, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def _retarget_file_handler(logger: logging.Logger, log_dir: Path) -> None:
    """Chuyển hướng tệp log xoay vòng sang thư mục log_dir của phiên tiến trình hiện hành."""
    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "app.log").resolve()
    for h in list(logger.handlers):
        if not isinstance(h, RotatingFileHandler):
            continue
        base = getattr(h, "baseFilename", "")
        try:
            if base and Path(base).resolve() == target:
                return
        except Exception:
            pass
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _add_file_handler(logger, log_dir)


def setup_logger(
    log_dir: Path | None = None,
    log_level: int = logging.INFO,
) -> tuple[logging.Logger, QtLogEmitter]:
    """Cấu hình bộ ghi log ứng dụng với đầu ra console, tệp xoay vòng app.log và Qt Signal handler.

    Args:
        log_dir: Thư mục lưu trữ tệp nhật ký (mặc định lấy theo thư mục logs của ứng dụng).
        log_level: Cấp độ chi tiết của log (mặc định logging.INFO).

    Returns:
        Tuple gồm đối tượng Logger gốc và QtLogEmitter để giao diện UI đăng ký nhận thông báo.
    """
    logger = logging.getLogger("ir_terminal")
    logger.setLevel(log_level)
    logger.propagate = False

    if logger.handlers:
        if log_dir is not None:
            _retarget_file_handler(logger, log_dir)
        return logger, _qt_log_emitter

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_dir is None:
        from ir_terminal.utils.path_helpers import get_logs_dir

        log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    qt_handler = QtSignalLogHandler(_qt_log_emitter)
    qt_handler.setFormatter(formatter)
    logger.addHandler(qt_handler)

    return logger, _qt_log_emitter
