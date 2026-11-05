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


class TestMockBaudControl:
    """Verifies the mock STM32 answers SET_BAUD and can emit NOTIFY_BAUD."""

    def test_set_baud_gets_baud_ok_reply(self) -> None:
        from ir_terminal.protocol.control_channel import parse_control_line

        port = MockSerialPort(simulated_latency_ms=0.0)
        port.open()
        port.write(b"SET_BAUD:19200\n")

        port.flush_due(force=True)
        reply = port.read(port.in_waiting)

        message = parse_control_line(reply)
        assert message is not None
        assert message.kind == "BAUD_OK"
        assert message.baudrate == 19200
        assert port.simulated_optical_baud == 19200

    def test_notify_baud_injects_hardware_event(self) -> None:
        from ir_terminal.protocol.control_channel import parse_control_line

        port = MockSerialPort()
        port.open()
        port.notify_baud(4800)

        message = parse_control_line(port.read(port.in_waiting))
        assert message is not None
        assert message.kind == "NOTIFY_BAUD"
        assert message.baudrate == 4800
        assert port.simulated_optical_baud == 4800

    def test_frame_handling_still_works_after_control_traffic(self) -> None:
        port = MockSerialPort(simulated_latency_ms=0.0)
        port.open()
        port.write(b"SET_BAUD:9600\n")
        port.flush_due(force=True)
        port.read(port.in_waiting)

        ping_frame = pack_frame(seq=21, opcode=Opcode.PING, payload=b"AFTER_CTRL")
        port.write(ping_frame)
        port.flush_due(force=True)
        packets, _ = parse_frames_from_buffer(port.read(port.in_waiting))

        assert len(packets) == 1
        assert packets[0].opcode == Opcode.PONG
        assert packets[0].payload == b"AFTER_CTRL"
