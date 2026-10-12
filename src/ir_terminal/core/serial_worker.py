"""Non-blocking Serial Worker implementing Worker-Object Pattern on dedicated QThread."""

from __future__ import annotations

import logging
from typing import ClassVar

import serial
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from serial.tools import list_ports

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.socket_transport import SocketTransport, resolve_link_endpoint
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE
from ir_terminal.protocol.framer import parse_frames_from_buffer
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


class PySerialTransport(ITransport):
    """Concrete wrapper around pyserial conforming to ITransport interface."""

    def __init__(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Initializes PySerial transport.

        Args:
            port_name: Serial device port identifier (e.g. '/dev/ttyUSB0' or 'COM3').
            baudrate: Baud rate for the physical UART connection.
        """
        self._port_name = port_name
        self._baudrate = baudrate
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        """Opens physical serial port connection."""
        self._serial = serial.Serial(
            port=self._port_name,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=0.2,
        )

    def close(self) -> None:
        """Closes physical serial port connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_open(self) -> bool:
        """Returns True if physical serial port is currently open."""
        return bool(self._serial and self._serial.is_open)

    def write(self, data: bytes) -> int:
        """Writes binary data to serial interface.

        Args:
            data: Raw bytes to transmit.

        Returns:
            Number of bytes written.
        """
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not open for write")
        written = self._serial.write(data)
        return int(written) if written is not None else 0

    def read(self, size: int | None = None) -> bytes:
        """Reads incoming bytes from serial buffer.

        Args:
            size: Number of bytes to read, or None to read all available.

        Returns:
            Extracted bytes sequence.
        """
        if not self._serial or not self._serial.is_open:
            return b""
        read_size = self._serial.in_waiting if size is None else size
        if read_size <= 0:
            return b""
        data = self._serial.read(read_size)
        return bytes(data) if data is not None else b""

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes in the serial driver ring buffer."""
        if not self._serial or not self._serial.is_open:
            return 0
        waiting = self._serial.in_waiting
        return int(waiting) if waiting is not None else 0


class SerialWorker(QObject):
    """Background worker managing serial port I/O and emitting parsed packets."""

    # Qt Signals
    connected: ClassVar[Signal] = Signal(str, int)  # (port_name, baudrate)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_data_received: ClassVar[Signal] = Signal(bytes)
    raw_data_sent: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(Packet)  # (Packet)

    def __init__(self) -> None:
        """Initializes non-blocking serial worker instance."""
        super().__init__()
        self._transport: ITransport | None = None
        self._rx_accumulation = bytearray()
        self._rx_consumed = 0
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

            if port_name.startswith("MOCK"):
                auto_respond = not (
                    "PIPE" in port_name or "NO_LOOPBACK" in port_name or "NODE" in port_name
                )
                self._transport = MockSerialPort(
                    port_name=port_name,
                    baudrate=baudrate,
                    auto_respond=auto_respond,
                )
            elif (endpoint := resolve_link_endpoint(port_name)) is not None:
                # Dual-mock path (2 apps on 1 PC via broker). Production
                # PySerial path below is untouched by this branch.
                host, tcp_port = endpoint
                self._transport = SocketTransport(
                    port_name=port_name.strip().upper(), host=host, port=tcp_port
                )
            else:
                self._transport = PySerialTransport(port_name=port_name, baudrate=baudrate)

            self._transport.open()
            self._rx_accumulation = bytearray()
            self._rx_consumed = 0

            # Setup periodic non-blocking poll timer on worker thread event loop.
            # Mock loopback simulation polls faster (2ms) so file-transfer demos
            # stay snappy; real UART hardware keeps the conservative 10ms tick.
            poll_ms = 2 if isinstance(self._transport, MockSerialPort) else 10
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(poll_ms)  # 500 Hz mock / 100 Hz hardware
            self._poll_timer.timeout.connect(self._poll_serial)
            self._poll_timer.start()

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
                    self._rx_accumulation.extend(chunk)
                    pending = memoryview(self._rx_accumulation)[self._rx_consumed :]
                    packets, remaining = parse_frames_from_buffer(pending)
                    pending.release()
                    # Release the view export before any compaction: resizing
                    # a bytearray with live exports raises BufferError, which
                    # would kill polling and stall the ARQ session.
                    self._rx_consumed = len(self._rx_accumulation) - len(remaining)
                    if self._rx_consumed >= 4096:
                        del self._rx_accumulation[: self._rx_consumed]
                        self._rx_consumed = 0
                    for pkt in packets:
                        self.packet_received.emit(pkt)

        except Exception as exc:
            logger.exception("%s Serial read poll error: %s", LogTag.HW_SERIAL, exc)
            self.error_occurred.emit(f"Read error: {exc}")
            self.close_port()


def scan_available_ports() -> list[str]:
    """Scans and returns available system COM ports plus MOCK options.

    LINK_A/B dual-mock ports are listed ONLY when IR_ENABLE_DUAL_MOCK=1 so
    production users with real hardware never see or pick them by mistake.

    Returns:
        List of available COM port identifiers.
    """
    ports = [p.device for p in list_ports.comports()]
    ports.append("MOCK_COM_LOOPBACK")
    import os

    if os.environ.get("IR_ENABLE_DUAL_MOCK", "").strip() in ("1", "true", "TRUE", "yes"):
        ports.extend(["LINK_A", "LINK_B"])
    return ports
