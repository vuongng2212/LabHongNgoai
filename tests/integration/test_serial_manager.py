"""Integration test for non-blocking SerialManager and background QThread."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.packet import Packet


class TestSerialManagerIntegration:
    """Integration test suite verifying multi-threaded serial communication."""

    def test_connect_and_ping_pong_roundtrip(self, qtbot: QtBot) -> None:
        """Verifies connecting to Mock COM and receiving PONG response via Qt Signals."""
        manager = SerialManager()

        with qtbot.waitSignal(manager.connected, timeout=2000) as blocker:
            manager.connect_port("MOCK_COM1", 115200)

        assert blocker.args == ["MOCK_COM1", 115200]
        assert manager.is_connected

        # Send PING and wait for PONG packet via packet_received signal
        received_packets: list[Packet] = []

        def on_packet(pkt: object) -> None:
            if isinstance(pkt, Packet):
                received_packets.append(pkt)

        manager.packet_received.connect(on_packet)

        with qtbot.waitSignal(manager.packet_received, timeout=2000):
            manager.send_packet(seq=15, opcode=Opcode.PING, payload=b"ECHO_P2P")

        assert len(received_packets) >= 1
        pong_pkt = received_packets[0]
        assert pong_pkt.opcode == Opcode.PONG
        assert pong_pkt.seq == 15
        assert pong_pkt.payload == b"ECHO_P2P"

        # Disconnect
        with qtbot.waitSignal(manager.disconnected, timeout=2000):
            manager.disconnect_port()

        manager.shutdown()
