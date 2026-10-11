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

    def test_worker_compaction_survives_large_stream(self, qtbot: QtBot) -> None:
        """Verifies poll+compact path handles >4096 consumed bytes without BufferError.

        Regression: resizing the bytearray accumulation while a memoryview
        export is alive raises BufferError, kills polling, and stalls ARQ.
        """
        from ir_terminal.core.serial_worker import SerialWorker

        worker = SerialWorker()
        worker.open_port("MOCK_COM_LOOPBACK", 115200)
        try:
            received: list[Packet] = []
            worker.packet_received.connect(received.append)
            errors: list[str] = []
            worker.error_occurred.connect(errors.append)

            from ir_terminal.protocol.framer import pack_frame

            # >600 frames x ~19B forces multiple 4096-byte compactions
            blob = b"".join(
                pack_frame(seq=i % 256, opcode=Opcode.PING, payload=b"X" * 7)
                for i in range(600)
            )
            worker._transport.inject_rx_data(blob)  # type: ignore[union-attr]
            qtbot.waitUntil(lambda: len(received) >= 600, timeout=10000)
            assert errors == []
            assert worker._rx_consumed < 4096
        finally:
            worker.close_port()
