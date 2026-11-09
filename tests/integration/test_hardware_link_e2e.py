"""End-to-End integration test suite for Hardware & Link Setup screen."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import DEFAULT_OPTICAL_BAUDRATE, PHYSICAL_BAUDRATE
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


class TestHardwareLinkE2E:
    """Verifies complete lifecycle of Hardware & Link screen."""

    def test_connect_disconnect_stress_cycle(self, qtbot: QtBot) -> None:
        """Connects and disconnects repeatedly to ensure zero handle leakage or lockups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir))
            manager = SerialManager()
            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            try:
                for _ in range(5):
                    with qtbot.waitSignal(bridge.portConnected, timeout=2000) as conn_sig:
                        bridge.connectDualPorts("MOCK_TX_CYCLE", "MOCK_RX_CYCLE", DEFAULT_OPTICAL_BAUDRATE)

                    tx_port, rx_port, baud = conn_sig.args
                    assert tx_port == "MOCK_TX_CYCLE"
                    assert rx_port == "MOCK_RX_CYCLE"
                    assert baud == DEFAULT_OPTICAL_BAUDRATE
                    assert manager.is_connected is True

                    with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
                        bridge.disconnectPort()

                    assert manager.is_connected is False
            finally:
                manager.shutdown()

    def test_in_band_optical_baudrate_switching_online(self, qtbot: QtBot) -> None:
        """Switches through all supported optical baudrates in-band without closing physical link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir))
            manager = SerialManager()
            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            try:
                with qtbot.waitSignal(bridge.portConnected, timeout=2000):
                    bridge.connectDualPorts("MOCK_COM_BAUD", "MOCK_COM_BAUD", 9600)

                for target_baud in (2400, 4800, 19200, 9600):
                    with qtbot.waitSignal(bridge.opticalBaudChanged, timeout=2000) as baud_sig:
                        result_json = bridge.setOpticalBaud(target_baud)
                        result = json.loads(result_json)
                        assert result.get("ok") is True

                    confirmed_baud = baud_sig.args[0]
                    assert confirmed_baud == target_baud
                    assert manager.is_connected is True
                    assert manager.current_baud == PHYSICAL_BAUDRATE

                with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
                    bridge.disconnectPort()
            finally:
                manager.shutdown()

    def test_offline_baudrate_selection_persists_on_next_connect(self, qtbot: QtBot) -> None:
        """Selects optical rate while disconnected and verifies automatic sync upon link-up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir))
            manager = SerialManager()
            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            try:
                result_json = bridge.setOpticalBaud(4800)
                result = json.loads(result_json)
                assert result.get("ok") is True
                assert result.get("connected") is False
                assert result.get("baudrate") == 4800

                with qtbot.waitSignal(bridge.portConnected, timeout=2000) as conn_sig:
                    bridge.connectDualPorts("MOCK_OFFLINE_SYNC", "MOCK_OFFLINE_SYNC", 4800)

                _, _, active_baud = conn_sig.args
                assert active_baud == 4800

                with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
                    bridge.disconnectPort()
            finally:
                manager.shutdown()

    def test_ping_pong_rtt_and_sequence(self, qtbot: QtBot) -> None:
        """Sends multiple ping frames and verifies RTT measurements and sequential numbers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir))
            manager = SerialManager()
            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            try:
                with qtbot.waitSignal(bridge.portConnected, timeout=2000):
                    bridge.connectDualPorts("MOCK_PING_TEST", "MOCK_PING_TEST", 9600)

                last_seq = 0
                for _ in range(3):
                    with qtbot.waitSignal(bridge.pingResult, timeout=2000) as ping_sig:
                        bridge.sendPing()

                    rtt_ms, seq = ping_sig.args
                    assert rtt_ms >= 0.0
                    assert seq > last_seq
                    last_seq = seq

                with qtbot.waitSignal(bridge.portDisconnected, timeout=2000):
                    bridge.disconnectPort()
            finally:
                manager.shutdown()

    def test_connect_rejection_on_invalid_parameters(self, qtbot: QtBot) -> None:
        """Ensures invalid inputs are rejected and emit descriptive error signals."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = StorageService(data_dir=Path(tmpdir))
            manager = SerialManager()
            bridge = BackendBridge(serial_manager=manager, storage_service=storage)

            try:
                with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as err_sig:
                    bridge.connectDualPorts("", "", 9600)
                assert "empty TX port" in err_sig.args[0]

                with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as err_sig:
                    bridge.connectDualPorts("COM1", "COM1", 38400)
                assert "unsupported optical baudrate" in err_sig.args[0]

                assert manager.is_connected is False
            finally:
                manager.shutdown()
