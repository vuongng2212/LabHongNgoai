"""Unit and Integration tests for Stop-and-Wait ARQ Optical File Transfer Service."""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_RETRY_COUNT,
    SOF_MAGIC,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import AckPayload
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
)


class TestFileTransferService:
    """Test suite for optical file chunking and Stop-and-Wait ARQ engine."""

    def test_file_transfer_e2e_mock_loopback(self, qtbot: QtBot) -> None:
        """Verifies end-to-end file transfer over mock loopback serial channel."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

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
            expected_crc = f"0x{zlib.crc32(test_content) & 0xFFFFFFFF:08X}"
            assert crc_hex == expected_crc
            assert service.state == TransferState.COMPLETED
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_large_file_chunking(self, qtbot: QtBot) -> None:
        """Verifies chunking and progress reporting for a multi-kilobyte file."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

        # 5,000 bytes with 128-byte chunk_size => 40 chunks
        test_content = b"".join(f"CHUNK_{i:04d}: {'A' * 110}\n".encode() for i in range(40))
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(test_content)
            tmp_path = tmp.name

        service = FileTransferService(serial_manager=manager, chunk_size=128)
        progress_records: list[tuple[int, float, float, int, int]] = []

        def on_progress(pct: int, speed: float, eta: float, cur: int, total: int) -> None:
            progress_records.append((pct, speed, eta, cur, total))

        service.progress_updated.connect(on_progress)

        try:
            with qtbot.waitSignal(service.transfer_completed, timeout=10000) as blocker:
                service.start_transfer(tmp_path)

            filename, bytes_sent, elapsed_sec, crc_hex = blocker.args
            assert filename == Path(tmp_path).name
            assert bytes_sent == len(test_content)
            assert service.state == TransferState.COMPLETED
            assert len(progress_records) > 0
            last_pct, _, _, last_chunk, total_chunks = progress_records[-1]
            assert last_pct == 100
            assert last_chunk == total_chunks
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_empty_file(self, qtbot: QtBot) -> None:
        """Verifies transfer of a zero-byte file completes safely."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as tmp:
            tmp_path = tmp.name

        service = FileTransferService(serial_manager=manager, chunk_size=128)

        try:
            with qtbot.waitSignal(service.transfer_completed, timeout=3000) as blocker:
                service.start_transfer(tmp_path)

            filename, bytes_sent, _, crc_hex = blocker.args
            assert filename == Path(tmp_path).name
            assert bytes_sent == 0
            assert crc_hex == "0x00000000"
            assert service.state == TransferState.COMPLETED
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_non_existent_file_fails(self, qtbot: QtBot) -> None:
        """Verifies error signal when attempting to transfer a missing file."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
            service.start_transfer("/tmp/non_existent_file_xyz_123.bin")

        (reason,) = blocker.args
        assert "not found" in reason.lower()
        assert service.state == TransferState.IDLE
        manager.shutdown()

    def test_file_transfer_abort_during_handshaking(self, qtbot: QtBot) -> None:
        """Verifies user abort during handshaking immediately halts transfer."""
        manager = SerialManager()
        # Do not connect port so no ACK is generated
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Abort handshaking test content")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state.name == "HANDSHAKING"

            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service.abort_transfer()

            (reason,) = blocker.args
            assert "cancelled by user" in reason.lower()
            assert service.state.name == "ABORTED"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_stale_ack_ignored(self) -> None:
        """Verifies that out-of-order or stale ACK packets are safely ignored."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Stale ACK test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state == TransferState.HANDSHAKING
            expected_seq = service._seq

            # Inject stale ACK with a mismatched sequence number
            wrong_seq = (expected_seq + 5) % 256
            ack_payload = AckPayload(acked_seq=wrong_seq, status=AckStatus.OK).pack()
            header = Header(
                sof=SOF_MAGIC,
                seq=wrong_seq,
                opcode=Opcode.ACK,
                length=len(ack_payload),
            )
            pkt = Packet(header=header, payload=ack_payload, crc32=0, eof=EOF_MAGIC)

            service._handle_packet(pkt)
            # Must remain in HANDSHAKING because the ACK was stale
            assert service.state == TransferState.HANDSHAKING
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_error_status_ack_triggers_retransmission(self) -> None:
        """Verifies ACK with non-OK status triggers retransmission without advancing."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Error status ACK test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state == TransferState.HANDSHAKING
            assert service._retry_count == 0

            # Inject ACK with matching sequence but CRC_ERROR status
            ack_payload = AckPayload(acked_seq=service._seq, status=AckStatus.CRC_ERROR).pack()
            header = Header(
                sof=SOF_MAGIC,
                seq=service._seq,
                opcode=Opcode.ACK,
                length=len(ack_payload),
            )
            pkt = Packet(header=header, payload=ack_payload, crc32=0, eof=EOF_MAGIC)

            service._handle_packet(pkt)
            assert service.state == TransferState.HANDSHAKING
            assert service._retry_count == 1
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_nack_triggers_retransmission(self) -> None:
        """Verifies NACK frame triggers retransmission and increments retry count."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"NACK test content")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state == TransferState.HANDSHAKING
            assert service._retry_count == 0

            # Send NACK
            nack_payload = bytes([service._seq, AckStatus.CRC_ERROR])
            header = Header(
                sof=SOF_MAGIC,
                seq=service._seq,
                opcode=Opcode.NACK,
                length=len(nack_payload),
            )
            pkt = Packet(header=header, payload=nack_payload, crc32=0, eof=EOF_MAGIC)

            service._handle_packet(pkt)
            assert service.state == TransferState.HANDSHAKING
            assert service._retry_count == 1
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_ack_timeout_max_retries_fails(self, qtbot: QtBot) -> None:
        """Verifies transfer transitions to FAILED when ACK timeout retry limit is exceeded."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Timeout failure test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state.name == "HANDSHAKING"

            # Simulate timeout triggers up to MAX_RETRY_COUNT + 1
            for _ in range(MAX_RETRY_COUNT):
                service._on_ack_timeout()
                assert service.state.name == "HANDSHAKING"

            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service._on_ack_timeout()

            (reason,) = blocker.args
            assert "timeout" in reason.lower()
            assert service.state.name == "FAILED"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_nack_max_retries_fails(self, qtbot: QtBot) -> None:
        """Verifies transfer transitions to FAILED when persistent NACK exceeds retry limit."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Persistent NACK failure test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state.name == "HANDSHAKING"

            header = Header(sof=SOF_MAGIC, seq=service._seq, opcode=Opcode.NACK, length=2)
            pkt = Packet(
                header=header,
                payload=bytes([service._seq, AckStatus.CRC_ERROR]),
                crc32=0,
                eof=EOF_MAGIC,
            )

            for _ in range(MAX_RETRY_COUNT):
                service._handle_packet(pkt)
                assert service.state.name == "HANDSHAKING"

            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service._handle_packet(pkt)

            (reason,) = blocker.args
            assert "nack" in reason.lower()
            assert service.state.name == "FAILED"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_busy_state_guard(self) -> None:
        """Verifies start_transfer does not interrupt an ongoing active transfer."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp1:
            tmp1.write(b"File 1")
            path1 = tmp1.name

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp2:
            tmp2.write(b"File 2")
            path2 = tmp2.name

        try:
            service.start_transfer(path1)
            assert service.state == TransferState.HANDSHAKING
            assert service._current_file_path == Path(path1)

            # Attempt to start a second transfer while busy
            service.start_transfer(path2)
            # File path must remain file 1
            assert service._current_file_path == Path(path1)
            assert service.state == TransferState.HANDSHAKING
        finally:
            Path(path1).unlink(missing_ok=True)
            Path(path2).unlink(missing_ok=True)
            manager.shutdown()
