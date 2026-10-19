"""Unit and Integration tests for Stop-and-Wait ARQ Optical File Transfer Service."""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_FILE_SIZE_BYTES,
    MAX_PAYLOAD_SIZE,
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
from ir_terminal.protocol.framer import parse_frames_from_buffer
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
)
from ir_terminal.services.storage_service import StorageService


def _rx_packet(seq: int, opcode: int, payload: bytes) -> Packet:
    """Builds an inbound packet frame bypassing wire parsing."""
    header = Header(sof=SOF_MAGIC, seq=seq, opcode=opcode, length=len(payload))
    return Packet(header=header, payload=payload, crc32=0, eof=EOF_MAGIC)


def _nack_opcodes_in(raw_frames: list[bytes]) -> list[int]:
    """Extracts opcodes from captured raw TX byte blobs."""
    opcodes: list[int] = []
    for blob in raw_frames:
        packets, _ = parse_frames_from_buffer(blob)
        opcodes.extend(pkt.opcode for pkt in packets)
    return opcodes


class TestFileTransferService:
    """Test suite for optical file chunking and Stop-and-Wait ARQ engine."""

    def test_oversize_file_rejected_before_transmission(self, qtbot: QtBot) -> None:
        """Verifies files above MAX_FILE_SIZE_BYTES fail fast without entering ARQ."""
        manager = SerialManager()
        service = FileTransferService(serial_manager=manager)

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(b"\0" * (MAX_FILE_SIZE_BYTES + 1))
            tmp_path = tmp.name

        try:
            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service.start_transfer(tmp_path)

            (reason,) = blocker.args
            assert "exceeds" in reason
            assert service.state == TransferState.IDLE
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

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

    def test_too_many_chunks_rejected_before_read(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A file needing >65535 chunks is rejected cleanly instead of struct.error."""
        manager = SerialManager()
        service = FileTransferService(manager, chunk_size=1)
        source = tmp_path / "too_many_chunks.bin"
        source.write_bytes(b"X" * 70_000)  # 70,000 chunks at 1B/chunk
        try:
            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service.start_transfer(str(source))
            (reason,) = blocker.args
            assert "chunks" in reason.lower()
            assert service.state == TransferState.IDLE
        finally:
            manager.shutdown()

    def test_invalid_chunk_size_rejected(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A chunk_size that cannot fit DATA_CHUNK (2B prefix) is rejected cleanly."""
        manager = SerialManager()
        service = FileTransferService(manager, chunk_size=MAX_PAYLOAD_SIZE)
        source = tmp_path / "bad_chunk_size.bin"
        source.write_bytes(b"test")
        try:
            with qtbot.waitSignal(service.transfer_failed, timeout=1000) as blocker:
                service.start_transfer(str(source))
            (reason,) = blocker.args
            assert "chunk size" in reason.lower()
            assert service.state == TransferState.IDLE
        finally:
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

    def test_file_receiver_size_mismatch_fails(self, qtbot: QtBot) -> None:
        """Assembled length must match START file_size and END total_bytes_sent."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FileTransferService(manager, transfers_dir=Path(tmp_dir))
            short_content = b"12345678"  # START declares 10B, chunks carry only 8B
            crc = zlib.crc32(short_content) & 0xFFFFFFFF
            start_payload = FileStartPayload(
                file_size=10,
                chunk_size=5,
                total_chunks=2,
                file_crc32=crc,
                filename="short.bin",
            )
            service._handle_packet(
                Packet(
                    header=Header(
                        sof=SOF_MAGIC, seq=1, opcode=Opcode.TRANSFER_START, length=10
                    ),
                    payload=start_payload.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )
            for idx in range(2):
                chunk = FileChunkPayload(chunk_id=idx, chunk_data=short_content[idx * 4 : (idx + 1) * 4])
                service._handle_packet(
                    Packet(
                        header=Header(
                            sof=SOF_MAGIC, seq=2 + idx, opcode=Opcode.DATA_CHUNK, length=6
                        ),
                        payload=chunk.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            end_payload = FileEndPayload(file_crc32=crc, total_bytes_sent=len(short_content))
            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(
                    Packet(
                        header=Header(
                            sof=SOF_MAGIC, seq=4, opcode=Opcode.TRANSFER_END, length=8
                        ),
                        payload=end_payload.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            assert service.state == TransferState.FAILED
            (err,) = blocker.args
            assert "size mismatch" in err.lower()

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


class TestFileTransferReceiver:
    """Test suite for the RX (receiver) side: guards, reassembly, and accounting."""

    def test_rx_start_rejected_while_tx_busy(self, qtbot: QtBot) -> None:
        """Verifies incoming START never clobbers an ongoing TX session (NACK backpressure)."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager)
        try:
            service._state = TransferState.TRANSFERRING

            raw_tx: list[bytes] = []
            manager.raw_tx.connect(raw_tx.append)

            start = FileStartPayload(
                file_size=100, chunk_size=50, total_chunks=2, file_crc32=1, filename="other.bin"
            )
            service._handle_packet(_rx_packet(9, Opcode.TRANSFER_START, start.pack()))

            assert service.state == TransferState.TRANSFERRING
            assert service._rx_filename == ""
            qtbot.waitUntil(lambda: len(raw_tx) > 0, timeout=2000)
            assert Opcode.NACK in _nack_opcodes_in(raw_tx)
        finally:
            manager.shutdown()

    def test_rx_oversize_start_rejected(self, qtbot: QtBot) -> None:
        """Verifies receiver refuses files above MAX_FILE_SIZE_BYTES."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager)
        try:
            failures: list[str] = []
            service.transfer_failed.connect(failures.append)

            start = FileStartPayload(
                file_size=MAX_FILE_SIZE_BYTES + 1,
                chunk_size=240,
                total_chunks=999,
                file_crc32=1,
                filename="huge.bin",
            )
            service._handle_packet(_rx_packet(3, Opcode.TRANSFER_START, start.pack()))

            assert service.state == TransferState.FAILED
            assert service._rx_received_chunks == {}
            assert any("exceeds" in reason for reason in failures)
        finally:
            manager.shutdown()

    def test_rx_invalid_chunk_size_rejected(self) -> None:
        """Verifies receiver refuses zero or over-limit chunk sizes."""
        manager = SerialManager()
        service = FileTransferService(serial_manager=manager)
        try:
            failures: list[str] = []
            service.transfer_failed.connect(failures.append)

            for bad_size in (0, MAX_PAYLOAD_SIZE + 1):
                start = FileStartPayload(
                    file_size=100, chunk_size=bad_size, total_chunks=2, file_crc32=1, filename="x.bin"
                )
                service._handle_packet(_rx_packet(4, Opcode.TRANSFER_START, start.pack()))

            assert any("chunk size" in reason for reason in failures)
            assert service._state == TransferState.FAILED
        finally:
            manager.shutdown()

    def test_rx_malformed_start_nacks_without_state_change(self, qtbot: QtBot) -> None:
        """Verifies truncated START payload triggers NACK and keeps IDLE state."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager)
        try:
            raw_tx: list[bytes] = []
            manager.raw_tx.connect(raw_tx.append)

            service._handle_packet(_rx_packet(5, Opcode.TRANSFER_START, b"\x01\x02"))

            assert service.state == TransferState.IDLE
            qtbot.waitUntil(lambda: len(raw_tx) > 0, timeout=2000)
            assert Opcode.NACK in _nack_opcodes_in(raw_tx)
        finally:
            manager.shutdown()

    def test_rx_duplicate_start_reacks_same_session(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A retransmitted START (lost ACK) must be re-ACKed, not NACKed as busy."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager, transfers_dir=tmp_path / "rx")
        raw_tx: list[bytes] = []
        manager.raw_tx.connect(raw_tx.append)
        try:
            start = FileStartPayload(
                file_size=10,
                chunk_size=5,
                total_chunks=2,
                file_crc32=zlib.crc32(b"0123456789") & 0xFFFFFFFF,
                filename="dup_start.bin",
            )
            pkt = _rx_packet(7, Opcode.TRANSFER_START, start.pack())
            service._handle_packet(pkt)
            qtbot.waitUntil(lambda: len(raw_tx) >= 1, timeout=2000)

            raw_tx.clear()
            service._handle_packet(pkt)  # identical retransmission
            qtbot.waitUntil(lambda: len(raw_tx) >= 1, timeout=2000)

            opcodes = _nack_opcodes_in(raw_tx)
            assert Opcode.ACK in opcodes
            assert Opcode.NACK not in opcodes
            assert service.state == TransferState.RECEIVING
        finally:
            manager.shutdown()

    def test_rx_duplicate_end_reacks_after_completion(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """A retransmitted END (lost final ACK) must be re-ACKed without re-saving."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager, transfers_dir=tmp_path / "rx")
        raw_tx: list[bytes] = []
        manager.raw_tx.connect(raw_tx.append)
        completed: list[tuple[str, int, float, str]] = []
        service.transfer_completed.connect(lambda *args: completed.append(args))
        try:
            content = b"0123456789"
            crc = zlib.crc32(content) & 0xFFFFFFFF
            start = FileStartPayload(10, 5, 2, crc, "dup_end.bin")
            service._handle_packet(_rx_packet(10, Opcode.TRANSFER_START, start.pack()))
            service._handle_packet(
                _rx_packet(11, Opcode.DATA_CHUNK, FileChunkPayload(0, content[:5]).pack())
            )
            service._handle_packet(
                _rx_packet(12, Opcode.DATA_CHUNK, FileChunkPayload(1, content[5:]).pack())
            )
            end = FileEndPayload(crc, len(content)).pack()
            service._handle_packet(_rx_packet(13, Opcode.TRANSFER_END, end))

            def ack_count() -> int:
                count = 0
                for blob in raw_tx:
                    packets, _ = parse_frames_from_buffer(blob)
                    for pkt in packets:
                        if pkt.opcode != Opcode.ACK:
                            continue
                        ack_info = AckPayload.unpack(pkt.payload)
                        if ack_info.acked_seq == 13:
                            count += 1
                return count

            qtbot.waitUntil(lambda: ack_count() >= 1, timeout=2000)
            assert service.state == TransferState.COMPLETED
            assert len(completed) == 1

            service._handle_packet(_rx_packet(13, Opcode.TRANSFER_END, end))
            qtbot.waitUntil(lambda: ack_count() >= 2, timeout=2000)
            assert service.state == TransferState.COMPLETED
            assert len(completed) == 1
            assert Opcode.NACK not in _nack_opcodes_in(raw_tx)
        finally:
            manager.shutdown()

    def test_stale_nack_does_not_consume_retry_budget(self, tmp_path: Path) -> None:
        """A NACK carrying an old acked_seq must not retransmit the current frame."""
        manager = SerialManager()
        service = FileTransferService(manager)
        tmp_file = tmp_path / "stale_nack.bin"
        tmp_file.write_bytes(b"stale nack test")
        try:
            service.start_transfer(str(tmp_file))
            service._ack_timer.stop()
            assert service.state == TransferState.HANDSHAKING

            stale_seq = (service._seq + 1) % 256
            nack_payload = AckPayload(acked_seq=stale_seq, status=AckStatus.CRC_ERROR).pack()
            service._handle_packet(_rx_packet(stale_seq, Opcode.NACK, nack_payload))

            assert service._retry_count == 0
            assert service.state == TransferState.HANDSHAKING
        finally:
            manager.shutdown()

    def test_rx_full_reassembly_with_duplicate_chunk(self, tmp_path: Path) -> None:
        """Verifies end-to-end RX: START, chunks (one duplicated), END, file + record."""
        import tempfile

        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                storage = StorageService(data_dir=Path(tmpdir) / "data")
                service = FileTransferService(
                    serial_manager=manager,
                    storage_service=storage,
                    transfers_dir=Path(tmpdir) / "rx",
                )

                content = b"hello world test data, chunked!"
                chunk_size = 8
                total = (len(content) + chunk_size - 1) // chunk_size
                crc = zlib.crc32(content) & 0xFFFFFFFF

                completed: list[tuple[str, int, float, str]] = []
                service.transfer_completed.connect(lambda *args: completed.append(args))

                start = FileStartPayload(
                    file_size=len(content),
                    chunk_size=chunk_size,
                    total_chunks=total,
                    file_crc32=crc,
                    filename="incoming.bin",
                )
                service._handle_packet(_rx_packet(10, Opcode.TRANSFER_START, start.pack()))
                assert service.state.name == "RECEIVING"

                for idx in range(total):
                    part = content[idx * chunk_size : (idx + 1) * chunk_size]
                    chunk = FileChunkPayload(chunk_id=idx, chunk_data=part)
                    service._handle_packet(_rx_packet(11 + idx, Opcode.DATA_CHUNK, chunk.pack()))

                # Duplicate retransmit of chunk 1 must not double-count bytes
                dup = FileChunkPayload(chunk_id=1, chunk_data=content[chunk_size : 2 * chunk_size])
                service._handle_packet(_rx_packet(40, Opcode.DATA_CHUNK, dup.pack()))
                assert service._rx_bytes_received == len(content)

                end = FileEndPayload(file_crc32=crc, total_bytes_sent=len(content))
                service._handle_packet(_rx_packet(50, Opcode.TRANSFER_END, end.pack()))

                assert service.state.name == "COMPLETED"
                assert len(completed) == 1
                assert completed[0][0] == "incoming.bin"
                assert completed[0][1] == len(content)

                saved = Path(tmpdir) / "rx" / "incoming.bin"
                assert saved.read_bytes() == content

                records = storage.get_file_transfers()
                assert len(records) == 1
                assert records[0]["direction"] == "RX"
                assert records[0]["file_path"] == str(saved)
                assert records[0]["status"] == "SUCCESS"
        finally:
            manager.shutdown()
