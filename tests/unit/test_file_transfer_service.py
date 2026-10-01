"""Unit and Integration tests for Stop-and-Wait ARQ Optical File Transfer Service."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
)


class TestFileTransferService:
    """Test suite for optical file chunking and Stop-and-Wait ARQ engine."""

    def test_file_transfer_e2e_mock_loopback(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

        # Create temporary sample file
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            test_content = b"SpectraLink IR-OWC Lab Terminal Optical Transmission Payload\n" * 10
            tmp.write(test_content)
            tmp_path = tmp.name

        service = FileTransferService(serial_manager=manager, chunk_size=64)

        try:
            with qtbot.waitSignal(service.transfer_completed, timeout=5000) as blocker:
                service.start_transfer(tmp_path)

            filename, bytes_sent, elapsed_sec, crc_hex = blocker.args
            assert filename == Path(tmp_path).name
            assert bytes_sent == len(test_content)
            assert elapsed_sec >= 0.0
            assert crc_hex.startswith("0x")
            assert service.state == TransferState.COMPLETED
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_non_existent_file_fails(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        service = FileTransferService(manager)

        with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
            service.start_transfer("/tmp/non_existent_file_xyz_123.bin")

        (reason,) = blocker.args
        assert "not found" in reason.lower()
        manager.shutdown()
