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

    HW_SERIAL = "[HW_SERIAL]"  # Physical UART, COM discovery, port open/close, baudrates
    OPTICAL_PHY = "[OPTICAL_PHY]"  # Optical transceiver, alignment, LOS beam blockage, RSSI
    PROTO_FRAME = "[PROTO_FRAME]"  # Binary framing, SOF/EOF magic, length check, CRC-32 validation
    ARQ_ENGINE = "[ARQ_ENGINE]"  # Stop-and-Wait ARQ state machine, ACK/NACK, retry, timeout
    CHAT_SVC = "[CHAT_SVC]"  # Optical text messaging, encoding, chat inspector
    FILE_SVC = "[FILE_SVC]"  # Binary file chunking, disk I/O, hash verification
    STORAGE = "[STORAGE]"  # File-store persistence, CRUD, history load
    IPC_BRIDGE = "[IPC_BRIDGE]"  # QWebChannel Chromium-Python IPC dispatch


class QtLogEmitter(QObject):
    """Qt Object that emits a signal whenever a log record is formatted."""

    record_logged: ClassVar[Signal] = Signal(str, str, str)  # (timestamp, level, message)


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


def setup_logger(
    log_dir: Path | None = None,
    log_level: int = logging.INFO,
) -> tuple[logging.Logger, QtLogEmitter]:
    """Configures root application logger with console, rotating file, and Qt handlers.

    Args:
        log_dir: Directory where log files are stored. Defaults to ~/.ir_terminal/logs.
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
        return logger, _qt_log_emitter

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Stream Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. File Handler (Rotating)
    if log_dir is None:
        log_dir = Path.home() / ".ir_terminal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB per log file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 3. Qt Signal Handler for UI Console
    qt_handler = QtSignalLogHandler(_qt_log_emitter)
    qt_handler.setFormatter(formatter)
    logger.addHandler(qt_handler)

    return logger, _qt_log_emitter
