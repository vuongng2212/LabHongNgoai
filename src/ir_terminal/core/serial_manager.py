"""High-level coordinator for the dual-port serial worker thread."""

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
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    SHUTDOWN_WAIT_MS,
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.protocol.framer import pack_packet
from ir_terminal.utils.logger import LogTag, format_hex_preview
from ir_terminal.utils.path_helpers import is_dual_mock_enabled

logger = logging.getLogger(__name__)


class SerialManager(QObject):
    """Coordinates the worker QThread and exposes the hardware API to services."""

    connected: ClassVar[Signal] = Signal(str, int)
    ports_connected: ClassVar[Signal] = Signal(str, str, int)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_tx: ClassVar[Signal] = Signal(bytes)
    raw_rx: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(object)
    ports_changed: ClassVar[Signal] = Signal(list)
    baud_changed: ClassVar[Signal] = Signal(int)
    baud_command_sent: ClassVar[Signal] = Signal(int, str)

    _sig_open: ClassVar[Signal] = Signal(str, int)
    _sig_open_hw: ClassVar[Signal] = Signal(str, str, int, int)
    _sig_close: ClassVar[Signal] = Signal()
    _sig_send: ClassVar[Signal] = Signal(bytes)
    _sig_inject: ClassVar[Signal] = Signal(bytes)
    _sig_set_baud: ClassVar[Signal] = Signal(int)

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
        """Initializes the manager and starts its worker thread.

        Args:
            link_endpoints: Optional LINK_* to ``(host, port)`` map.
            include_link: Whether LINK ports appear in the scan list.
            link_connect_retries: Extra LINK connect attempts.
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
        self._sig_open_hw.connect(self._worker.open_hardware_ports)
        self._sig_close.connect(self._worker.close_port)
        self._sig_send.connect(self._worker.send_bytes)
        self._sig_inject.connect(self._worker.inject_rx_data)
        self._sig_set_baud.connect(self._worker.set_baud)

        self._worker.connected.connect(self.connected)
        self._worker.ports_connected.connect(self.ports_connected)
        self._worker.disconnected.connect(self.disconnected)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.raw_data_sent.connect(self.raw_tx)
        self._worker.raw_data_received.connect(self.raw_rx)
        self._worker.packet_received.connect(self.packet_received)
        self._worker.ports_changed.connect(self.ports_changed)
        self._worker.baud_changed.connect(self.baud_changed)
        self._worker.baud_command_sent.connect(self.baud_command_sent)

        self._thread.start()
        self._is_connected = False
        self._current_data_port = ""
        self._current_ctrl_port = ""
        self._current_baud = DEFAULT_BAUDRATE
        self._current_ctrl_baud = CONTROL_BAUDRATE
        self.ports_connected.connect(self._on_ports_connected)
        self._worker.disconnected.connect(self._on_disconnected)
        self._worker.baud_changed.connect(self._on_baud_changed)
        logger.debug(
            "%s Manager worker thread started: running=%s",
            LogTag.HW_SERIAL,
            self._thread.isRunning(),
        )

    def _on_ports_connected(self, data_port: str, ctrl_port: str, baud: int) -> None:
        """Stores the active port and baudrate state after a successful open."""
        self._is_connected = True
        self._current_data_port = data_port
        self._current_ctrl_port = ctrl_port
        self._current_baud = baud
        logger.info(
            "%s Manager link UP: COM1=%s @%d COM2=%s @%d",
            LogTag.HW_SERIAL,
            data_port,
            baud,
            ctrl_port or "<none>",
            self._current_ctrl_baud,
        )

    def _on_disconnected(self) -> None:
        """Clears connection state after the worker closes the transport."""
        logger.info(
            "%s Manager link DOWN: was COM1=%s @%d",
            LogTag.HW_SERIAL,
            self._current_data_port,
            self._current_baud,
        )
        self._is_connected = False
        self._current_data_port = ""
        self._current_ctrl_port = ""

    def _on_baud_changed(self, baudrate: int) -> None:
        """Stores the optical rate confirmed by the worker.

        Args:
            baudrate: New COM1 optical baudrate.
        """
        self._current_baud = int(baudrate)
        logger.info("%s Manager optical baudrate now %d bps", LogTag.BAUD_CTRL, baudrate)

    @property
    def is_connected(self) -> bool:
        """Returns True while the hardware link is open."""
        return self._is_connected

    @property
    def current_port(self) -> str:
        """Returns the active data port name."""
        return self._current_data_port

    @property
    def current_data_port(self) -> str:
        """Returns the active COM1 data port name."""
        return self._current_data_port

    @property
    def current_ctrl_port(self) -> str:
        """Returns the active COM2 control port name."""
        return self._current_ctrl_port

    @property
    def current_tx_port(self) -> str:
        """Returns the data port for TX-oriented callers."""
        return self._current_data_port

    @property
    def current_rx_port(self) -> str:
        """Returns the data port for RX-oriented callers."""
        return self._current_data_port

    @property
    def current_baud(self) -> int:
        """Returns the active optical data baudrate."""
        return self._current_baud

    @property
    def current_control_baud(self) -> int:
        """Returns the fixed COM2 control baudrate."""
        return self._current_ctrl_baud

    def connect_ports(
        self,
        data_port: str,
        ctrl_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Requests opening COM1 and COM2 in the worker thread.

        Args:
            data_port: COM1 optical data port.
            ctrl_port: COM2 control port, or None when no control channel exists.
            baudrate: Optical data baudrate.
            ctrl_baudrate: Fixed MCU control baudrate.
        """
        data_name = (data_port or "").strip()
        ctrl_name = (ctrl_port or "").strip()
        self._current_ctrl_baud = int(ctrl_baudrate)
        logger.info(
            "%s Manager connect: COM1=%s @%d COM2=%s @%d",
            LogTag.HW_SERIAL,
            data_name,
            baudrate,
            ctrl_name or "<none>",
            ctrl_baudrate,
        )
        self._sig_open_hw.emit(data_name, ctrl_name, int(baudrate), int(ctrl_baudrate))

    def connect_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Requests opening one data port without a control channel.

        Args:
            port_name: Port identifier used for COM1 data.
            baudrate: Optical data baudrate.
        """
        self.connect_ports(port_name, None, baudrate)

    def disconnect_port(self) -> None:
        """Requests closing the hardware link in the worker thread."""
        logger.info(
            "%s Manager disconnect requested: COM1=%s",
            LogTag.HW_SERIAL,
            self._current_data_port,
        )
        self._sig_close.emit()

    def inject_raw_rx(self, data: bytes) -> None:
        """Injects raw bytes through the worker thread.

        Args:
            data: Raw bytes to parse as an incoming optical stream.
        """
        self._sig_inject.emit(data)

    def send_raw(self, data: bytes) -> None:
        """Sends raw bytes through the worker thread.

        Args:
            data: Raw bytes to transmit on COM1.
        """
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX raw: len=%dB hex=%s connected=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
                self._is_connected,
            )
        self._sig_send.emit(data)

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Packs and transmits one WinForm frame.

        Args:
            sequence: Frame identifier.
            data: Payload bytes.
        """
        try:
            frame = pack_packet(sequence, data)
        except Exception:
            logger.exception(
                "%s Manager pack FAILED: seq=0x%08X len=%dB",
                LogTag.PROTO_FRAME,
                sequence,
                len(data),
            )
            raise
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX frame: seq=0x%08X payload=%dB frame=%dB",
                LogTag.PROTO_FRAME,
                sequence,
                len(data),
                len(frame),
            )
        self.send_raw(frame)

    def send_baud_command(self, baudrate: int) -> None:
        """Queues a COM2 baud switch and COM1 reconfigure.

        Args:
            baudrate: Target optical baudrate.
        """
        logger.info("%s Manager baud switch requested: %d", LogTag.BAUD_CTRL, baudrate)
        self._sig_set_baud.emit(int(baudrate))

    def get_port_list(self) -> list[str]:
        """Scans and returns available port identifiers."""
        return scan_available_ports(self._include_link)

    def get_port_info_list(self) -> list[PortInfo]:
        """Scans and returns available port metadata."""
        return scan_available_port_infos(self._include_link)

    def shutdown(self) -> None:
        """Stops the worker thread and releases hardware handles."""
        logger.info(
            "%s Manager shutdown: requesting worker close (COM1=%s connected=%s)",
            LogTag.HW_SERIAL,
            self._current_data_port,
            self._is_connected,
        )
        try:
            self._sig_close.emit()
            self._thread.quit()
            if not self._thread.wait(SHUTDOWN_WAIT_MS):
                logger.warning(
                    "%s SerialManager thread did not stop in %d ms",
                    LogTag.HW_SERIAL,
                    SHUTDOWN_WAIT_MS,
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
