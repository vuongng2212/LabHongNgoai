"""End-to-end integration test simulating two independent physical workstations (Node A and Node B).

Validates bi-directional communication, true Stop-and-Wait ARQ exchange without mock loopback shortcuts,
and file reception writing to disk with whole-file CRC-32 integrity verification.
"""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService


class TestTwoNodesE2EFileTransfer:
    """Verifies complete E2E transmission between two distinct application instances."""

    def test_two_nodes_file_transfer_and_disk_persistence(self, qtbot: QtBot) -> None:
        """Node A transmits a multi-chunk file to Node B. Node B receives, verifies, and writes to disk."""
        mgr_a = SerialManager()
        mgr_a.connect_port("MOCK_NODE_A", 115200)

        mgr_b = SerialManager()
        mgr_b.connect_port("MOCK_NODE_B", 115200)

        mgr_a.raw_tx.connect(mgr_b.inject_raw_rx)
        mgr_b.raw_tx.connect(mgr_a.inject_raw_rx)

        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            source_file = dir_path / "firmware_update_v1.bin"
            test_content = (
                b"OPTICAL_PACKET_PAYLOAD_CHUNK_DATA_TEST_" * 80
            )  # 3120 bytes (~13 chunks @ 240B)
            source_file.write_bytes(test_content)
            expected_crc = zlib.crc32(test_content) & 0xFFFFFFFF

            downloads_b = dir_path / "node_b_downloads"
            data_b = dir_path / "node_b_data"
            storage_b = StorageService(data_dir=data_b)

            service_a = FileTransferService(serial_manager=mgr_a, chunk_size=240)
            service_b = FileTransferService(
                serial_manager=mgr_b,
                chunk_size=240,
                storage_service=storage_b,
                transfers_dir=downloads_b,
            )

            with (
                qtbot.waitSignal(service_a.transfer_completed, timeout=10000) as blocker_a,
                qtbot.waitSignal(service_b.transfer_completed, timeout=10000) as blocker_b,
            ):
                service_a.start_transfer(str(source_file))

            fname_a, bytes_a, elapsed_a, crc_a = blocker_a.args
            assert fname_a == source_file.name
            assert bytes_a == len(test_content)
            assert crc_a == f"0x{expected_crc:08X}"
            assert service_a.state == TransferState.COMPLETED

            fname_b, bytes_b, elapsed_b, crc_b = blocker_b.args
            assert fname_b == source_file.name
            assert bytes_b == len(test_content)
            assert crc_b == f"0x{expected_crc:08X}"
            assert service_b.state == TransferState.COMPLETED

            received_file = downloads_b / source_file.name
            assert received_file.exists()
            assert received_file.read_bytes() == test_content

            records = storage_b.get_file_transfers()
            assert len(records) >= 1
            rx_record = records[0]
            assert rx_record["filename"] == source_file.name
            assert rx_record["direction"] == "RX"
            assert rx_record["size_bytes"] == len(test_content)
            assert rx_record["status"] == "SUCCESS"
            assert rx_record["crc32_hex"] == f"0x{expected_crc:08X}"

        mgr_a.shutdown()
        mgr_b.shutdown()

    def test_two_nodes_receiver_cancel_mid_transfer(self, qtbot: QtBot) -> None:
        """Node B cancels mid-transfer; both nodes tear down and no partial file remains."""
        mgr_a = SerialManager()
        mgr_a.connect_port("MOCK_NODE_A", 115200)
        mgr_b = SerialManager()
        mgr_b.connect_port("MOCK_NODE_B", 115200)
        mgr_a.raw_tx.connect(mgr_b.inject_raw_rx)
        mgr_b.raw_tx.connect(mgr_a.inject_raw_rx)

        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            source_file = dir_path / "cancel_me.bin"
            source_file.write_bytes(b"CANCEL_MID_TRANSFER_BLOCK_" * 200)  # ~5200B

            downloads_b = dir_path / "node_b_downloads"
            service_a = FileTransferService(serial_manager=mgr_a, chunk_size=240)
            service_b = FileTransferService(
                serial_manager=mgr_b, chunk_size=240, transfers_dir=downloads_b
            )

            cancelled = {"done": False}

            def _cancel_on_first_chunk(
                _pct: int, _kbps: float, _eta: float, received: int, _total: int
            ) -> None:
                if not cancelled["done"] and received >= 1:
                    cancelled["done"] = True
                    service_b.abort_transfer()

            service_b.progress_updated.connect(_cancel_on_first_chunk)

            with (
                qtbot.waitSignal(service_a.transfer_failed, timeout=10000) as fail_a,
                qtbot.waitSignal(service_b.transfer_failed, timeout=10000) as fail_b,
            ):
                service_a.start_transfer(str(source_file))

            assert cancelled["done"], "receiver never reached first chunk before completion"
            assert "aborted by user" in fail_b.args[0].lower()
            assert "remote cancelled" in fail_a.args[0].lower()
            assert service_a.state == TransferState.ABORTED
            assert service_b.state == TransferState.ABORTED
            assert service_a._tx_file is None
            assert service_b._rx_file_handle is None
            assert not list(downloads_b.glob(".ir_rx_*.part"))
            assert not (downloads_b / source_file.name).exists()

        mgr_a.shutdown()
        mgr_b.shutdown()
