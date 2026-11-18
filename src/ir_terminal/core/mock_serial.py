"""Virtual COM1/COM2 simulator used by TDD and UI integration tests."""

from __future__ import annotations

import logging
import random
import threading
import time

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.ring_buffer import ByteRingBuffer
from ir_terminal.protocol.constants import (
    BAUD_COMMANDS,
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    RING_BUFFER_CAPACITY,
)
from ir_terminal.protocol.framer import pack_packet, parse_stream
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)


class MockSerialPort(ITransport):
    """Simulates the COM1 data link and the COM2 control link in one object.

    The data channel can echo complete WinForm frames to emulate a remote peer
    on a loopback bench setup. The control channel records the two-character
    baud commands that would be sent to the STM32.
    """

    def __init__(
        self,
        data_port: str = "MOCK_COM1",
        ctrl_port: str | None = "MOCK_COM2",
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
        simulated_latency_ms: float = 30.0,
        simulated_loss_rate: float = 0.0,
        auto_respond: bool = True,
    ) -> None:
        """Initializes the virtual dual-port transport.

        Args:
            data_port: Virtual COM1 identifier.
            ctrl_port: Virtual COM2 identifier, or None to disable control.
            baudrate: Simulated optical baudrate for COM1.
            ctrl_baudrate: Simulated MCU baudrate for COM2.
            simulated_latency_ms: Echo latency in milliseconds.
            simulated_loss_rate: Probability in the 0.0 to 1.0 range of dropping
                one outgoing frame.
            auto_respond: True to echo complete frames back on the data channel.
        """
        self._data_port = data_port
        self._ctrl_port = ctrl_port or ""
        self._baudrate = int(baudrate)
        self._ctrl_baudrate = int(ctrl_baudrate)
        self._latency_s = simulated_latency_ms / 1000.0
        self._loss_rate = simulated_loss_rate
        self._auto_respond = auto_respond
        self._is_open = False
        self._rx_buffer = ByteRingBuffer(capacity=RING_BUFFER_CAPACITY)
        self._tx_accumulation = bytearray()
        self._simulated_optical_baud = int(baudrate)
        self._los_blocked = False
        self._pending: list[tuple[float, bytes]] = []
        self._dropped_by_loss: int = 0
        self._dropped_by_los: int = 0
        self._baud_commands: list[tuple[int, str]] = []
        self._lock = threading.Lock()

    @property
    def data_port(self) -> str:
        """Returns the virtual COM1 identifier."""
        return self._data_port

    @property
    def ctrl_port(self) -> str:
        """Returns the virtual COM2 identifier, or an empty string."""
        return self._ctrl_port

    @property
    def tx_port(self) -> str:
        """Returns the data port for compatibility with legacy callers."""
        return self._data_port

    @property
    def rx_port(self) -> str:
        """Returns the data port for compatibility with legacy callers."""
        return self._data_port

    @property
    def baudrate(self) -> int:
        """Returns the active optical data baudrate."""
        return self._baudrate

    @property
    def control_baudrate(self) -> int:
        """Returns the fixed MCU control baudrate."""
        return self._ctrl_baudrate

    @property
    def has_control_port(self) -> bool:
        """Returns True when a virtual COM2 control channel is configured."""
        return bool(self._ctrl_port)

    @property
    def sent_baud_commands(self) -> list[tuple[int, str]]:
        """Returns every recorded control command as (baud, code) pairs."""
        with self._lock:
            return list(self._baud_commands)

    @property
    def last_baud_command(self) -> tuple[int, str] | None:
        """Returns the most recent recorded control command, if any."""
        with self._lock:
            return self._baud_commands[-1] if self._baud_commands else None

    def open(self) -> None:
        """Opens the virtual transport and clears every buffer."""
        self._is_open = True
        self._rx_buffer.clear()
        self._tx_accumulation = bytearray()
        with self._lock:
            self._pending = []
        logger.info(
            "%s Mock open: data=%s ctrl=%s baud=%d ctrl_baud=%d latency=%.1fms "
            "loss=%.3f auto_respond=%s",
            LogTag.HW_SERIAL,
            self._data_port,
            self._ctrl_port or "<none>",
            self._baudrate,
            self._ctrl_baudrate,
            self._latency_s * 1000.0,
            self._loss_rate,
            self._auto_respond,
        )

    def close(self) -> None:
        """Closes the virtual transport and releases pending echoes."""
        was_open = self._is_open
        self._is_open = False
        self._rx_buffer.clear()
        self._tx_accumulation = bytearray()
        with self._lock:
            pending = len(self._pending)
            self._pending = []
        logger.info(
            "%s Mock closed: data=%s was_open=%s dropped_pending=%d loss_drops=%d "
            "los_drops=%d",
            LogTag.HW_SERIAL,
            self._data_port,
            was_open,
            pending,
            self._dropped_by_loss,
            self._dropped_by_los,
        )

    @property
    def is_open(self) -> bool:
        """Returns True while the virtual transport is active."""
        return self._is_open

    def set_los_blocked(self, blocked: bool) -> None:
        """Simulates a physical obstacle blocking the optical beam.

        Args:
            blocked: True to discard outgoing frames, False to restore the link.
        """
        self._los_blocked = blocked
        logger.warning(
            "%s Mock LOS %s: data=%s",
            LogTag.OPTICAL_PHY,
            "BLOCKED" if blocked else "CLEARED",
            self._data_port,
        )

    def write(self, data: bytes) -> int:
        """Writes bytes to the simulated COM1 data channel.

        Args:
            data: Raw frame bytes produced by the application.

        Returns:
            Number of bytes accepted.

        Raises:
            RuntimeError: If the transport is closed.
        """
        if not self._is_open:
            raise RuntimeError("Cannot write to closed MockSerialPort")
        if self._los_blocked:
            with self._lock:
                self._dropped_by_los += 1
            return len(data)
        if self._loss_rate > 0 and random.random() < self._loss_rate:
            with self._lock:
                self._dropped_by_loss += 1
            return len(data)
        self._tx_accumulation.extend(data)
        packets, remaining = parse_stream(self._tx_accumulation)
        self._tx_accumulation = remaining
        if self._auto_respond:
            for packet in packets:
                self._schedule_echo(pack_packet(packet.sequence, packet.data))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Mock TX: data=%s len=%dB frames=%d hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                len(data),
                len(packets),
                format_hex_preview(data),
            )
        return len(data)

    def _schedule_echo(self, frame: bytes) -> None:
        """Queues one loopback frame for delivery after the simulated latency."""
        if self._latency_s <= 0:
            self._rx_buffer.write(frame)
            return
        due = time.monotonic() + self._latency_s
        with self._lock:
            self._pending.append((due, frame))

    def _deliver_due(self) -> None:
        """Moves due loopback frames into the RX buffer."""
        now = time.monotonic()
        due_frames: list[bytes] = []
        with self._lock:
            if not self._pending:
                return
            remaining: list[tuple[float, bytes]] = []
            for due, frame in self._pending:
                if due <= now:
                    due_frames.append(frame)
                else:
                    remaining.append((due, frame))
            self._pending = remaining
        for frame in due_frames:
            self._rx_buffer.write(frame)

    def read(self, size: int | None = None) -> bytes:
        """Reads bytes from the simulated COM1 RX buffer.

        Args:
            size: Number of bytes to read, or None for every available byte.

        Returns:
            Extracted byte sequence.
        """
        if not self._is_open:
            return b""
        self._deliver_due()
        return self._rx_buffer.read(max_bytes=size)

    @property
    def in_waiting(self) -> int:
        """Returns the number of bytes buffered on the simulated COM1 RX path."""
        if not self._is_open:
            return 0
        self._deliver_due()
        return len(self._rx_buffer)

    def flush(self) -> None:
        """Flushes the simulated transmit path (no-op)."""

    def send_baud_command(self, baud: int) -> str:
        """Records one COM2 baud switch command and updates the simulated rate.

        Args:
            baud: Target optical baudrate.

        Returns:
            The two-character firmware command.

        Raises:
            ValueError: If the baudrate has no firmware command.
            RuntimeError: If the transport is closed.
        """
        code = BAUD_COMMANDS.get(int(baud))
        if code is None:
            raise ValueError(f"Unsupported optical baudrate {baud}")
        if not self._is_open or not self._ctrl_port:
            raise RuntimeError("Control channel is not open for baud commands")
        with self._lock:
            self._baud_commands.append((int(baud), code))
        logger.info(
            "%s Mock COM2 baud command: baud=%d code=%r data=%s ctrl=%s",
            LogTag.BAUD_CTRL,
            baud,
            code,
            self._data_port,
            self._ctrl_port or "<none>",
        )
        return code

    def reconfigure_data_baud(self, baud: int) -> None:
        """Updates the simulated COM1 baudrate.

        Args:
            baud: Target optical baudrate.

        Raises:
            ValueError: If the baudrate has no firmware command.
        """
        target = int(baud)
        if target not in BAUD_COMMANDS:
            raise ValueError(f"Unsupported optical baudrate {target}")
        previous = self._baudrate
        self._baudrate = target
        self._simulated_optical_baud = target
        logger.info(
            "%s Mock COM1 baudrate: %d -> %d bps",
            LogTag.BAUD_CTRL,
            previous,
            target,
        )

    @property
    def simulated_optical_baud(self) -> int:
        """Returns the optical baudrate currently simulated by the mock."""
        return self._simulated_optical_baud

    @property
    def dropped_by_loss(self) -> int:
        """Returns cumulative frames dropped by simulated noise."""
        with self._lock:
            return self._dropped_by_loss

    @property
    def dropped_by_los(self) -> int:
        """Returns cumulative frames dropped by simulated LOS blockage."""
        with self._lock:
            return self._dropped_by_los

    def flush_due(self, force: bool = False) -> int:
        """Delivers pending echo frames for deterministic tests.

        Args:
            force: True to deliver every pending frame regardless of its due time.

        Returns:
            Number of frames delivered.
        """
        due_frames: list[bytes] = []
        with self._lock:
            if force:
                due_frames = [frame for _, frame in self._pending]
                self._pending = []
            else:
                now = time.monotonic()
                remaining: list[tuple[float, bytes]] = []
                for due, frame in self._pending:
                    if due <= now:
                        due_frames.append(frame)
                    else:
                        remaining.append((due, frame))
                self._pending = remaining
        for frame in due_frames:
            self._rx_buffer.write(frame)
        return len(due_frames)

    def inject_rx_data(self, data: bytes) -> None:
        """Injects raw bytes into the COM1 RX buffer.

        Args:
            data: Byte sequence to inject.
        """
        self._rx_buffer.write(data)

    def inject_packet(self, packet: WinFormPacket) -> None:
        """Injects one pre-built packet into the COM1 RX buffer.

        Args:
            packet: Packet to serialize and deliver.
        """
        self._rx_buffer.write(pack_packet(packet.sequence, packet.data))
