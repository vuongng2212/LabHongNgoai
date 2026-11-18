"""Unit tests for the virtual COM1/COM2 transport simulator."""

from __future__ import annotations

import pytest

from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.protocol.constants import BAUD_COMMANDS, DEFAULT_BAUDRATE, SEQ_END
from ir_terminal.protocol.framer import pack_packet, parse_stream
from ir_terminal.protocol.packet import WinFormPacket


class TestMockSerial:
    """Verifies loopback, LOS, loss and baud-command simulation behaviour."""

    def test_open_and_close_clear_state(self) -> None:
        port = MockSerialPort()
        initial_state = port.is_open
        port.open()
        open_state = port.is_open
        port.close()
        closed_state = port.is_open

        assert initial_state is False
        assert open_state is True
        assert closed_state is False

    def test_injected_bytes_are_readable(self) -> None:
        port = MockSerialPort()
        port.open()
        port.inject_rx_data(b"HELLO_STM32")

        assert port.in_waiting == 11
        assert port.read(5) == b"HELLO"
        assert port.read() == b"_STM32"
        assert port.in_waiting == 0

    def test_complete_frames_are_echoed_on_data_port(self) -> None:
        port = MockSerialPort(auto_respond=True, simulated_latency_ms=0.0)
        port.open()

        frame = pack_packet(sequence=7, data=b"chunk-seven")
        assert port.write(frame) == len(frame)

        packets, remaining = parse_stream(bytearray(port.read()))
        assert remaining == bytearray()
        assert packets == [WinFormPacket(sequence=7, length=11, data=b"chunk-seven")]

    def test_partial_frame_is_not_echoed_until_complete(self) -> None:
        port = MockSerialPort(auto_respond=True, simulated_latency_ms=0.0)
        port.open()
        frame = pack_packet(sequence=1, data=b"payload")

        port.write(frame[:4])
        assert port.read() == b""

        port.write(frame[4:])
        packets, remaining = parse_stream(bytearray(port.read()))
        assert remaining == bytearray()
        assert len(packets) == 1
        assert packets[0].sequence == 1

    def test_flush_due_releases_pending_echoes_deterministically(self) -> None:
        port = MockSerialPort(auto_respond=True, simulated_latency_ms=60_000.0)
        port.open()
        port.write(pack_packet(sequence=SEQ_END, data=b""))
        assert port.in_waiting == 0

        delivered = port.flush_due(force=True)
        assert delivered == 1
        packets, _ = parse_stream(bytearray(port.read()))
        assert [packet.sequence for packet in packets] == [SEQ_END]

    def test_los_blockage_discards_outgoing_frames(self) -> None:
        port = MockSerialPort(auto_respond=True, simulated_latency_ms=0.0)
        port.open()
        port.set_los_blocked(True)

        port.write(pack_packet(sequence=0, data=b"blocked"))
        assert port.in_waiting == 0
        assert port.dropped_by_los == 1

        port.set_los_blocked(False)
        port.write(pack_packet(sequence=0, data=b"clear"))
        assert port.in_waiting > 0

    def test_loss_rate_can_drop_every_frame(self) -> None:
        port = MockSerialPort(auto_respond=True, simulated_latency_ms=0.0, simulated_loss_rate=1.0)
        port.open()
        port.write(pack_packet(sequence=0, data=b"lost"))

        assert port.in_waiting == 0
        assert port.dropped_by_loss == 1

    @pytest.mark.parametrize(("baud", "code"), sorted(BAUD_COMMANDS.items()))
    def test_baud_command_recording(self, baud: int, code: str) -> None:
        port = MockSerialPort(baudrate=DEFAULT_BAUDRATE)
        port.open()

        assert port.send_baud_command(baud) == code
        assert port.last_baud_command == (baud, code)
        assert port.sent_baud_commands == [(baud, code)]

    def test_baud_command_requires_open_control_channel(self) -> None:
        port = MockSerialPort()
        with pytest.raises(RuntimeError):
            port.send_baud_command(9600)

    def test_unsupported_baud_commands_are_rejected(self) -> None:
        port = MockSerialPort(ctrl_port=None)
        port.open()
        assert port.has_control_port is False
        with pytest.raises(ValueError):
            port.send_baud_command(1200)
        with pytest.raises(ValueError):
            port.reconfigure_data_baud(1200)

    def test_reconfigure_data_baud_updates_simulated_rate(self) -> None:
        port = MockSerialPort(baudrate=19200)
        port.open()

        port.reconfigure_data_baud(4800)
        assert port.baudrate == 4800
        assert port.simulated_optical_baud == 4800

    def test_writes_require_open_transport(self) -> None:
        port = MockSerialPort()
        with pytest.raises(RuntimeError):
            port.write(b"\xAA\x55")
