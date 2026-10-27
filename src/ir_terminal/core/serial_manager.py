"""High-level Serial Connection Manager coordinating Worker Thread lifecycle."""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal

from ir_terminal.core.serial_worker import (
    PortInfo,
    SerialWorker,
    scan_available_port_infos,
    scan_available_ports,
)
from ir_terminal.protocol.constants import (
    DEFAULT_BAUDRATE,
    SHUTDOWN_WAIT_MS,
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.protocol.framer import pack_frame
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import (
    LogTag,
    format_frame_summary,
    format_hex_preview,
    opcode_name,
)
from ir_terminal.utils.path_helpers import is_dual_mock_enabled

logger = logging.getLogger(__name__)


class SerialManager(QObject):
    """Coordinates the background QThread and exposes high-level API to UI/Services."""

    connected: ClassVar[Signal] = Signal(str, int)  # (port, baud)
    ports_connected: ClassVar[Signal] = Signal(str, str, int)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_tx: ClassVar[Signal] = Signal(bytes)
    raw_rx: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(Packet)  # (Packet)
    ports_changed: ClassVar[Signal] = Signal(list)
    framer_error: ClassVar[Signal] = Signal(str, int)

    _sig_open: ClassVar[Signal] = Signal(str, int)
    _sig_open_dual: ClassVar[Signal] = Signal(str, str, int)
    _sig_close: ClassVar[Signal] = Signal()
    _sig_send: ClassVar[Signal] = Signal(bytes)
    _sig_inject: ClassVar[Signal] = Signal(bytes)

    def __init__(
        self,
        link_endpoints: dict[str, tuple[str, int]] | None = None,
        *,
        include_link: bool | None = None,
        link_connect_retries: int = 0,
        link_retry_interval_sec: float = 0.25,
        link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
        link_token: str | None = None,
    ) -> None:
        """Initializes the manager and its background worker thread.

        Args:
            link_endpoints: Optional LINK_* -> (host, port) map for dual mode.
            include_link: Whether LINK_A/B appear in the port list (None = env).
            link_connect_retries: Extra LINK connect attempts on open.
            link_retry_interval_sec: Delay between LINK connect attempts.
            link_connect_timeout_sec: Per-attempt LINK connect timeout.
            link_token: Optional broker handshake token.
        """
        super().__init__()
        self._include_link = is_dual_mock_enabled() if include_link is None else include_link
        self._thread = QThread()
        self._worker = SerialWorker(
            link_endpoints=link_endpoints,
            include_link=self._include_link,
            link_connect_retries=link_connect_retries,
            link_retry_interval_sec=link_retry_interval_sec,
            link_connect_timeout_sec=link_connect_timeout_sec,
            link_token=link_token,
        )
        self._worker.moveToThread(self._thread)

        self._sig_open.connect(self._worker.open_port)
        self._sig_open_dual.connect(self._worker.open_ports)
        self._sig_close.connect(self._worker.close_port)
        self._sig_send.connect(self._worker.send_bytes)
        self._sig_inject.connect(self._worker.inject_rx_data)

        self._worker.connected.connect(self.connected)
        self._worker.ports_connected.connect(self.ports_connected)
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
        self._current_tx_port = ""
        self._current_rx_port = ""
        self._current_baud = DEFAULT_BAUDRATE

        self.connected.connect(self._on_connected)
        self.ports_connected.connect(self._on_ports_connected)
        self.disconnected.connect(self._on_disconnected)

    def _on_connected(self, port: str, baud: int) -> None:
        self._is_connected = True
        self._current_port = port
        self._current_tx_port = port
        self._current_baud = baud
        logger.info(
            "%s Manager link UP: port=%s baud=%d",
            LogTag.HW_SERIAL,
            port,
            baud,
        )

    def _on_ports_connected(self, tx_port: str, rx_port: str, baud: int) -> None:
        self._is_connected = True
        self._current_tx_port = tx_port
        self._current_rx_port = rx_port
        self._current_baud = baud
        logger.info(
            "%s Manager dual link UP: tx=%s rx=%s baud=%d",
            LogTag.HW_SERIAL,
            tx_port,
            rx_port,
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
        self._current_tx_port = ""
        self._current_rx_port = ""

    @property
    def is_connected(self) -> bool:
        """Returns True if serial port is actively connected."""
        return self._is_connected

    @property
    def current_port(self) -> str:
        """Returns current active port name."""
        return self._current_port

    @property
    def current_tx_port(self) -> str:
        """Returns current active TX port name."""
        return self._current_tx_port

    @property
    def current_rx_port(self) -> str:
        """Returns current active RX port name."""
        return self._current_rx_port

    @property
    def current_baud(self) -> int:
        """Returns current baud rate."""
        return self._current_baud

    def connect_ports(
        self,
        tx_port: str,
        rx_port: str,
        baudrate: int = DEFAULT_BAUDRATE,
    ) -> None:
        """Requests opening independent TX/RX serial ports in background thread."""
        logger.info(
            "%s Manager connect requested: tx=%s rx=%s baud=%d (dispatch to worker thread)",
            LogTag.HW_SERIAL,
            tx_port,
            rx_port,
            baudrate,
        )
        self._sig_open_dual.emit(tx_port, rx_port, baudrate)

    def connect_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Requests opening a single shared serial port (backward-compatible)."""
        self.connect_ports(port_name, port_name, baudrate)

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

    def get_port_list(self) -> list[str]:
        """Scans for available serial ports on the host system."""
        return scan_available_ports(self._include_link)

    def get_port_info_list(self) -> list[PortInfo]:
        """Scans for available serial ports including VID/PID/description."""
        return scan_available_port_infos(self._include_link)

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
                    "%s SerialManager thread did not stop in %d ms (thread_running=%s, port=%s)",
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
