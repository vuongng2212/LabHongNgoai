"""Unit tests for transfer settings: storage keys, service config, bridge slots."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import MAX_FILE_SIZE_BYTES
from ir_terminal.services.file_transfer_service import (
    MAX_CONFIGURABLE_FILE_SIZE_BYTES,
    MIN_FILE_SIZE_BYTES,
    FileTransferService,
)
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


class TestTransferSettingsStorage:
    """Test suite verifying settings persistence primitives."""

    def test_save_and_get_setting_roundtrip(self, tmp_path: Path) -> None:
        """Verifies a saved setting value reads back identically."""
        storage = StorageService(data_dir=tmp_path / "data")
        storage.save_setting("max_file_size_bytes", "10485760")
        assert storage.get_setting("max_file_size_bytes") == "10485760"

    def test_get_setting_returns_default_when_missing(self, tmp_path: Path) -> None:
        """Verifies unknown keys fall back to the provided default."""
        storage = StorageService(data_dir=tmp_path / "data")
        assert storage.get_setting("no_such_key", "fallback") == "fallback"
        assert storage.get_setting("no_such_key") == ""


class TestFileTransferServiceConfig:
    """Test suite verifying dynamic max-size and receive-dir configuration."""

    def test_defaults_match_protocol_constants(self, tmp_path: Path) -> None:
        """Verifies fresh service uses the MVP default limit and dir."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            default_dir = tmp_path / "rx_default"
            service = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=default_dir,
            )
            assert service.max_file_size_bytes == MAX_FILE_SIZE_BYTES
            assert service.transfers_dir_path == default_dir
        finally:
            manager.shutdown()

    def test_set_max_file_size_persists_and_clamps(self, tmp_path: Path) -> None:
        """Verifies limit update persists and clamps to 1MB..100MB."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            service = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx",
            )
            assert service.set_max_file_size_bytes(5 * 1024 * 1024) == 5 * 1024 * 1024
            assert storage.get_setting("max_file_size_bytes") == str(5 * 1024 * 1024)
            assert service.set_max_file_size_bytes(1000) == MIN_FILE_SIZE_BYTES
            assert (
                service.set_max_file_size_bytes(10 * 1024 * 1024 * 1024)
                == MAX_CONFIGURABLE_FILE_SIZE_BYTES
            )
        finally:
            manager.shutdown()

    def test_set_max_file_size_rejects_non_positive(self, tmp_path: Path) -> None:
        """Verifies zero or negative limits raise ValueError."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            service = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx",
            )
            try:
                service.set_max_file_size_bytes(0)
                raise AssertionError("expected ValueError")
            except ValueError:
                pass
        finally:
            manager.shutdown()

    def test_set_transfers_dir_persists_and_creates(self, tmp_path: Path) -> None:
        """Verifies receive-dir update creates the folder and persists it."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            service = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx",
            )
            target = tmp_path / "custom" / "incoming"
            assert service.set_transfers_dir(str(target)) == target
            assert target.is_dir()
            assert storage.get_setting("transfers_dir") == str(target)
        finally:
            manager.shutdown()

    def test_load_settings_restores_persisted_config(self, tmp_path: Path) -> None:
        """Verifies a new service instance picks up previously saved settings."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            first = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx1",
            )
            custom_dir = tmp_path / "restored"
            first.set_max_file_size_bytes(7 * 1024 * 1024)
            first.set_transfers_dir(custom_dir)

            second = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx2",
            )
            assert second.max_file_size_bytes == 7 * 1024 * 1024
            assert second.transfers_dir_path == custom_dir
        finally:
            manager.shutdown()

    def test_custom_limit_enforced_on_start(self, qtbot: QtBot, tmp_path: Path) -> None:
        """Verifies TX rejects files above the configured (lowered) limit."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            service = FileTransferService(
                serial_manager=manager,
                storage_service=storage,
                transfers_dir=tmp_path / "rx",
            )
            service.set_max_file_size_bytes(MIN_FILE_SIZE_BYTES)
            payload = b"\0" * (MIN_FILE_SIZE_BYTES + 16)
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
                tmp.write(payload)
                tmp_path_str = tmp.name
            try:
                with qtbot.waitSignal(service.transfer_failed, timeout=1000):
                    service.start_transfer(tmp_path_str)
            finally:
                Path(tmp_path_str).unlink(missing_ok=True)
        finally:
            manager.shutdown()


class TestBackendBridgeSettings:
    """Test suite verifying settings slots exposed to the Web UI."""

    def test_get_settings_returns_current_config(self, tmp_path: Path) -> None:
        """Verifies getSettings JSON carries size bytes, MB, and dir."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            bridge = BackendBridge(manager, storage_service=storage)
            config = json.loads(bridge.getSettings())
            assert config["max_file_size_bytes"] == MAX_FILE_SIZE_BYTES
            assert config["max_file_size_mb"] == 10.0
            assert isinstance(config["transfers_dir"], str)
        finally:
            manager.shutdown()

    def test_save_settings_updates_service_and_emits(self, qtbot: QtBot, tmp_path: Path) -> None:
        """Verifies saveSettings applies MB limit plus dir and emits signal."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            bridge = BackendBridge(manager, storage_service=storage)
            custom_dir = str(tmp_path / "bridge_rx")
            with qtbot.waitSignal(bridge.settingsChanged, timeout=1000):
                raw = bridge.saveSettings(
                    json.dumps({"max_file_size_mb": 5, "transfers_dir": custom_dir})
                )
            result = json.loads(raw)
            assert result["ok"] is True
            assert result["max_file_size_bytes"] == 5 * 1024 * 1024
            assert result["transfers_dir"] == custom_dir
            assert bridge.file_service.max_file_size_bytes == 5 * 1024 * 1024
        finally:
            manager.shutdown()

    def test_save_settings_rejects_invalid_input(self, tmp_path: Path) -> None:
        """Verifies malformed JSON and empty objects fail gracefully."""
        manager = SerialManager()
        try:
            storage = StorageService(data_dir=tmp_path / "data")
            bridge = BackendBridge(manager, storage_service=storage)
            assert json.loads(bridge.saveSettings("not-json"))["ok"] is False
            assert json.loads(bridge.saveSettings("{}"))["ok"] is False
            assert json.loads(bridge.saveSettings('{"max_file_size_mb": 0}'))["ok"] is False
            # Failed saves must not clobber the running config.
            assert bridge.file_service.max_file_size_bytes == MAX_FILE_SIZE_BYTES
        finally:
            manager.shutdown()
