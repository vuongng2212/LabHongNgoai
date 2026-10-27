"""Unit tests for MockSerialPort hardware simulator."""

from __future__ import annotations

from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.framer import pack_frame, parse_frames_from_buffer


class TestMockSerial:
    """Test suite for mock hardware simulator."""

    def test_open_close(self) -> None:
        port = MockSerialPort()
        initial_state = port.is_open
        assert initial_state is False

        port.open()
        open_state = port.is_open
        assert open_state is True

        port.close()
        closed_state = port.is_open
        assert closed_state is False

    def test_direct_injection_and_read(self) -> None:
        port = MockSerialPort()
        port.open()
        port.inject_rx_data(b"HELLO_STM32")
        assert port.in_waiting == 11
        assert port.read(5) == b"HELLO"
        assert port.read() == b"_STM32"
        assert port.in_waiting == 0

    def test_auto_ping_pong_loopback(self) -> None:
        port = MockSerialPort(simulated_latency_ms=10.0)
        port.open()

        ping_frame = pack_frame(seq=99, opcode=Opcode.PING, payload=b"OPTICAL_TEST")
        port.write(ping_frame)

        port.flush_due(force=True)
        assert port.in_waiting > 0
        rx_data = port.read(port.in_waiting)
        packets, _ = parse_frames_from_buffer(rx_data)

        assert len(packets) == 1
        assert packets[0].opcode == Opcode.PONG
        assert packets[0].seq == 99
        assert packets[0].payload == b"OPTICAL_TEST"

    def test_los_blockage_drops_packets(self) -> None:
        port = MockSerialPort()
        port.open()
        port.set_los_blocked(True)

        ping_frame = pack_frame(seq=1, opcode=Opcode.PING, payload=b"TEST")
        port.write(ping_frame)

        assert port.in_waiting == 0
