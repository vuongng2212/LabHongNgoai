"""Integration tests for the non-blocking SerialManager worker thread."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import SEQ_END
from ir_terminal.protocol.packet import WinFormPacket


class TestSerialManagerIntegration:
    """Verifies COM1/COM2 lifecycle and frame round-trips across the worker thread."""

    def test_connect_send_and_receive_loopback(self, qtbot: QtBot) -> None:
        manager = SerialManager()

        with qtbot.waitSignal(manager.ports_connected, timeout=3000) as blocker:
            manager.connect_ports("MOCK_COM1", "MOCK_COM2", 19200)

        assert blocker.args == ["MOCK_COM1", "MOCK_COM2", 19200]
        assert manager.is_connected
        assert manager.current_data_port == "MOCK_COM1"
        assert manager.current_ctrl_port == "MOCK_COM2"

        with qtbot.waitSignal(manager.packet_received, timeout=3000) as packet_blocker:
            manager.send_packet(11, b"loopback")

        packet = packet_blocker.args[0]
        assert isinstance(packet, WinFormPacket)
        assert packet.sequence == 11
        assert packet.data == b"loopback"

        manager.disconnect_port()
        manager.shutdown()

    def test_baud_switch_uses_control_channel(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        with qtbot.waitSignal(manager.ports_connected, timeout=3000):
            manager.connect_ports("MOCK_COM1", "MOCK_COM2", 19200)

        with qtbot.waitSignal(manager.baud_command_sent, timeout=3000) as blocker:
            manager.send_baud_command(9600)

        assert blocker.args == [9600, "01"]
        assert manager.current_baud == 9600

        manager.shutdown()

    def test_inject_raw_bytes_become_packets(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        with qtbot.waitSignal(manager.ports_connected, timeout=3000):
            manager.connect_ports("MOCK_COM1", "MOCK_COM2", 19200)

        frame = b"\xAA\x55" + SEQ_END.to_bytes(4, "little") + b"\x00\x00"
        with qtbot.waitSignal(manager.packet_received, timeout=2000) as blocker:
            manager.inject_raw_rx(frame)

        packet = blocker.args[0]
        assert isinstance(packet, WinFormPacket)
        assert packet.is_end

        manager.shutdown()
