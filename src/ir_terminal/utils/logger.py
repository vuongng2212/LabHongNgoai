"""Structured logging system with subsystem IoT tags, rotating files, and Qt Signal sink.

Follows Google Python Style Guide docstring conventions.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, Signal


class LogTag(StrEnum):
    """Subsystem categorization tags for IoT hardware troubleshooting."""

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
    """Formats bytes as truncated upper-case hex for single-line log context.

    Args:
        data: Raw bytes to preview.
        max_bytes: Maximum bytes rendered before truncation.

    Returns:
        String like ``55 AA 02 ... (len=240B, shown=32B)`` or ``<empty>``.
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
    """Qt Object that emits a signal whenever a log record is formatted."""

    record_logged: ClassVar[Signal] = Signal(str, str, str)


_qt_log_emitter = QtLogEmitter()


class QtSignalLogHandler(logging.Handler):
    """Custom logging handler routing log messages directly to Qt Signal sink."""

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
    """Attaches (once) a rotating file handler for the given directory."""
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
    """Moves the rotating file handler to ``log_dir`` if it points elsewhere.

    Used when a logger was already configured (for example by an earlier call
    in the same process) but the instance-specific log directory is only known
    afterwards. Removes any stale rotating handler first so two processes never
    share one ``app.log`` handle.
    """
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
    """Configures root application logger with console, rotating file, and Qt handlers.

    Args:
        log_dir: Directory where log files are stored. Defaults to the
            application data tree (``<exe or project root>/data/logs``).
        log_level: Base logging severity level.

    Returns:
        A tuple containing:
            - Root configured logger instance.
            - QtLogEmitter instance for UI subscribers.
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
