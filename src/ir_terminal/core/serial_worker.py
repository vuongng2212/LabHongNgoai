"""Non-blocking Serial Worker implementing Worker-Object Pattern on dedicated QThread."""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
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
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.protocol.framer import FramerStats, parse_frames_with_stats
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview
from ir_terminal.utils.path_helpers import is_dual_mock_enabled

logger = logging.getLogger(__name__)


def _natural_port_key(name: str) -> str:
    """Sort key placing COM2 before COM10 (natural numeric ordering)."""
    return "".join(
        f"{int(part):08d}" if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)
    )


def _windows_registry_ports() -> list[str]:
    """Returns COM names from the Windows registry (virtual-COM fallback).

    Some virtual COM drivers (older com0com, some USB-serial stacks) do not
    appear in ``serial.tools.list_ports`` but are registered under
    ``HARDWARE\\DEVICEMAP\\SERIALCOMM``. Non-Windows hosts return an empty list.
    """
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        # winreg does not exist on non-Windows hosts.
        return []
    ports: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            index = 0
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if value:
                    ports.append(str(value))
                index += 1
    except OSError:
        return []
    return ports


@dataclass(slots=True)
class PortInfo:
    """Detailed metadata describing a single selectable serial port."""

    device: str
    description: str
    manufacturer: str | None
    vid: str | None
    pid: str | None
    hwid: str
    label: str

    def to_dict(self) -> dict[str, str | None]:
        """Serializes the port metadata into a JSON-friendly dictionary."""
        return {
            "device": self.device,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "vid": self.vid,
            "pid": self.pid,
            "hwid": self.hwid,
            "label": self.label,
        }


def _format_hex_identifier(value: int | None) -> str | None:
    """Formats a numeric USB identifier as a 4-character uppercase hex string."""
    if value is None:
        return None
    return f"{int(value) & 0xFFFF:04X}"


def _friendly_label(device: str, description: str, vid: str | None, pid: str | None) -> str:
    """Builds a human-friendly display label for a hardware serial port."""
    if vid and pid:
        return f"{description or device} [{vid}:{pid}]"
    if description:
        return f"{device} - {description}"
    return device


def _virtual_port_info(device: str, description: str) -> PortInfo:
    """Builds a PortInfo entry for an in-process virtual (non-hardware) port."""
    return PortInfo(
        device=device,
        description=description,
        manufacturer=None,
        vid=None,
        pid=None,
        hwid="",
        label=f"{device} ({description})",
    )


