"""Mock hardware serial port simulator for TDD and UI integration testing."""

from __future__ import annotations

import logging
import random
import threading
import time

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.ring_buffer import ByteRingBuffer
from ir_terminal.protocol.constants import RING_BUFFER_CAPACITY, Opcode
from ir_terminal.protocol.framer import pack_frame, parse_frames_from_buffer
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview

logger = logging.getLogger(__name__)


class MockSerialPort(ITransport):
    """Simulates a physical STM32 UART port with configurable physical channel anomalies.

    Supports:
        - Echoing PONG in response to PING packets (with simulated RTT latency).
        - Simulating optical packet loss / drop rate.
        - Simulating Line-of-Sight (LOS) blockage.
    """

    def __init__(
        self,
        port_name: str = "MOCK_COM1",
        baudrate: int = 115200,
        simulated_latency_ms: float = 30.0,
        simulated_loss_rate: float = 0.0,
        auto_respond: bool = True,
    ) -> None:
        """Initializes mock serial transport.

        Args:
            port_name: Virtual COM identifier.
            baudrate: Simulated baud rate.
            simulated_latency_ms: Artificial round-trip delay in milliseconds.
            simulated_loss_rate: Float 0.0 to 1.0 representing probability of packet drop.
            auto_respond: True to auto-echo PONG/ACK internally; False for pure transport tube.
        """
        self._port_name = port_name
        self._baudrate = baudrate
        self._latency_s = simulated_latency_ms / 1000.0
        self._loss_rate = simulated_loss_rate
        self._auto_respond = auto_respond
        self._is_open = False
        self._rx_buffer = ByteRingBuffer(capacity=RING_BUFFER_CAPACITY)
        self._tx_buffer = bytearray()
        self._los_blocked = False
        # Non-blocking latency queue: (due_monotonic, frame_bytes). Responses are
        # enqueued in write() and delivered when due in read()/in_waiting, so the
        # worker event loop is never blocked by time.sleep().
        self._pending: list[tuple[float, bytes]] = []
        self._dropped_by_loss: int = 0
        self._dropped_by_los: int = 0
        self._lock = threading.Lock()

    def open(self) -> None:
        """Opens virtual mock serial connection."""
        self._is_open = True
        self._rx_buffer.clear()
        self._tx_buffer = bytearray()
        with self._lock:
            self._pending = []
        logger.info(
            "%s Mock open: port=%s baud=%d latency=%.1fms loss=%.3f auto_respond=%s",
            LogTag.HW_SERIAL,
            self._port_name,
            self._baudrate,
            self._latency_s * 1000.0,
            self._loss_rate,
            self._auto_respond,
        )

    def close(self) -> None:
        """Closes virtual mock serial connection."""
        was_open = self._is_open
        self._is_open = False
        self._rx_buffer.clear()
        self._tx_buffer = bytearray()
        with self._lock:
            pending = len(self._pending)
            self._pending = []
        logger.info(
            "%s Mock closed: port=%s was_open=%s dropped_pending=%d "
            "loss_drops=%d los_drops=%d",
            LogTag.HW_SERIAL,
            self._port_name,
            was_open,
            pending,
            self._dropped_by_loss,
            self._dropped_by_los,
        )

    @property
    def is_open(self) -> bool:
        """Returns True if virtual port is active."""
        return self._is_open

    def set_los_blocked(self, blocked: bool) -> None:
        """Simulates physical obstacle blocking the optical beam.

        Args:
            blocked: True to block optical transmission; False to restore link.
        """
        self._los_blocked = blocked
        logger.warning(
            "%s Mock LOS %s: port=%s (beam %s)",
            LogTag.OPTICAL_PHY,
            "BLOCKED — packets will vanish" if blocked else "CLEARED — link restored",
            self._port_name,
            "obstructed" if blocked else "clear",
        )

    def write(self, data: bytes) -> int:
        """Simulates sending data to STM32 and automatically handles PING/PONG responses.

        Args:
            data: Raw frame bytes sent by PC application.

        Returns:
            Number of bytes written.
        """
        if not self._is_open:
            logger.warning(
                "%s Mock write rejected (closed): port=%s len=%dB",
                LogTag.HW_SERIAL,
                self._port_name,
                len(data),
            )
            raise RuntimeError("Cannot write to closed MockSerialPort")

        if self._los_blocked:
            # Beam is physically blocked; packets disappear into void.
            # Counted (not silent) so ARQ/telemetry can observe the blockage.
            with self._lock:
                self._dropped_by_los += 1
                los_total = self._dropped_by_los
            logger.debug(
                "%s Mock TX dropped by LOS: port=%s len=%dB hex=%s total_los_drops=%d",
                LogTag.OPTICAL_PHY,
                self._port_name,
                len(data),
                format_hex_preview(data),
                los_total,
            )
            return len(data)

        # Check if packet should be dropped due to simulated channel noise
        if self._loss_rate > 0 and random.random() < self._loss_rate:
            # Packet dropped by simulated atmospheric noise (counted).
            with self._lock:
                self._dropped_by_loss += 1
                loss_total = self._dropped_by_loss
            logger.debug(
                "%s Mock TX dropped by noise: port=%s loss_rate=%.3f len=%dB hex=%s "
                "total_loss_drops=%d",
                LogTag.OPTICAL_PHY,
                self._port_name,
                self._loss_rate,
                len(data),
                format_hex_preview(data),
                loss_total,
            )
            return len(data)

        # Parse outgoing frame to detect commands like PING, CHAT, FILE and auto-respond (if enabled)
        if self._auto_respond:
            self._tx_buffer.extend(data)
            packets, remaining = parse_frames_from_buffer(self._tx_buffer)
            # remaining is bytes; wrap back into bytearray without extra scan
            self._tx_buffer = bytearray(remaining)

            for pkt in packets:
                if pkt.opcode == Opcode.PING:
                    self._handle_ping_packet(pkt.seq, pkt.payload)
                elif pkt.opcode == Opcode.DATA_TEXT:
                    self._handle_chat_packet(pkt.seq, pkt.payload)
                elif pkt.opcode in (Opcode.TRANSFER_START, Opcode.DATA_CHUNK, Opcode.TRANSFER_END):
                    self._handle_file_ack(pkt.seq)

        return len(data)

    def _schedule_response(self, frame: bytes, delay_s: float) -> None:
        """Enqueues a response frame for delivery after delay_s (non-blocking)."""
        if delay_s <= 0:
            self._rx_buffer.write(frame)
            return
        due = time.monotonic() + delay_s
        with self._lock:
            self._pending.append((due, frame))

    def _deliver_due(self) -> None:
        """Moves due responses into the RX buffer (called on worker thread)."""
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

    def _handle_ping_packet(self, seq: int, payload: bytes) -> None:
        """Generates a PONG response frame after simulated optical latency."""
        pong_frame = pack_frame(seq=seq, opcode=Opcode.PONG, payload=payload)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Mock auto-PONG %s latency=%.1fms",
                LogTag.OPTICAL_PHY,
                format_frame_summary(seq, Opcode.PONG, len(payload)),
                self._latency_s * 1000.0,
            )
        self._schedule_response(pong_frame, self._latency_s)

    def _handle_chat_packet(self, seq: int, payload: bytes) -> None:
        """Echoes back chat response from Station Bravo node."""
        # ACK the chat frame with matching sequence number
        ack_payload = bytes([seq, 0])
        ack_frame = pack_frame(seq=seq, opcode=Opcode.ACK, payload=ack_payload)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Mock auto-ACK chat %s payload_hex=%s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(seq, Opcode.ACK, len(ack_payload)),
                format_hex_preview(payload, max_bytes=16),
            )
        self._schedule_response(ack_frame, self._latency_s)

    def _handle_file_ack(self, seq: int) -> None:
        """Sends ACK for file transfer chunk."""
        # Near-instant ACK keeps loopback simulation snappy (chat PING keeps
        # the full simulated latency for realistic RTT display).
        delay = min(self._latency_s, 0.002)
        ack_payload = bytes([seq, 0])
        ack_frame = pack_frame(seq=seq, opcode=Opcode.ACK, payload=ack_payload)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Mock auto-ACK file %s delay=%.1fms",
                LogTag.ARQ_ENGINE,
                format_frame_summary(seq, Opcode.ACK, len(ack_payload)),
                delay * 1000.0,
            )
        self._schedule_response(ack_frame, delay)

    def read(self, size: int | None = None) -> bytes:
        """Reads received bytes from mock RX buffer.

        Args:
            size: Number of bytes to read, or None to read all available bytes.
        """
        if not self._is_open:
            return b""
        self._deliver_due()
        return self._rx_buffer.read(max_bytes=size)

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes in mock RX buffer."""
        if not self._is_open:
            return 0
        self._deliver_due()
        return len(self._rx_buffer)

    @property
    def dropped_by_loss(self) -> int:
        """Returns cumulative frames dropped by simulated loss."""
        with self._lock:
            return self._dropped_by_loss

    @property
    def dropped_by_los(self) -> int:
        """Returns cumulative frames dropped by LOS blockage."""
        with self._lock:
            return self._dropped_by_los

    def flush_due(self, force: bool = False) -> int:
        """Delivers due (or all, if force) pending responses for deterministic tests.

        Args:
            force: True to deliver all pending regardless of due time.

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
        """Directly pushes raw bytes into RX buffer for unit testing.

        Args:
            data: Byte sequence to inject.
        """
        self._rx_buffer.write(data)
