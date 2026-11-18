"""Integration tests for the COM1 data / COM2 control lifecycle."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    CONTROL_BAUDRATE,
    DEFAULT_OPTICAL_BAUDRATE,
    SEQ_HEADER,
)
from ir_terminal.protocol.framer import pack_header_payload
from ir_terminal.protocol.packet import WinFormPacket


class TestDualComLifecycle:
    """Exercises connect, baud switch, traffic and disconnect on COM1/COM2."""

    def test_full_lifecycle(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=3000) as connected:
                manager.connect_ports("MOCK_COM1", "MOCK_COM2", DEFAULT_OPTICAL_BAUDRATE)

            assert connected.args == [
                "MOCK_COM1",
                "MOCK_COM2",
                DEFAULT_OPTICAL_BAUDRATE,
            ]
            assert manager.is_connected is True
            assert manager.current_data_port == "MOCK_COM1"
            assert manager.current_ctrl_port == "MOCK_COM2"
            assert manager.current_control_baud == CONTROL_BAUDRATE

            with qtbot.waitSignal(manager.baud_command_sent, timeout=3000) as baud_cmd:
                manager.send_baud_command(9600)

            assert baud_cmd.args == [9600, "01"]
            assert manager.current_baud == 9600
            assert manager.current_control_baud == CONTROL_BAUDRATE

            header_payload = pack_header_payload("lifecycle.bin", 1)
            with qtbot.waitSignal(manager.packet_received, timeout=3000) as packet_signal:
                manager.send_packet(SEQ_HEADER, header_payload)

            packet = packet_signal.args[0]
            assert isinstance(packet, WinFormPacket)
            assert packet.is_header
            assert packet.data == header_payload

            with qtbot.waitSignal(manager.disconnected, timeout=3000):
                manager.disconnect_port()

            assert manager.is_connected is False
        finally:
            manager.shutdown()

    def test_reconnect_after_baud_change(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=3000):
                manager.connect_ports("MOCK_COM1", "MOCK_COM2", 19200)
            with qtbot.waitSignal(manager.baud_changed, timeout=3000):
                manager.send_baud_command(4800)

            with qtbot.waitSignal(manager.disconnected, timeout=3000):
                manager.disconnect_port()

            with qtbot.waitSignal(manager.ports_connected, timeout=3000) as reconnect:
                manager.connect_ports("MOCK_COM1", "MOCK_COM2", 4800)

            assert reconnect.args == ["MOCK_COM1", "MOCK_COM2", 4800]
            assert manager.current_baud == 4800
        finally:
            manager.shutdown()

    def test_data_only_connection_has_no_control_channel(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=3000) as connected:
                manager.connect_port("MOCK_COM1", 19200)

            assert connected.args == ["MOCK_COM1", "", 19200]
            assert manager.current_ctrl_port == ""

            with qtbot.waitSignal(manager.error_occurred, timeout=3000) as error:
                manager.send_baud_command(9600)

            assert "not open" in error.args[0]
        finally:
            manager.shutdown()