class SerialWorker(QObject):
    """Background worker managing serial port I/O and emitting parsed packets."""

    # Qt Signals
    connected: ClassVar[Signal] = Signal(str, int)
    ports_connected: ClassVar[Signal] = Signal(str, str, int)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_data_received: ClassVar[Signal] = Signal(bytes)
    raw_data_sent: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(Packet)  # (Packet)
    ports_changed: ClassVar[Signal] = Signal(list)
    framer_error: ClassVar[Signal] = Signal(str, int)  # (kind, count delta)

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
        """Initializes non-blocking serial worker instance.

        Args:
            link_endpoints: Optional LINK_* -> (host, port) map for dual mode.
            include_link: Whether LINK_A/B are offered in the port list
                (defaults to the IR_ENABLE_DUAL_MOCK env gate).
            link_connect_retries: Extra LINK connect attempts on open.
            link_retry_interval_sec: Delay between LINK connect attempts.
            link_connect_timeout_sec: Per-attempt LINK connect timeout.
            link_token: Optional broker handshake token.
        """
        super().__init__()
        self._transport: ITransport | None = None
        self._rx_accumulation = bytearray()
        self._rx_consumed = 0
        self._rx_dropped = 0
        self._framer_stats = FramerStats()
        self._last_ports: list[PortInfo] = []
        self._port_poll_timer: QTimer | None = None
        self._poll_timer: QTimer | None = None
        self._link_endpoints = link_endpoints
        self._include_link = is_dual_mock_enabled() if include_link is None else include_link
        self._link_connect_retries = link_connect_retries
        self._link_retry_interval_sec = link_retry_interval_sec
        self._link_connect_timeout_sec = link_connect_timeout_sec
        self._link_token = link_token
        self._link_lost_pending = False
        self._current_tx_port = ""
        self._current_rx_port = ""
        self._current_baud = DEFAULT_BAUDRATE

    @property
    def current_tx_port(self) -> str:
        """Returns the active TX port name."""
        return self._current_tx_port

    @property
    def current_rx_port(self) -> str:
        """Returns the active RX port name."""
        return self._current_rx_port

    @property
    def current_baud(self) -> int:
        """Returns the active baud rate."""
        return self._current_baud

    def _on_link_lost(self) -> None:
        """Records that the socket transport dropped (handled on next poll)."""
        self._link_lost_pending = True

    @Slot(str, int)
    def open_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Opens one port used for both RX and TX (backward-compatible entry).

        Args:
            port_name: Target port identifier.
            baudrate: Baud rate integer.
        """
        self.open_ports(port_name, port_name, baudrate)

    @Slot(str, str, int)
    def open_ports(self, tx_port: str, rx_port: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Opens independent TX/RX serial connections and starts periodic polling.

        Args:
            tx_port: Target port identifier used for writes.
            rx_port: Target port identifier used for reads.
            baudrate: Baud rate integer.
        """
        tx_name = (tx_port or "").strip()
        rx_name = (rx_port or "").strip() or tx_name
        try:
            self.close_port()
            logger.info(
                "%s Opening serial ports: tx=%s rx=%s at %d bps (thread=%s)",
                LogTag.HW_SERIAL,
                tx_name,
                rx_name,
                baudrate,
                "worker",
            )

            self._transport = create_transport(
                tx_port=tx_name,
                rx_port=rx_name,
                baudrate=baudrate,
                link_endpoints=self._link_endpoints,
                link_connect_retries=self._link_connect_retries,
                link_retry_interval_sec=self._link_retry_interval_sec,
                link_connect_timeout_sec=self._link_connect_timeout_sec,
                link_token=self._link_token,
            )
            transport_kind = type(self._transport).__name__
            if hasattr(self._transport, "on_link_lost"):
                self._transport.on_link_lost = self._on_link_lost
            self._link_lost_pending = False
            logger.info(
                "%s Transport created: %s for tx=%s rx=%s baud=%d",
                LogTag.HW_SERIAL,
                transport_kind,
                tx_name,
                rx_name,
                baudrate,
            )

            self._transport.open()
            self._rx_accumulation = bytearray()
            self._rx_consumed = 0
            self._rx_dropped = 0
            self._framer_stats = FramerStats()
            self._current_tx_port = tx_name
            self._current_rx_port = rx_name
            self._current_baud = baudrate

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

            logger.info(
                "%s Port OPENED: tx=%s rx=%s @ %d bps via %s poll=%dms rx_cap=%dB",
                LogTag.HW_SERIAL,
                tx_name,
                rx_name,
                baudrate,
                transport_kind,
                poll_ms,
                RING_BUFFER_CAPACITY,
            )
            self.connected.emit(tx_name, baudrate)
            self.ports_connected.emit(tx_name, rx_name, baudrate)

        except Exception as exc:
            self._current_tx_port = ""
            self._current_rx_port = ""
            logger.exception(
                "%s Failed to open serial ports tx=%s rx=%s @ %d bps (%s: %s)",
                LogTag.HW_SERIAL,
                tx_name,
                rx_name,
                baudrate,
                type(exc).__name__,
                exc,
            )
            self.error_occurred.emit(f"Connection failed: {exc}")

    @Slot()
    def close_port(self) -> None:
        """Stops polling timer and cleanly closes serial transport."""
        was_open = self._transport is not None and self._transport.is_open
        transport_kind = type(self._transport).__name__ if self._transport else "none"
        buffered = len(self._rx_accumulation) - self._rx_consumed
        stats = self._framer_stats.snapshot()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._stop_port_watcher()

        if self._transport is not None:
            try:
                self._transport.close()
            except Exception as exc:
                logger.warning(
                    "%s Error during transport close (%s): %s: %s",
                    LogTag.HW_SERIAL,
                    transport_kind,
                    type(exc).__name__,
                    exc,
                )

        self._transport = None
        self._rx_accumulation = bytearray()
        self._rx_consumed = 0
        self._link_lost_pending = False
        self._current_tx_port = ""
        self._current_rx_port = ""
        # Only emit on a real connected->disconnected transition. open_port()
        # calls close_port() as cleanup, and emitting here would fake a
        # disconnect that wipes pending-connect UI state before the real
        # connected/error signal arrives.
        if was_open:
            logger.info(
                "%s Port CLOSED (%s): buffered=%dB dropped=%dB frames_ok=%d "
                "crc_err=%d eof_err=%d len_err=%d noise=%dB",
                LogTag.HW_SERIAL,
                transport_kind,
                buffered,
                self._rx_dropped,
                stats["frames_ok"],
                stats["crc_errors"],
                stats["eof_errors"],
                stats["len_errors"],
                stats["noise_bytes"],
            )
            self.disconnected.emit()

    @Slot(bytes)
    def inject_rx_data(self, data: bytes) -> None:
        """Thread-safe RX injection slot (replaces direct transport access).

        Args:
            data: Raw bytes to feed into the RX accumulation buffer.
        """
        if not data:
            return
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s RX inject: len=%dB hex=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
            )
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
                "%s RX accumulation overflow: dropped %d oldest bytes "
                "(cap=%dB total_dropped=%dB incoming_hex=%s)",
                LogTag.HW_SERIAL,
                overflow,
                RING_BUFFER_CAPACITY,
                self._rx_dropped,
                format_hex_preview(chunk, max_bytes=16),
            )

    def _drain_rx_buffer(self, emit_raw: bool = True) -> None:
        """Parses accumulated bytes and emits packets with framer diagnostics."""
        before = self._framer_stats.snapshot()
        pending = memoryview(self._rx_accumulation)[self._rx_consumed :]
        pending_len = len(pending)
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
                logger.warning(
                    "%s Framer %s x%d (scanned=%dB parsed=%d totals=%s)",
                    LogTag.PROTO_FRAME,
                    kind,
                    delta,
                    pending_len,
                    len(packets),
                    after,
                )
                self.framer_error.emit(kind, delta)
        if after["noise_bytes"] != before["noise_bytes"]:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s Framer noise_bytes=%d (+%d) scanned=%dB",
                    LogTag.PROTO_FRAME,
                    after["noise_bytes"],
                    after["noise_bytes"] - before["noise_bytes"],
                    pending_len,
                )
        for pkt in packets:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s RX packet %s crc=0x%08X payload_hex=%s",
                    LogTag.PROTO_FRAME,
                    format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
                    pkt.crc32 & 0xFFFFFFFF,
                    format_hex_preview(pkt.payload, max_bytes=16),
                )
            self.packet_received.emit(pkt)

    @Slot(bytes)
    def send_bytes(self, data: bytes) -> None:
        """Transmits raw binary bytes to the serial transport.

        Args:
            data: Raw bytes to write out to UART.
        """
        if not self._transport or not self._transport.is_open:
            logger.warning(
                "%s TX rejected (not connected): len=%dB hex=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
            )
            self.error_occurred.emit("Cannot send data: Serial port is not connected")
            return

        try:
            self._transport.write(data)
            if logger.isEnabledFor(logging.DEBUG):
                self._describe_tx_bytes(data)
            self.raw_data_sent.emit(data)
        except Exception as exc:
            logger.exception(
                "%s Write error (%s: %s): len=%dB hex=%s",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
                len(data),
                format_hex_preview(data),
            )
            self.error_occurred.emit(f"Write error: {exc}")

    @Slot()
    def _poll_serial(self) -> None:
        """Periodically polls for incoming serial bytes."""
        if self._transport is None:
            return
        if self._link_lost_pending:
            self._link_lost_pending = False
            logger.error(
                "%s Link LOST: peer/broker disconnected (port=%s); port closed",
                LogTag.HW_SERIAL,
                type(self._transport).__name__,
            )
            self.error_occurred.emit("Link lost: peer or broker disconnected")
            self.close_port()
            # close_port() sees is_open already False and stays silent, so the
            # UI disconnect transition is emitted explicitly here.
            self.disconnected.emit()
            return
        if not self._transport.is_open:
            return

        try:
            available = self._transport.in_waiting
            if available > 0:
                chunk = self._transport.read(available)
                if chunk:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "%s Poll RX: req=%dB got=%dB hex=%s",
                            LogTag.HW_SERIAL,
                            available,
                            len(chunk),
                            format_hex_preview(chunk),
                        )
                    self.raw_data_received.emit(chunk)
                    # Extend bytearray (amortized, no full copy) and parse a
                    # memoryview over the unconsumed tail. Compaction only when
                    # the consumed prefix grows large, avoiding memmove per poll.
                    self._append_rx_chunk(chunk)
                    self._drain_rx_buffer(emit_raw=False)

        except Exception as exc:
            logger.exception(
                "%s Serial read poll error (%s: %s); closing port",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
            self.error_occurred.emit(f"Read error: {exc}")
            self.close_port()

    def _describe_tx_bytes(self, data: bytes) -> None:
        """Logs a one-line TX descriptor, decoding the frame header when possible."""
        if len(data) >= 6 and data[0] == 0x55 and data[1] == 0xAA:
            seq = data[2]
            opcode = data[3]
            payload_len = int.from_bytes(data[4:6], "little")
            logger.debug(
                "%s TX bytes %s frame_len=%dB hex=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(seq, opcode, payload_len),
                len(data),
                format_hex_preview(data),
            )
        else:
            logger.debug(
                "%s TX raw len=%dB hex=%s (no SOF header)",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
            )

    def _start_port_watcher(self) -> None:
        """Starts hotplug polling (emits ports_changed only on change)."""
        self._last_ports = scan_available_port_infos(self._include_link)
        logger.debug(
            "%s Hotplug watcher started: initial_ports=%s",
            LogTag.HW_SERIAL,
            self._last_ports,
        )
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
            current = scan_available_port_infos(self._include_link)
            if current != self._last_ports:
                logger.info(
                    "%s Hotplug ports changed: %s -> %s",
                    LogTag.HW_SERIAL,
                    self._last_ports,
                    current,
                )
                self._last_ports = current
                self.ports_changed.emit(current)
        except Exception as exc:
            logger.debug(
                "%s Port watcher poll failed (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )

    @property
    def rx_dropped_bytes(self) -> int:
        """Returns cumulative RX accumulation overflow drops."""
        return self._rx_dropped

    @property
    def framer_stats_snapshot(self) -> dict[str, int]:
        """Returns current framer diagnostics."""
        return self._framer_stats.snapshot()


def scan_available_port_infos(include_link: bool | None = None) -> list[PortInfo]:
    """Scans available ports and returns detailed metadata for each one.

    Merges ``pyserial`` enumeration with the Windows registry so virtual COM
    drivers that pyserial misses still show up, then appends
    ``MOCK_COM_LOOPBACK`` and (when enabled) ``LINK_A``/``LINK_B``.

    Args:
        include_link: Whether to list the LINK_A/B virtual ports. None falls
            back to the IR_ENABLE_DUAL_MOCK env gate (back-compat).

    Returns:
        Naturally sorted, de-duplicated list of PortInfo records.
    """
    infos: list[PortInfo] = []
    seen: set[str] = set()
    for listed in list_ports.comports():
        device = listed.device
        if device in seen:
            continue
        description = (listed.description or "").strip()
        if description.lower() in ("n/a", "none"):
            description = ""
        hwid_str = (listed.hwid or "").strip()
        if hwid_str.lower() in ("n/a", "none"):
            hwid_str = ""
        subsystem_str = str(getattr(listed, "subsystem", "") or "").strip().lower()
        if (
            device.startswith("/dev/ttyS")
            and not hwid_str
            and not description
            and subsystem_str in ("serial-base", "")
        ):
            continue
        seen.add(device)
        vid = _format_hex_identifier(listed.vid)
        pid = _format_hex_identifier(listed.pid)
        infos.append(
            PortInfo(
                device=device,
                description=description,
                manufacturer=listed.manufacturer or None,
                vid=vid,
                pid=pid,
                hwid=listed.hwid or "",
                label=_friendly_label(device, description, vid, pid),
            )
        )
    for extra in _windows_registry_ports():
        if extra in seen:
            continue
        seen.add(extra)
        infos.append(
            PortInfo(
                device=extra,
                description="Windows Registry Serial Port",
                manufacturer=None,
                vid=None,
                pid=None,
                hwid="",
                label=f"{extra} - Windows Registry Serial Port",
            )
        )
    try:
        from pathlib import Path

        for pty_file in sorted(Path("/tmp").glob("LINK_*")):
            pty_path = str(pty_file)
            if pty_path not in seen and (pty_file.exists() or pty_file.is_symlink()):
                seen.add(pty_path)
                infos.append(
                    PortInfo(
                        device=pty_path,
                        description="Virtual Dual COM (PTY)",
                        manufacturer=None,
                        vid=None,
                        pid=None,
                        hwid="",
                        label=f"{pty_path} - Virtual Dual COM (PTY)",
                    )
                )
    except OSError:
        pass
    infos.append(_virtual_port_info("MOCK_COM_LOOPBACK", "Virtual Loopback"))
    link_enabled = is_dual_mock_enabled() if include_link is None else include_link
    if link_enabled:
        infos.append(_virtual_port_info("LINK_A", "Virtual Dual-Link"))
        infos.append(_virtual_port_info("LINK_B", "Virtual Dual-Link"))
        infos.append(_virtual_port_info("LINK_A_TX", "Virtual Dual-Link TX"))
        infos.append(_virtual_port_info("LINK_A_RX", "Virtual Dual-Link RX"))
        infos.append(_virtual_port_info("LINK_B_TX", "Virtual Dual-Link TX"))
        infos.append(_virtual_port_info("LINK_B_RX", "Virtual Dual-Link RX"))
    return sorted(infos, key=lambda info: _natural_port_key(info.device))


def scan_available_ports(include_link: bool | None = None) -> list[str]:
    """Scans and returns available port device names plus MOCK options.

    Backward-compatible string projection of :func:`scan_available_port_infos`.

    Args:
        include_link: Whether to list the LINK_A/B virtual ports. None falls
            back to the IR_ENABLE_DUAL_MOCK env gate (back-compat).

    Returns:
        Naturally sorted, de-duplicated list of port identifiers.
    """
    return [info.device for info in scan_available_port_infos(include_link)]
