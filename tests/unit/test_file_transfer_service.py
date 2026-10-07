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
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileEndPayload,
    FileStartPayload,
)
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

    def test_file_receiver_full_cycle_success(self, qtbot: QtBot) -> None:
        """Verifies receiver state machine handles TRANSFER_START, DATA_CHUNK, and TRANSFER_END."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest_dir = Path(tmp_dir) / "downloads"
            service = FileTransferService(manager, transfers_dir=dest_dir)

            file_bytes = b"Hello from Optical Transmitter! Chunked binary payload test."
            file_crc = zlib.crc32(file_bytes) & 0xFFFFFFFF
            fname = "sample_optical_doc.bin"

            # 1. Incoming TRANSFER_START
            start_payload = FileStartPayload(
                file_size=len(file_bytes),
                chunk_size=32,
                total_chunks=2,
                file_crc32=file_crc,
                filename=fname,
            )
            pkt_start = Packet(
                header=Header(
                    sof=SOF_MAGIC,
                    seq=10,
                    opcode=Opcode.TRANSFER_START,
                    length=len(start_payload.pack()),
                ),
                payload=start_payload.pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )
            service._handle_packet(pkt_start)
            assert service.state.name == "RECEIVING"

            # 2. Incoming Chunks
            chunk0 = file_bytes[:32]
            chunk1 = file_bytes[32:]

            pkt_chunk0 = Packet(
                header=Header(sof=SOF_MAGIC, seq=11, opcode=Opcode.DATA_CHUNK, length=34),
                payload=FileChunkPayload(chunk_id=0, chunk_data=chunk0).pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )
            service._handle_packet(pkt_chunk0)
            assert len(service._rx_received_chunks) == 1

            pkt_chunk1 = Packet(
                header=Header(
                    sof=SOF_MAGIC, seq=12, opcode=Opcode.DATA_CHUNK, length=len(chunk1) + 2
                ),
                payload=FileChunkPayload(chunk_id=1, chunk_data=chunk1).pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )
            service._handle_packet(pkt_chunk1)
            assert len(service._rx_received_chunks) == 2

            # 3. Incoming TRANSFER_END
            end_payload = FileEndPayload(file_crc32=file_crc, total_bytes_sent=len(file_bytes))
            pkt_end = Packet(
                header=Header(sof=SOF_MAGIC, seq=13, opcode=Opcode.TRANSFER_END, length=8),
                payload=end_payload.pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )

            with qtbot.waitSignal(service.transfer_completed, timeout=2000) as blocker:
                service._handle_packet(pkt_end)

            assert service.state == TransferState.COMPLETED
            completed_fname, total_b, _, crc_hex = blocker.args
            assert completed_fname == fname
            assert total_b == len(file_bytes)
            assert crc_hex == f"0x{file_crc:08X}"

            # Verify saved file on disk
            saved_file = dest_dir / fname
            assert saved_file.exists()
            assert saved_file.read_bytes() == file_bytes

        manager.shutdown()

    def test_file_receiver_missing_chunk_fails(self, qtbot: QtBot) -> None:
        """Verifies missing chunk during TRANSFER_END triggers NACK and fails."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FileTransferService(manager, transfers_dir=Path(tmp_dir))
            file_bytes = b"0123456789"
            file_crc = zlib.crc32(file_bytes) & 0xFFFFFFFF

            # Expect 2 chunks but only send chunk 0
            start_payload = FileStartPayload(
                file_size=len(file_bytes),
                chunk_size=5,
                total_chunks=2,
                file_crc32=file_crc,
                filename="missing.bin",
            )
            service._handle_packet(
                Packet(
                    header=Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.TRANSFER_START, length=10),
                    payload=start_payload.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )

            chunk0 = FileChunkPayload(chunk_id=0, chunk_data=file_bytes[:5])
            service._handle_packet(
                Packet(
                    header=Header(sof=SOF_MAGIC, seq=2, opcode=Opcode.DATA_CHUNK, length=7),
                    payload=chunk0.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )

            # Send TRANSFER_END without sending chunk 1
            end_payload = FileEndPayload(file_crc32=file_crc, total_bytes_sent=len(file_bytes))
            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(
                    Packet(
                        header=Header(sof=SOF_MAGIC, seq=3, opcode=Opcode.TRANSFER_END, length=8),
                        payload=end_payload.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            assert service.state == TransferState.FAILED
            (err,) = blocker.args
            assert "missing chunk 1" in err.lower()

        manager.shutdown()

    def test_file_receiver_crc_mismatch_fails(self, qtbot: QtBot) -> None:
        """Verifies that received data with checksum mismatch is rejected."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FileTransferService(manager, transfers_dir=Path(tmp_dir))
            file_bytes = b"Corrupted optical stream"
            start_payload = FileStartPayload(
                file_size=len(file_bytes),
                chunk_size=len(file_bytes),
                total_chunks=1,
                file_crc32=0x11223344,  # Intentionally wrong CRC
                filename="bad_crc.bin",
            )
            service._handle_packet(
                Packet(
                    header=Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.TRANSFER_START, length=10),
                    payload=start_payload.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )

            chunk0 = FileChunkPayload(chunk_id=0, chunk_data=file_bytes)
            service._handle_packet(
                Packet(
                    header=Header(
                        sof=SOF_MAGIC, seq=2, opcode=Opcode.DATA_CHUNK, length=len(file_bytes) + 2
                    ),
                    payload=chunk0.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )

            end_payload = FileEndPayload(file_crc32=0x11223344, total_bytes_sent=len(file_bytes))
            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(
                    Packet(
                        header=Header(sof=SOF_MAGIC, seq=3, opcode=Opcode.TRANSFER_END, length=8),
                        payload=end_payload.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            assert service.state == TransferState.FAILED
            (err,) = blocker.args
            assert "checksum mismatch" in err.lower()

        manager.shutdown()

    def test_file_receiver_abort_clears_buffer(self) -> None:
        """Verifies aborting an incoming file transfer cleanly resets receiver state."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FileTransferService(manager, transfers_dir=Path(tmp_dir))
            start_payload = FileStartPayload(
                file_size=100,
                chunk_size=50,
                total_chunks=2,
                file_crc32=123,
                filename="abort_rx.bin",
            )
            service._handle_packet(
                Packet(
                    header=Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.TRANSFER_START, length=10),
                    payload=start_payload.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )
            assert service.state.name == "RECEIVING"

            service.abort_transfer()
            assert service.state.name == "ABORTED"
            assert len(service._rx_received_chunks) == 0

        manager.shutdown()
