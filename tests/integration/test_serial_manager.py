"""Integration test for non-blocking SerialManager and background QThread."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import PHYSICAL_BAUDRATE, Opcode
from ir_terminal.protocol.framer import pack_frame
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

            blob = b"".join(
                pack_frame(seq=i % 256, opcode=Opcode.PING, payload=b"X" * 7) for i in range(600)
            )
            worker._transport.inject_rx_data(blob)  # type: ignore[union-attr]
            qtbot.waitUntil(lambda: len(received) >= 600, timeout=10000)
            assert errors == []
            assert worker._rx_consumed < 4096
        finally:
            worker.close_port()


class TestSerialManagerControlChannel:
    """Verifies ASCII control RX/TX integration with the worker thread."""

    def test_control_line_emits_without_packet(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        packets: list[Packet] = []
        manager.packet_received.connect(packets.append)
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            with qtbot.waitSignal(manager.control_message_received, timeout=2000) as blocker:
                manager.inject_raw_rx(b"BAUD_OK:19200\n")

            assert blocker.args == ["BAUD_OK:19200"]
            assert packets == []
            assert manager.current_baud == PHYSICAL_BAUDRATE
        finally:
            manager.shutdown()

    def test_control_line_split_across_injections(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        messages: list[str] = []
        manager.control_message_received.connect(messages.append)
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            manager.inject_raw_rx(b"NOTIFY_BAUD:4")
            qtbot.wait(50)
            assert messages == []

            with qtbot.waitSignal(manager.control_message_received, timeout=2000):
                manager.inject_raw_rx(b"800\n")

            assert messages == ["NOTIFY_BAUD:4800"]
            assert manager.is_connected is True
        finally:
            manager.shutdown()

    def test_control_then_packet_order_is_preserved(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        events: list[str] = []
        manager.control_message_received.connect(lambda text: events.append("control:" + text))
        manager.packet_received.connect(lambda pkt: events.append("packet:" + str(pkt.seq)))
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            frame = pack_frame(seq=12, opcode=Opcode.DATA_TEXT, payload=b"hello")
            with qtbot.waitSignal(manager.packet_received, timeout=2000):
                manager.inject_raw_rx(b"BAUD_OK:9600\n" + frame)

            assert events == ["control:BAUD_OK:9600", "packet:12"]
        finally:
            manager.shutdown()

    def test_control_token_inside_frame_payload_is_not_emitted(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        messages: list[str] = []
        manager.control_message_received.connect(messages.append)
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            payload = b"BAUD_OK:19200\n"
            frame = pack_frame(seq=4, opcode=Opcode.DATA_TEXT, payload=payload)
            with qtbot.waitSignal(manager.packet_received, timeout=2000) as blocker:
                manager.inject_raw_rx(frame)

            packet = blocker.args[0]
            assert isinstance(packet, Packet)
            assert packet.payload == payload
            assert messages == []
        finally:
            manager.shutdown()

    def test_send_control_line_writes_exact_ascii(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            with qtbot.waitSignal(manager.raw_tx, timeout=2000) as blocker:
                manager.send_control_line("SET_BAUD:19200")

            assert blocker.args == [b"SET_BAUD:19200\n"]
        finally:
            manager.shutdown()

    def test_send_control_line_while_disconnected_emits_error(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.error_occurred, timeout=2000) as blocker:
                manager.send_control_line("SET_BAUD:19200")

            assert "not connected" in blocker.args[0].lower()
        finally:
            manager.shutdown()

    def test_notify_baud_does_not_disconnect_or_change_physical_baud(
        self, qtbot: QtBot
    ) -> None:
        manager = SerialManager()
        disconnects: list[object] = []
        manager.disconnected.connect(lambda: disconnects.append(True))
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000):
                manager.connect_port("MOCK_COM_LOOPBACK", PHYSICAL_BAUDRATE)

            with qtbot.waitSignal(manager.control_message_received, timeout=2000):
                manager.inject_raw_rx(b"NOTIFY_BAUD:4800\n")

            qtbot.wait(50)
            assert disconnects == []
            assert manager.current_baud == PHYSICAL_BAUDRATE
            assert manager.is_connected is True
        finally:
            manager.shutdown()
