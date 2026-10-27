"""Unit tests for BackendBridge QWebChannel integration layer."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ir_terminal.config import LaunchConfig
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import EOF_MAGIC, SOF_MAGIC, Opcode
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


class TestBackendBridge:
    """Test suite verifying BackendBridge QWebChannel slots and signals."""

    def test_scan_ports_returns_json_list(self) -> None:
        """Verifies scanPorts slot returns a JSON list of detailed port objects."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        ports_json = bridge.scanPorts()
        ports = json.loads(ports_json)
        assert isinstance(ports, list)
        devices = [port["device"] for port in ports]
        assert "MOCK_COM_LOOPBACK" in devices

        manager.shutdown()

    def test_scan_ports_includes_detailed_metadata(self) -> None:
        """Verifies every scanned port exposes the detailed metadata contract."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        ports = json.loads(bridge.scanPorts())
        assert ports
        for port in ports:
            assert set(port) == {
                "device",
                "description",
                "manufacturer",
                "vid",
                "pid",
                "hwid",
                "label",
            }
            assert isinstance(port["device"], str)
            assert isinstance(port["label"], str)
            assert port["vid"] is None or len(port["vid"]) == 4
            assert port["pid"] is None or len(port["pid"]) == 4

        mock = next(port for port in ports if port["device"] == "MOCK_COM_LOOPBACK")
        assert mock["label"] == "MOCK_COM_LOOPBACK (Virtual Loopback)"
        assert mock["vid"] is None
        assert mock["pid"] is None

        manager.shutdown()

    def test_port_info_list_matches_string_port_list(self) -> None:
        """Verifies get_port_info_list stays consistent with get_port_list."""
        manager = SerialManager()

        infos = manager.get_port_info_list()
        devices = [info.device for info in infos]
        assert devices == manager.get_port_list()
        assert "MOCK_COM_LOOPBACK" in devices

        manager.shutdown()

    def test_connect_and_disconnect_port_signals(self, qtbot: QtBot) -> None:
        """Verifies connectPort and disconnectPort trigger bridge signals."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        with qtbot.waitSignal(bridge.portConnected, timeout=2000) as blocker:
            bridge.connectPort("MOCK_COM_LOOPBACK", 115200)

        assert blocker.args == [
            "MOCK_COM_LOOPBACK",
            "MOCK_COM_LOOPBACK",
            115200,
        ]

        with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
            bridge.disconnectPort()

        manager.shutdown()

    def test_connect_dual_ports_emits_tx_rx_baud(self, qtbot: QtBot) -> None:
        """Verifies connectDualPorts forwards both ports and the baudrate."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        try:
            with qtbot.waitSignal(bridge.portConnected, timeout=2000) as blocker:
                bridge.connectDualPorts("MOCK_NODE_A", "MOCK_NODE_B", 57600)

            assert blocker.args == ["MOCK_NODE_A", "MOCK_NODE_B", 57600]
            qtbot.waitUntil(lambda: manager.current_rx_port == "MOCK_NODE_B", timeout=1000)
            connected = json.loads(bridge.getConnectedPorts())
            assert connected == {
                "tx_port": "MOCK_NODE_A",
                "rx_port": "MOCK_NODE_B",
                "baudrate": 57600,
            }
        finally:
            manager.shutdown()

    def test_connect_dual_ports_rejects_invalid_input(self, qtbot: QtBot) -> None:
        """Verifies empty TX port and non-positive baudrate are rejected."""
        manager = SerialManager()
        bridge = BackendBridge(manager)

        try:
            with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as blocker:
                bridge.connectDualPorts("", "MOCK_COM_LOOPBACK", 115200)
            assert "empty TX port" in blocker.args[0]

            with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as blocker:
                bridge.connectDualPorts("MOCK_COM_LOOPBACK", "MOCK_COM_LOOPBACK", 0)
            assert "invalid baudrate" in blocker.args[0]
        finally:
            manager.shutdown()

    def test_get_available_baudrates_defaults(self, tmp_path: Path) -> None:
        """Verifies the default supported baudrate list is exposed as JSON."""
        manager = SerialManager()
        bridge = BackendBridge(
            manager,
            storage_service=StorageService(data_dir=tmp_path / "data"),
        )

        baudrates = json.loads(bridge.getAvailableBaudrates())
        assert baudrates == [9600, 38400, 57600, 115200]

        manager.shutdown()

    def test_get_available_baudrates_honors_settings(self, tmp_path: Path) -> None:
        """Verifies a custom JSON list in settings overrides the defaults."""
        manager = SerialManager()
        storage = StorageService(data_dir=tmp_path / "data")
        storage.save_setting("supported_baudrates", json.dumps([9600, 14400, 28800]))
        bridge = BackendBridge(manager, storage_service=storage)

        assert json.loads(bridge.getAvailableBaudrates()) == [9600, 14400, 28800]
        launch = json.loads(bridge.getLaunchConfig())
        assert launch["supported_baudrates"] == [9600, 14400, 28800]

        manager.shutdown()

    def test_launch_config_includes_dual_port_fields(self, tmp_path: Path) -> None:
        """Verifies getLaunchConfig exposes the dual-port hints and defaults."""
        manager = SerialManager()
        bridge = BackendBridge(
            manager,
            storage_service=StorageService(data_dir=tmp_path / "data"),
            launch_config=LaunchConfig(tx_port="COM3", rx_port="COM4", baud=57600),
        )

        launch = json.loads(bridge.getLaunchConfig())
        assert launch["tx_port"] == "COM3"
        assert launch["rx_port"] == "COM4"
        assert launch["default_baudrate"] == 57600
        assert launch["baud"] == 57600
        assert launch["supported_baudrates"][-1] == 115200

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

    def test_scan_ports_includes_dual_virtual_links(self) -> None:
        manager = SerialManager(include_link=True)
        bridge = BackendBridge(manager)

        ports_json = bridge.scanPorts()
        ports = json.loads(ports_json)
        devices = [port["device"] for port in ports]

        assert "LINK_A" in devices
        assert "LINK_B" in devices
        assert "LINK_A_TX" in devices
        assert "LINK_A_RX" in devices
        assert "LINK_B_TX" in devices
        assert "LINK_B_RX" in devices

        manager.shutdown()

    def test_scan_ports_filters_dummy_linux_serial_base_ports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from serial.tools import list_ports_common

        dummy_port = list_ports_common.ListPortInfo("/dev/ttyS0")
        dummy_port.description = "n/a"
        dummy_port.hwid = "n/a"
        subsystem_attr = "subsystem"
        setattr(dummy_port, subsystem_attr, "serial-base")

        real_port = list_ports_common.ListPortInfo("/dev/ttyUSB0")
        real_port.description = "FTDI USB Serial"
        real_port.hwid = "USB VID:PID=0403:6001"
        real_port.vid = 0x0403
        real_port.pid = 0x6001

        from serial.tools import list_ports

        monkeypatch.setattr(list_ports, "comports", lambda: [dummy_port, real_port])

        manager = SerialManager(include_link=False)
        bridge = BackendBridge(manager)

        ports = json.loads(bridge.scanPorts())
        devices = [port["device"] for port in ports]

        assert "/dev/ttyS0" not in devices
        assert "/dev/ttyUSB0" in devices

        manager.shutdown()
