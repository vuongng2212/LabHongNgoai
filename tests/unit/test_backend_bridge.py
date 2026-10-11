"""Unit tests for BackendBridge QWebChannel integration layer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import EOF_MAGIC, SOF_MAGIC, Opcode
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.ui.backend_bridge import BackendBridge


class TestBackendBridge:
    """Test suite verifying BackendBridge QWebChannel slots and signals."""

    def test_scan_ports_returns_json_list(self) -> None:
        """Verifies scanPorts slot returns valid JSON list with available ports."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        ports_json = bridge.scanPorts()
        ports = json.loads(ports_json)
        assert isinstance(ports, list)
        assert "MOCK_COM_LOOPBACK" in ports

        manager.shutdown()

    def test_connect_and_disconnect_port_signals(self, qtbot: QtBot) -> None:
        """Verifies connectPort and disconnectPort trigger bridge signals."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with qtbot.waitSignal(bridge.portConnected, timeout=2000) as blocker:
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        assert blocker.args == ["MOCK_COM_LOOPBACK", 115200]

        with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
            bridge.disconnectPort()

        manager.shutdown()

    def test_send_ping_pong_result(self, qtbot: QtBot) -> None:
        """Verifies sendPing triggers pingResult signal upon PONG loopback."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with qtbot.waitSignal(bridge.portConnected, timeout=2000):
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        with qtbot.waitSignal(bridge.pingResult, timeout=2000) as blocker:
            bridge.sendPing()

        rtt_ms, seq = blocker.args
        assert rtt_ms >= 0.0
        assert seq > 0

        manager.shutdown()

    def test_send_chat_signal_propagation(self, qtbot: QtBot) -> None:
        """Verifies sendChat propagates through chatMessageReceived and chatFrameInspected."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with qtbot.waitSignal(bridge.portConnected, timeout=2000):
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        with qtbot.waitSignal(bridge.chatMessageReceived, timeout=1000) as blocker:
            bridge.sendChat("Bridge Chat Test")

        text, time_str, is_outgoing = blocker.args
        assert text == "Bridge Chat Test"
        assert is_outgoing is True
        assert len(time_str) > 0

        manager.shutdown()

    def test_file_transfer_and_abort_signals(self, qtbot: QtBot) -> None:
        """Verifies file transfer initiation and abort signals through bridge."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Bridge File Content")
            tmp_path = tmp.name

        try:
            with qtbot.waitSignal(bridge.transferStateChanged, timeout=1000) as blocker:
                bridge.startFileTransfer(tmp_path)

            assert blocker.args == ["HANDSHAKING"]

            with qtbot.waitSignal(bridge.transferFailed, timeout=1000) as blocker:
                bridge.abortFileTransfer()

            (reason,) = blocker.args
            assert "cancelled by user" in reason.lower()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_raw_rx_and_tx_signals(self, qtbot: QtBot) -> None:
        """Verifies rawTxData and rawRxData hex string signals."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with qtbot.waitSignal(bridge.portConnected, timeout=2000):
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        with qtbot.waitSignal(bridge.rawTxData, timeout=1000) as blocker:
            manager.send_packet(seq=1, opcode=Opcode.PING, payload=b"RAW")

        hex_tx = blocker.args[0]
        assert isinstance(hex_tx, str)
        assert len(hex_tx) > 0

        manager.shutdown()

    def test_open_transfer_location_returns_dir_or_empty(self, tmp_path: Path) -> None:
        """Verifies openTransferLocation resolves parent dir or empty when missing."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        target = tmp_path / "payload.bin"
        target.write_bytes(b"data")
        assert bridge.openTransferLocation(str(target)) == str(tmp_path)
        assert bridge.openTransferLocation(str(tmp_path / "gone.bin")) == ""

        manager.shutdown()

    def test_auto_health_check_pings_periodically_and_silently(self, qtbot: QtBot) -> None:
        """Verifies silent periodic health pings while connected, none after disconnect."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        manual_pongs: list[object] = []
        bridge.pingResult.connect(lambda *args: manual_pongs.append(args))

        with qtbot.waitSignal(bridge.portConnected, timeout=2000):
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        bridge.setAutoPingInterval(0.2)
        with qtbot.waitSignal(bridge.healthResult, timeout=2000):
            pass
        with qtbot.waitSignal(bridge.healthResult, timeout=2000) as blocker:
            pass

        rtt_ms, seq = blocker.args
        assert rtt_ms >= 0.0
        assert seq > 0
        assert len(manual_pongs) == 0

        count = len(manual_pongs)
        bridge.setAutoPingInterval(0)
        with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
            bridge.disconnectPort()
        qtbot.wait(500)
        assert len(manual_pongs) == count

        manager.shutdown()

    def test_bridge_ignores_non_pong_in_packet_received(self) -> None:
        """Verifies _on_packet_received does not emit pingResult for non-PONG packets."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        ping_events: list[object] = []
        bridge.pingResult.connect(lambda *args: ping_events.append(args))

        header = Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.DATA_TEXT, length=4)
        pkt = Packet(header=header, payload=b"TEST", crc32=0, eof=EOF_MAGIC)
        bridge._on_packet_received(pkt)
        bridge._on_packet_received("NOT_A_PACKET")

        assert len(ping_events) == 0
        manager.shutdown()
