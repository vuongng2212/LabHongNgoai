"""End-to-end tests for the hardware and link setup surface."""

from __future__ import annotations

import json
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import CONTROL_BAUDRATE, DEFAULT_OPTICAL_BAUDRATE
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


def make_stack(tmp_path: Path) -> tuple[BackendBridge, SerialManager]:
    """Builds a bridge and manager backed by a temporary store."""
    storage = StorageService(data_dir=tmp_path / "data")
    manager = SerialManager()
    bridge = BackendBridge(serial_manager=manager, storage_service=storage)
    return bridge, manager


class TestHardwareLinkE2E:
    """Verifies the COM1/COM2 lifecycle exposed to the hardware screen."""

    def test_connect_disconnect_stress_cycle(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager = make_stack(tmp_path)
        try:
            for _ in range(5):
                with qtbot.waitSignal(bridge.portConnected, timeout=3000) as conn_sig:
                    bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", DEFAULT_OPTICAL_BAUDRATE)

                com1, com2, baud = conn_sig.args
                assert (com1, com2, baud) == (
                    "MOCK_COM1",
                    "MOCK_COM2",
                    DEFAULT_OPTICAL_BAUDRATE,
                )
                assert manager.is_connected is True

                with qtbot.waitSignal(bridge.portDisconnected, timeout=3000):
                    bridge.disconnectPort()

                assert manager.is_connected is False
        finally:
            manager.shutdown()

    def test_in_band_baud_switching(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager = make_stack(tmp_path)
        try:
            with qtbot.waitSignal(bridge.portConnected, timeout=3000):
                bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 9600)

            for target in (2400, 4800, 19200, 9600):
                with qtbot.waitSignal(bridge.opticalBaudChanged, timeout=3000) as baud_sig:
                    result = json.loads(bridge.sendBaudSelection(target))
                    assert result["ok"] is True

                assert baud_sig.args[0] == target
                assert manager.is_connected is True
                assert manager.current_baud == target
                assert manager.current_control_baud == CONTROL_BAUDRATE
        finally:
            manager.shutdown()

    def test_offline_baud_selection_applies_on_connect(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        bridge, manager = make_stack(tmp_path)
        try:
            result = json.loads(bridge.sendBaudSelection(4800))
            assert result == {"ok": True, "connected": False, "baudrate": 4800}

            with qtbot.waitSignal(bridge.portConnected, timeout=3000) as conn_sig:
                bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 4800)

            assert conn_sig.args[2] == 4800
        finally:
            manager.shutdown()

    def test_packet_round_trip_over_mock_data_port(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        bridge, manager = make_stack(tmp_path)
        try:
            with qtbot.waitSignal(bridge.portConnected, timeout=3000):
                bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 9600)

            with qtbot.waitSignal(manager.packet_received, timeout=3000) as packet_sig:
                manager.send_packet(0, b"payload")

            assert packet_sig.args[0].data == b"payload"
        finally:
            manager.shutdown()

    def test_invalid_connect_parameters_are_rejected(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        bridge, manager = make_stack(tmp_path)
        try:
            with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as empty_sig:
                bridge.connectHardwarePorts("", "", 9600)
            assert "empty COM1 port name" in empty_sig.args[0]

            with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as baud_sig:
                bridge.connectHardwarePorts("COM1", "COM2", 38400)
            assert "unsupported optical baudrate" in baud_sig.args[0]

            assert manager.is_connected is False
        finally:
            manager.shutdown()
