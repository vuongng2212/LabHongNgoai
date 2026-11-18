"""Unit tests for the QWebChannel BackendBridge slot contract."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import CONTROL_BAUDRATE
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


def make_bridge(tmp_path: Path) -> tuple[BackendBridge, SerialManager, FileTransferService]:
    """Builds a bridge wired to real worker threads and a temporary store."""
    storage = StorageService(data_dir=tmp_path / "data")
    manager = SerialManager()
    file_service = FileTransferService(
        manager,
        storage_service=storage,
        transfers_dir=tmp_path / "transfers",
    )
    bridge = BackendBridge(manager, file_service=file_service, storage_service=storage)
    return bridge, manager, file_service


class TestBridgeQueries:
    """Verifies JSON-returning slots used during UI bootstrap."""

    def test_scan_ports_returns_virtual_ports(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)
        ports = json.loads(bridge.scanPorts())

        assert isinstance(ports, list)
        assert any(port["device"] == "MOCK_COM1" for port in ports)
        manager.shutdown()

    def test_launch_config_and_baudrates(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)
        config = json.loads(bridge.getLaunchConfig())
        baudrates = json.loads(bridge.getAvailableBaudrates())

        assert config["control_baudrate"] == CONTROL_BAUDRATE
        assert config["supported_baudrates"] == baudrates
        assert baudrates == [2400, 4800, 9600, 19200]
        manager.shutdown()

    def test_history_and_telemetry_slots(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        assert isinstance(json.loads(bridge.getFileHistory()), list)
        assert isinstance(json.loads(bridge.getChatHistory()), list)
        assert isinstance(json.loads(bridge.getSnifferRecords()), list)
        stats = json.loads(bridge.getTelemetryStats())
        assert "throughput_kbps" in stats
        manager.shutdown()


class TestBridgeConnection:
    """Verifies COM1/COM2 connect, baud commands and disconnect."""

    def test_connect_hardware_ports_and_query_state(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        with qtbot.waitSignal(bridge.portConnected, timeout=3000) as blocker:
            bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 19200)

        com1, com2, baud = blocker.args
        assert (com1, com2, baud) == ("MOCK_COM1", "MOCK_COM2", 19200)

        state = json.loads(bridge.getConnectedPorts())
        assert state["com1_port"] == "MOCK_COM1"
        assert state["com2_port"] == "MOCK_COM2"
        assert state["control_baudrate"] == CONTROL_BAUDRATE

        bridge.disconnectPort()
        manager.shutdown()

    def test_connect_rejects_unsupported_baud(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as blocker:
            bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 1234)

        assert "unsupported optical baudrate" in blocker.args[0]
        manager.shutdown()

    def test_send_baud_command_uses_com2(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)
        with qtbot.waitSignal(bridge.portConnected, timeout=3000):
            bridge.connectHardwarePorts("MOCK_COM1", "MOCK_COM2", 19200)

        result = json.loads(bridge.sendBaudSelection(9600))
        assert result["ok"] is True

        with qtbot.waitSignal(bridge.opticalBaudChanged, timeout=3000) as blocker:
            pass

        assert blocker.args[0] == 9600
        manager.shutdown()

    def test_send_baud_command_while_disconnected_only_stores(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        result = json.loads(bridge.sendBaudSelection(4800))

        assert result == {"ok": True, "connected": False, "baudrate": 4800}
        manager.shutdown()

    def test_invalid_baud_command_is_reported(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        result = json.loads(bridge.sendBaudSelection(1234))

        assert result["ok"] is False
        manager.shutdown()


class TestBridgeSettings:
    """Verifies receive-folder and AutoSave slots."""

    def test_choose_destination_folder_persists(
        self, qtbot: QtBot, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        bridge, manager, file_service = make_bridge(tmp_path)
        chosen = tmp_path / "chosen"
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            staticmethod(lambda *args, **kwargs: str(chosen)),
        )

        with qtbot.waitSignal(bridge.settingsChanged, timeout=1000):
            result = bridge.chooseDestinationFolder()

        assert result == str(chosen.resolve())
        assert file_service.transfers_dir_path == chosen.resolve()
        manager.shutdown()

    def test_set_auto_save_option(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, file_service = make_bridge(tmp_path)

        with qtbot.waitSignal(bridge.settingsChanged, timeout=1000):
            payload = bridge.setAutoSaveOption(False)

        assert json.loads(payload)["auto_save"] is False
        assert file_service.auto_save is False
        manager.shutdown()

    def test_save_and_get_settings(self, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)
        target = tmp_path / "saved"

        result = json.loads(
            bridge.saveSettings(
                json.dumps({"max_file_size_bytes": 4096, "transfers_dir": str(target)})
            )
        )

        assert result["max_file_size_bytes"] == 4096
        assert json.loads(bridge.getSettings())["transfers_dir"] == str(target.resolve())
        manager.shutdown()

    def test_browse_transfers_dir_persists(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        bridge, manager, file_service = make_bridge(tmp_path)
        chosen = tmp_path / "browsed"
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            staticmethod(lambda *args, **kwargs: str(chosen)),
        )

        result = bridge.browseTransfersDir()

        assert result == str(chosen.resolve())
        assert file_service.transfers_dir_path == chosen.resolve()
        manager.shutdown()


class TestBridgeLegacyActions:
    """Verifies legacy actions are rejected without breaking the UI contract."""

    def test_chat_and_ping_are_rejected(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as chat_error:
            bridge.sendChat("hello")
        assert "Chat is disabled" in chat_error.args[0]

        with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as ping_error:
            bridge.sendPing()
        assert "not supported" in ping_error.args[0]
        manager.shutdown()

    def test_start_file_transfer_validates_path(self, qtbot: QtBot, tmp_path: Path) -> None:
        bridge, manager, _ = make_bridge(tmp_path)

        with qtbot.waitSignal(bridge.errorOccurred, timeout=1000) as blocker:
            bridge.startFileTransfer(str(tmp_path / "missing.bin"))

        assert "not found" in blocker.args[0]
        manager.shutdown()
