"""Non-blocking Serial Worker implementing Worker-Object Pattern on dedicated QThread."""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from serial.tools import list_ports

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.transport_factory import create_transport
from ir_terminal.protocol.constants import (
    DEFAULT_BAUDRATE,
    HW_POLL_MS,
    MOCK_POLL_MS,
    RING_BUFFER_CAPACITY,
    RX_COMPACT_THRESHOLD,
)
from ir_terminal.protocol.framer import FramerStats, parse_frames_with_stats
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import is_dual_mock_enabled

logger = logging.getLogger(__name__)


class SerialWorker(QObject):
    """Background worker managing serial port I/O and emitting parsed packets."""

    # Qt Signals
    connected: ClassVar[Signal] = Signal(str, int)  # (port_name, baudrate)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_data_received: ClassVar[Signal] = Signal(bytes)
    raw_data_sent: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(Packet)  # (Packet)
    ports_changed: ClassVar[Signal] = Signal(list)  # ([port names])
    framer_error: ClassVar[Signal] = Signal(str, int)  # (kind, count delta)

    def __init__(self) -> None:
        """Initializes non-blocking serial worker instance."""
        super().__init__()
        self._transport: ITransport | None = None
        self._rx_accumulation = bytearray()
        self._rx_consumed = 0
        self._rx_dropped = 0
        self._framer_stats = FramerStats()
        self._last_ports: list[str] = []
        self._port_poll_timer: QTimer | None = None
        self._poll_timer: QTimer | None = None

    @Slot(str, int)
    def open_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Opens physical or virtual serial connection and starts periodic polling.

        Args:
            port_name: Target port identifier.
            baudrate: Baud rate integer.
        """
        try:
            self.close_port()
            logger.info(
                "%s Opening serial port: %s at %d bps", LogTag.HW_SERIAL, port_name, baudrate
            )

            self._transport = create_transport(port_name=port_name, baudrate=baudrate)

            self._transport.open()
            self._rx_accumulation = bytearray()
            self._rx_consumed = 0
            self._rx_dropped = 0
            self._framer_stats = FramerStats()

            # Setup periodic non-blocking poll timer on worker thread event loop.
            # Mock loopback simulation polls faster (2ms) so file-transfer demos
            # stay snappy; real UART hardware keeps the conservative 10ms tick.
            from ir_terminal.core.mock_serial import MockSerialPort

            poll_ms = MOCK_POLL_MS if isinstance(self._transport, MockSerialPort) else HW_POLL_MS
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(poll_ms)  # 500 Hz mock / 100 Hz hardware
            self._poll_timer.timeout.connect(self._poll_serial)
            self._poll_timer.start()
            self._start_port_watcher()

            self.connected.emit(port_name, baudrate)

        except Exception as exc:
            logger.exception(
                "%s Failed to open serial port %s: %s", LogTag.HW_SERIAL, port_name, exc
            )
            self.error_occurred.emit(f"Connection failed: {exc}")

    @Slot()
    def close_port(self) -> None:
        """Stops polling timer and cleanly closes serial transport."""
        was_open = self._transport is not None and self._transport.is_open
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._stop_port_watcher()

        if self._transport and self._transport.is_open:
            try:
                self._transport.close()
            except Exception as exc:
                logger.warning("%s Error during transport close: %s", LogTag.HW_SERIAL, exc)

        self._transport = None
        self._rx_accumulation = bytearray()
        self._rx_consumed = 0
        # Only emit on a real connected->disconnected transition. open_port()
        # calls close_port() as cleanup, and emitting here would fake a
        # disconnect that wipes pending-connect UI state before the real
        # connected/error signal arrives.
        if was_open:
            self.disconnected.emit()

    @Slot(bytes)
    def inject_rx_data(self, data: bytes) -> None:
        """Thread-safe RX injection slot (replaces direct transport access).

        Args:
            data: Raw bytes to feed into the RX accumulation buffer.
        """
        if not data:
            return
        self._append_rx_chunk(data)
        self._drain_rx_buffer(emit_raw=False)

    def _append_rx_chunk(self, chunk: bytes) -> None:
        """Appends chunk with a hard cap; drops oldest on overflow (counted)."""
        self._rx_accumulation.extend(chunk)
        live = len(self._rx_accumulation) - self._rx_consumed
        if live > RING_BUFFER_CAPACITY:
            overflow = live - RING_BUFFER_CAPACITY
            del self._rx_accumulation[:overflow]
            self._rx_consumed = max(0, self._rx_consumed - overflow)
            self._rx_dropped += overflow
            logger.warning(
                "%s RX accumulation overflow: dropped %d oldest bytes",
                LogTag.HW_SERIAL,
                overflow,
            )

    def _drain_rx_buffer(self, emit_raw: bool = True) -> None:
        """Parses accumulated bytes and emits packets with framer diagnostics."""
        before = self._framer_stats.snapshot()
        pending = memoryview(self._rx_accumulation)[self._rx_consumed :]
        packets, remaining = parse_frames_with_stats(pending, self._framer_stats)[:2]
        pending.release()
        # Release the view export before any compaction: resizing
        # a bytearray with live exports raises BufferError, which
        # would kill polling and stall the ARQ session.
        self._rx_consumed = len(self._rx_accumulation) - len(remaining)
        if self._rx_consumed >= RX_COMPACT_THRESHOLD:
            del self._rx_accumulation[: self._rx_consumed]
            self._rx_consumed = 0
        after = self._framer_stats.snapshot()
        for kind in ("crc_errors", "eof_errors", "len_errors"):
            delta = after[kind] - before[kind]
            if delta > 0:
                self.framer_error.emit(kind, delta)
        for pkt in packets:
            self.packet_received.emit(pkt)

    @Slot(bytes)
    def send_bytes(self, data: bytes) -> None:
        """Transmits raw binary bytes to the serial transport.

        Args:
            data: Raw bytes to write out to UART.
        """
        if not self._transport or not self._transport.is_open:
            self.error_occurred.emit("Cannot send data: Serial port is not connected")
            return

        try:
            self._transport.write(data)
            self.raw_data_sent.emit(data)
        except Exception as exc:
            logger.exception("%s Write error to serial port: %s", LogTag.HW_SERIAL, exc)
            self.error_occurred.emit(f"Write error: {exc}")

    @Slot()
    def _poll_serial(self) -> None:
        """Periodically polls for incoming serial bytes."""
        if not self._transport or not self._transport.is_open:
            return

        try:
            available = self._transport.in_waiting
            if available > 0:
                chunk = self._transport.read(available)
                if chunk:
                    self.raw_data_received.emit(chunk)
                    # Extend bytearray (amortized, no full copy) and parse a
                    # memoryview over the unconsumed tail. Compaction only when
                    # the consumed prefix grows large, avoiding memmove per poll.
                    self._append_rx_chunk(chunk)
                    self._drain_rx_buffer(emit_raw=False)

        except Exception as exc:
            logger.exception("%s Serial read poll error: %s", LogTag.HW_SERIAL, exc)
            self.error_occurred.emit(f"Read error: {exc}")
            self.close_port()

    def _start_port_watcher(self) -> None:
        """Starts hotplug polling (emits ports_changed only on change)."""
        self._last_ports = scan_available_ports()
        if self._port_poll_timer is None:
            self._port_poll_timer = QTimer(self)
            self._port_poll_timer.setInterval(1500)
            self._port_poll_timer.timeout.connect(self._poll_ports)
            self._port_poll_timer.start()

    def _stop_port_watcher(self) -> None:
        """Stops hotplug polling."""
        if self._port_poll_timer is not None:
            self._port_poll_timer.stop()
            self._port_poll_timer = None

    @Slot()
    def _poll_ports(self) -> None:
        """Emits ports_changed when the OS port list differs."""
        try:
            current = scan_available_ports()
            if current != self._last_ports:
                self._last_ports = current
                self.ports_changed.emit(current)
        except Exception as exc:
            logger.debug("%s Port watcher poll failed: %s", LogTag.HW_SERIAL, exc)

    @property
    def rx_dropped_bytes(self) -> int:
        """Returns cumulative RX accumulation overflow drops."""
        return self._rx_dropped

    @property
    def framer_stats_snapshot(self) -> dict[str, int]:
        """Returns current framer diagnostics."""
        return self._framer_stats.snapshot()


def scan_available_ports() -> list[str]:
    """Scans and returns available system COM ports plus MOCK options.

    LINK_A/B dual-mock ports are listed ONLY when IR_ENABLE_DUAL_MOCK=1 so
    production users with real hardware never see or pick them by mistake.

    Returns:
        List of available COM port identifiers.
    """
    ports = [p.device for p in list_ports.comports()]
    ports.append("MOCK_COM_LOOPBACK")
    if is_dual_mock_enabled():
        ports.extend(["LINK_A", "LINK_B"])
    return ports
