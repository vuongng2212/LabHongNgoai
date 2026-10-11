"""Mock hardware serial port simulator for TDD and UI integration testing."""

from __future__ import annotations

import random
import time

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.ring_buffer import ByteRingBuffer
from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.framer import pack_frame, parse_frames_from_buffer


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
        self._rx_buffer = ByteRingBuffer(capacity=65536)
        self._tx_buffer = bytearray()
        self._los_blocked = False

    def open(self) -> None:
        """Opens virtual mock serial connection."""
        self._is_open = True
        self._rx_buffer.clear()
        self._tx_buffer = bytearray()

    def close(self) -> None:
        """Closes virtual mock serial connection."""
        self._is_open = False
        self._rx_buffer.clear()
        self._tx_buffer = bytearray()

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

    def write(self, data: bytes) -> int:
        """Simulates sending data to STM32 and automatically handles PING/PONG responses.

        Args:
            data: Raw frame bytes sent by PC application.

        Returns:
            Number of bytes written.
        """
        if not self._is_open:
            raise RuntimeError("Cannot write to closed MockSerialPort")

        if self._los_blocked:
            # Beam is physically blocked; packets disappear into void
            return len(data)

        # Check if packet should be dropped due to simulated channel noise
        if self._loss_rate > 0 and random.random() < self._loss_rate:
            # Packet dropped by simulated atmospheric noise
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

    def _handle_ping_packet(self, seq: int, payload: bytes) -> None:
        """Generates a PONG response frame after simulated optical latency."""
        if self._latency_s > 0:
            time.sleep(self._latency_s)

        pong_frame = pack_frame(seq=seq, opcode=Opcode.PONG, payload=payload)
        self._rx_buffer.write(pong_frame)

    def _handle_chat_packet(self, seq: int, payload: bytes) -> None:
        """Echoes back chat response from Station Bravo node."""
        if self._latency_s > 0:
            time.sleep(self._latency_s)
        # ACK the chat frame with matching sequence number
        ack_payload = bytes([seq, 0])
        ack_frame = pack_frame(seq=seq, opcode=Opcode.ACK, payload=ack_payload)
        self._rx_buffer.write(ack_frame)

    def _handle_file_ack(self, seq: int) -> None:
        """Sends ACK for file transfer chunk."""
        if self._latency_s > 0:
            # Near-instant ACK keeps loopback simulation snappy (chat PING keeps
            # the full simulated latency for realistic RTT display).
            time.sleep(min(self._latency_s, 0.002))
        ack_payload = bytes([seq, 0])
        ack_frame = pack_frame(seq=seq, opcode=Opcode.ACK, payload=ack_payload)
        self._rx_buffer.write(ack_frame)

    def read(self, size: int | None = None) -> bytes:
        """Reads received bytes from mock RX buffer.

        Args:
            size: Number of bytes to read, or None to read all available bytes.
        """
        if not self._is_open:
            return b""
        return self._rx_buffer.read(max_bytes=size)

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes in mock RX buffer."""
        if not self._is_open:
            return 0
        return len(self._rx_buffer)

    def inject_rx_data(self, data: bytes) -> None:
        """Directly pushes raw bytes into RX buffer for unit testing.

        Args:
            data: Byte sequence to inject.
        """
        self._rx_buffer.write(data)
