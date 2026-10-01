"""High-level Serial Connection Manager coordinating Worker Thread lifecycle."""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal

from ir_terminal.core.serial_worker import SerialWorker, scan_available_ports
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE
from ir_terminal.protocol.framer import pack_frame

logger = logging.getLogger(__name__)


class SerialManager(QObject):
    """Coordinates the background QThread and exposes high-level API to UI/Services."""

    # UI facing signals
    connected: ClassVar[Signal] = Signal(str, int)  # (port, baud)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_tx: ClassVar[Signal] = Signal(bytes)
    raw_rx: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(object)  # (Packet)

    # Internal dispatch signals to worker thread
    _sig_open: ClassVar[Signal] = Signal(str, int)
    _sig_close: ClassVar[Signal] = Signal()
    _sig_send: ClassVar[Signal] = Signal(bytes)

    def __init__(self) -> None:
        super().__init__()
        self._thread = QThread()
        self._worker = SerialWorker()
        self._worker.moveToThread(self._thread)

        # Connect internal dispatch signals
        self._sig_open.connect(self._worker.open_port)
        self._sig_close.connect(self._worker.close_port)
        self._sig_send.connect(self._worker.send_bytes)

        # Forward worker signals to manager subscribers
        self._worker.connected.connect(self.connected)
        self._worker.disconnected.connect(self.disconnected)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.raw_data_sent.connect(self.raw_tx)
        self._worker.raw_data_received.connect(self.raw_rx)
        self._worker.packet_received.connect(self.packet_received)

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

    def _on_disconnected(self) -> None:
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
        self._sig_open.emit(port_name, baudrate)

    def disconnect_port(self) -> None:
        """Requests closing serial port in background thread."""
        self._sig_close.emit()

    def send_raw(self, data: bytes) -> None:
        """Sends raw byte sequence through serial worker."""
        self._sig_send.emit(data)

    def send_packet(self, seq: int, opcode: int, payload: bytes = b"") -> None:
        """Packs and transmits a structured protocol frame."""
        frame = pack_frame(seq=seq, opcode=opcode, payload=payload)
        self.send_raw(frame)

    @staticmethod
    def get_port_list() -> list[str]:
        """Scans for available serial ports on the host system."""
        return scan_available_ports()

    def shutdown(self) -> None:
        """Cleanly halts background thread and worker."""
        self.disconnect_port()
        self._thread.quit()
        self._thread.wait(1000)
