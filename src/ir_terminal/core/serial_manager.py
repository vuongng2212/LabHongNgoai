"""High-level Serial Connection Manager coordinating Worker Thread lifecycle."""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal

from ir_terminal.core.serial_worker import SerialWorker, scan_available_ports
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE, SHUTDOWN_WAIT_MS
from ir_terminal.protocol.framer import pack_frame
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import (
    LogTag,
    format_frame_summary,
    format_hex_preview,
    opcode_name,
)

logger = logging.getLogger(__name__)


class SerialManager(QObject):
    """Coordinates the background QThread and exposes high-level API to UI/Services."""

    # UI facing signals
    connected: ClassVar[Signal] = Signal(str, int)  # (port, baud)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_tx: ClassVar[Signal] = Signal(bytes)
    raw_rx: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(Packet)  # (Packet)
    ports_changed: ClassVar[Signal] = Signal(list)
    framer_error: ClassVar[Signal] = Signal(str, int)

    # Internal dispatch signals to worker thread
    _sig_open: ClassVar[Signal] = Signal(str, int)
    _sig_close: ClassVar[Signal] = Signal()
    _sig_send: ClassVar[Signal] = Signal(bytes)
    _sig_inject: ClassVar[Signal] = Signal(bytes)

    def __init__(self) -> None:
        super().__init__()
        self._thread = QThread()
        self._worker = SerialWorker()
        self._worker.moveToThread(self._thread)

        # Connect internal dispatch signals
        self._sig_open.connect(self._worker.open_port)
        self._sig_close.connect(self._worker.close_port)
        self._sig_send.connect(self._worker.send_bytes)
        self._sig_inject.connect(self._worker.inject_rx_data)

        # Forward worker signals to manager subscribers
        self._worker.connected.connect(self.connected)
        self._worker.disconnected.connect(self.disconnected)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.raw_data_sent.connect(self.raw_tx)
        self._worker.raw_data_received.connect(self.raw_rx)
        self._worker.packet_received.connect(self.packet_received)
        self._worker.ports_changed.connect(self.ports_changed)
        self._worker.framer_error.connect(self.framer_error)

        self._thread.start()
        self._is_connected = False
        self._current_port = ""
        self._current_baud = DEFAULT_BAUDRATE

        self.connected.connect(self._on_connected)
        self.disconnected.connect(self._on_disconnected)

    def _on_connected(self, port: str, baud: int) -> None:
        self._is_connected = True
        self._current_port = port
        self._current_baud = baud
        logger.info(
            "%s Manager link UP: port=%s baud=%d",
            LogTag.HW_SERIAL,
            port,
            baud,
        )

    def _on_disconnected(self) -> None:
        logger.info(
            "%s Manager link DOWN: was port=%s baud=%d",
            LogTag.HW_SERIAL,
            self._current_port,
            self._current_baud,
        )
        self._is_connected = False
        self._current_port = ""

    @property
    def is_connected(self) -> bool:
        """Returns True if serial port is actively connected."""
        return self._is_connected

    @property
    def current_port(self) -> str:
        """Returns current active port name."""
        return self._current_port

    @property
    def current_baud(self) -> int:
        """Returns current baud rate."""
        return self._current_baud

    def connect_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Requests opening serial port in background thread."""
        logger.info(
            "%s Manager connect requested: port=%s baud=%d (dispatch to worker thread)",
            LogTag.HW_SERIAL,
            port_name,
            baudrate,
        )
        self._sig_open.emit(port_name, baudrate)

    def disconnect_port(self) -> None:
        """Requests closing serial port in background thread."""
        logger.info(
            "%s Manager disconnect requested: current_port=%s",
            LogTag.HW_SERIAL,
            self._current_port,
        )
        self._sig_close.emit()

    def inject_raw_rx(self, data: bytes) -> None:
        """Injects simulated raw bytes via worker thread (thread-safe)."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager RX inject: len=%dB hex=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
            )
        self._sig_inject.emit(data)

    def send_raw(self, data: bytes) -> None:
        """Sends raw byte sequence through serial worker."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX raw: len=%dB hex=%s connected=%s port=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
                self._is_connected,
                self._current_port,
            )
        self._sig_send.emit(data)

    def send_packet(self, seq: int, opcode: int, payload: bytes = b"") -> None:
        """Packs and transmits a structured protocol frame."""
        try:
            frame = pack_frame(seq=seq, opcode=opcode, payload=payload)
        except Exception:
            logger.exception(
                "%s Manager pack FAILED %s payload_hex=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(seq, opcode, len(payload)),
                format_hex_preview(payload, max_bytes=16),
            )
            raise
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX %s (%s) port=%s frame_len=%dB frame_hex=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(seq, opcode, len(payload)),
                opcode_name(opcode),
                self._current_port or "<no-port>",
                len(frame),
                format_hex_preview(frame),
            )
        self.send_raw(frame)

    @staticmethod
    def get_port_list() -> list[str]:
        """Scans for available serial ports on the host system."""
        return scan_available_ports()

    def shutdown(self) -> None:
        """Cleanly halts background thread and worker."""
        logger.info(
            "%s Manager shutdown: requesting worker close (port=%s, connected=%s)",
            LogTag.HW_SERIAL,
            self._current_port,
            self._is_connected,
        )
        try:
            self._sig_close.emit()
            self._thread.quit()
            if not self._thread.wait(SHUTDOWN_WAIT_MS):
                logger.warning(
                    "%s SerialManager thread did not stop in %d ms "
                    "(thread_running=%s, port=%s)",
                    LogTag.HW_SERIAL,
                    SHUTDOWN_WAIT_MS,
                    self._thread.isRunning(),
                    self._current_port,
                )
            else:
                logger.info("%s SerialManager worker thread stopped cleanly", LogTag.HW_SERIAL)
        except Exception as exc:
            logger.warning(
                "%s SerialManager shutdown error (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
