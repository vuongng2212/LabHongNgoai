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


_LOG_FORMAT = (
    "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] [%(threadName)s] "
    "[%(name)s:%(funcName)s:%(lineno)d] %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Maximum bytes rendered inline in a hex preview. Full frames can be 524B;
# previews keep per-packet logs single-line while still showing SOF/SEQ/CMD/LEN.
MAX_HEX_PREVIEW_BYTES: int = 32

_OPCODE_NAMES: dict[int, str] = {
    0x01: "DATA_CHUNK",
    0x02: "DATA_TEXT",
    0x10: "ACK",
    0x11: "NACK",
    0x20: "PING",
    0x21: "PONG",
    0x30: "TRANSFER_START",
    0x31: "TRANSFER_END",
    0x32: "TRANSFER_PAUSE",
    0x33: "TRANSFER_RESUME",
    0x34: "TRANSFER_START_LARGE",
    0x35: "DATA_CHUNK_LARGE",
    0x36: "TRANSFER_END_LARGE",
}

_ACK_STATUS_NAMES: dict[int, str] = {
    0x00: "OK",
    0x01: "CRC_ERROR",
    0x02: "OUT_OF_ORDER",
    0x03: "TIMEOUT",
}


def opcode_name(opcode: int) -> str:
    """Returns human-readable opcode mnemonic for log lines.

    Args:
        opcode: Raw opcode byte value.

    Returns:
        Mnemonic such as ``DATA_TEXT`` or fallback ``OP_0xFF``.
    """
    return _OPCODE_NAMES.get(opcode, f"OP_0x{opcode:02X}")


def ack_status_name(status: int) -> str:
    """Returns human-readable ACK status mnemonic for log lines.

    Args:
        status: Raw ACK status byte value.

    Returns:
        Mnemonic such as ``OK`` or fallback ``STATUS_0xFF``.
    """
    return _ACK_STATUS_NAMES.get(status, f"STATUS_0x{status:02X}")


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


def format_frame_summary(
    seq: int, opcode: int, payload_len: int, crc32: int | None = None
) -> str:
    """Formats a compact frame descriptor for consistent cross-layer log correlation.

    Args:
        seq: Sequence number byte.
        opcode: Opcode byte.
        payload_len: Payload length in bytes.
        crc32: Optional CRC-32 value.

    Returns:
        String like ``seq=5 opcode=DATA_TEXT(0x02) len=12 crc=0x1234ABCD``.
    """
    base = f"seq={seq} opcode={opcode_name(opcode)}(0x{opcode:02X}) len={payload_len}B"
    if crc32 is not None:
        base += f" crc=0x{crc32 & 0xFFFFFFFF:08X}"
    return base


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
        return logger, _qt_log_emitter

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    # 1. Console Stream Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. File Handler (Rotating)
    if log_dir is None:
        from ir_terminal.utils.path_helpers import get_logs_dir

        log_dir = get_logs_dir()
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
