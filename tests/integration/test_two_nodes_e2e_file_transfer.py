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
        # 1. Initialize Node A (Transmitter)
        mgr_a = SerialManager()
        mgr_a.connect_port("MOCK_NODE_A", 115200)

        # 2. Initialize Node B (Receiver)
        mgr_b = SerialManager()
        mgr_b.connect_port("MOCK_NODE_B", 115200)

        # 3. Interconnect Node A and Node B serial streams (bidirectional optical link)
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

            # Node B should emit transfer_completed upon receiving the whole file
            # Node A should emit transfer_completed upon receiving final ACK
            with (
                qtbot.waitSignal(service_a.transfer_completed, timeout=10000) as blocker_a,
                qtbot.waitSignal(service_b.transfer_completed, timeout=10000) as blocker_b,
            ):
                service_a.start_transfer(str(source_file))

            # Verify Node A (Transmitter) result
            fname_a, bytes_a, elapsed_a, crc_a = blocker_a.args
            assert fname_a == source_file.name
            assert bytes_a == len(test_content)
            assert crc_a == f"0x{expected_crc:08X}"
            assert service_a.state == TransferState.COMPLETED

            # Verify Node B (Receiver) result
            fname_b, bytes_b, elapsed_b, crc_b = blocker_b.args
            assert fname_b == source_file.name
            assert bytes_b == len(test_content)
            assert crc_b == f"0x{expected_crc:08X}"
            assert service_b.state == TransferState.COMPLETED

            # Verify physical file written to Node B disk
            received_file = downloads_b / source_file.name
            assert received_file.exists()
            assert received_file.read_bytes() == test_content

            # Verify Node B file store records RX entry
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
