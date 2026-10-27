"""Unit and Integration tests for Stop-and-Wait ARQ Optical File Transfer Service."""

from __future__ import annotations

import tempfile
import zlib
from pathlib import Path
from typing import BinaryIO

import pytest
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_EXTENDED_CHUNK_DATA_SIZE,
    MAX_PAYLOAD_SIZE,
    MAX_RETRY_COUNT,
    MAX_V1_CHUNK_COUNT,
    SOF_MAGIC,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileChunkPayloadLarge,
    FileEndPayload,
    FileEndPayloadLarge,
    FileStartPayload,
    FileStartPayloadLarge,
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


def _state(service: FileTransferService) -> TransferState:
    """Reads a service's live state.

    Goes through a function so mypy does not keep an earlier ``assert``'s enum
    narrowing on the ``state`` property, which would flag later transitions
    (e.g. HANDSHAKING -> ABORTED after a cancel) as impossible.
    """
    return service.state


def _tx_file(service: FileTransferService) -> BinaryIO | None:
    """Reads the TX file handle without leaking mypy optional narrowing."""
    return service._tx_file


class TestFileTransferService:
    """Test suite for optical file chunking and Stop-and-Wait ARQ engine."""

    def test_file_above_legacy_10mb_limit_is_accepted(self, qtbot: QtBot, tmp_path: Path) -> None:
        """The former 10 MB product cap is gone; large files enter ARQ handshaking."""
        manager = SerialManager()
        service = FileTransferService(serial_manager=manager, transfers_dir=tmp_path / "rx")
        source = tmp_path / "above_legacy_limit.bin"
        source.write_bytes(b"\0" * (10 * 1024 * 1024 + 1024))

        try:
            service.start_transfer(str(source))
            assert _state(service) == TransferState.HANDSHAKING
            assert service._file_size == source.stat().st_size
            assert _tx_file(service) is not None
            service.abort_transfer()
            assert _state(service) == TransferState.ABORTED
            assert service._tx_file is None
        finally:
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
            assert _state(service) == TransferState.COMPLETED
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_large_file_chunking(self, qtbot: QtBot) -> None:
        """Verifies chunking and progress reporting for a multi-kilobyte file."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)

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
            assert _state(service) == TransferState.COMPLETED
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
            assert _state(service) == TransferState.COMPLETED
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_too_many_chunks_switch_to_extended_protocol(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """A file needing >65535 legacy chunks uses the large-transfer payloads."""
        manager = SerialManager()
        service = FileTransferService(manager, chunk_size=1, transfers_dir=tmp_path / "rx")
        source = tmp_path / "many_chunks.bin"
        source.write_bytes(b"X" * (MAX_V1_CHUNK_COUNT + 1))
        try:
            service.start_transfer(str(source))
            assert _state(service) == TransferState.HANDSHAKING
            assert service.uses_extended_protocol is True
            assert service._total_chunks == MAX_V1_CHUNK_COUNT + 1
            assert service._cached_start_payload is not None
            assert len(service._cached_start_payload) == 19 + len(source.name)
            service.abort_transfer()
            assert _state(service) == TransferState.ABORTED
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
            assert _state(service) == TransferState.IDLE
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
        assert _state(service) == TransferState.IDLE
        manager.shutdown()

    def test_file_transfer_abort_during_handshaking(self, qtbot: QtBot) -> None:
        """Verifies user abort during handshaking immediately halts transfer."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Abort handshaking test content")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            assert service.state.name == "HANDSHAKING"

            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service.abort_transfer()

            (reason,) = blocker.args
            assert "cancelled by user" in reason.lower()
            assert service.state.name == "ABORTED"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_stale_ack_ignored(self, qtbot: QtBot) -> None:
        """Verifies that out-of-order or stale ACK packets are safely ignored."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Stale ACK test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            service._ack_timer.stop()
            service._retry_count = 0
            expected_seq = service._seq

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
            assert service._current_chunk_idx == 0
        finally:
            service.abort_transfer()
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_error_status_ack_triggers_retransmission(self, qtbot: QtBot) -> None:
        """Verifies ACK with non-OK status triggers retransmission without advancing."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"Error status ACK test")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            service._ack_timer.stop()
            service._retry_count = 0

            ack_payload = AckPayload(acked_seq=service._seq, status=AckStatus.CRC_ERROR).pack()
            header = Header(
                sof=SOF_MAGIC,
                seq=service._seq,
                opcode=Opcode.ACK,
                length=len(ack_payload),
            )
            pkt = Packet(header=header, payload=ack_payload, crc32=0, eof=EOF_MAGIC)

            service._handle_packet(pkt)
            assert service._retry_count == 1
        finally:
            service.abort_transfer()
            Path(tmp_path).unlink(missing_ok=True)
            manager.shutdown()

    def test_file_transfer_nack_triggers_retransmission(self, qtbot: QtBot) -> None:
        """Verifies NACK frame triggers retransmission and increments retry count."""
        manager = SerialManager()
        service = FileTransferService(manager)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"NACK test content")
            tmp_path = tmp.name

        try:
            service.start_transfer(tmp_path)
            service._ack_timer.stop()
            service._retry_count = 0

            nack_payload = bytes([service._seq, AckStatus.CRC_ERROR])
            header = Header(
                sof=SOF_MAGIC,
                seq=service._seq,
                opcode=Opcode.NACK,
                length=len(nack_payload),
            )
            pkt = Packet(header=header, payload=nack_payload, crc32=0, eof=EOF_MAGIC)

            service._handle_packet(pkt)
            assert service._retry_count == 1
        finally:
            service.abort_transfer()
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

    def test_file_transfer_busy_state_guard(self, qtbot: QtBot, tmp_path: Path) -> None:
        """Verifies start_transfer does not interrupt an ongoing active transfer."""
        manager = SerialManager()
        service = FileTransferService(manager)

        path1 = tmp_path / "busy_file_1.txt"
        path1.write_text("File 1")

        path2 = tmp_path / "busy_file_2.txt"
        path2.write_text("File 2")

        try:
            service.start_transfer(str(path1))
            service._ack_timer.stop()
            assert _state(service) == TransferState.HANDSHAKING
            assert service._current_file_path == path1

            service.start_transfer(str(path2))
            assert service._current_file_path == path1
            assert _state(service) == TransferState.HANDSHAKING
        finally:
            service.abort_transfer()
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

            chunk0 = file_bytes[:32]
            chunk1 = file_bytes[32:]

            pkt_chunk0 = Packet(
                header=Header(sof=SOF_MAGIC, seq=11, opcode=Opcode.DATA_CHUNK, length=34),
                payload=FileChunkPayload(chunk_id=0, chunk_data=chunk0).pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )
            service._handle_packet(pkt_chunk0)
            assert service._rx_received_count == 1
            assert service._rx_expected_chunk == 1

            pkt_chunk1 = Packet(
                header=Header(
                    sof=SOF_MAGIC, seq=12, opcode=Opcode.DATA_CHUNK, length=len(chunk1) + 2
                ),
                payload=FileChunkPayload(chunk_id=1, chunk_data=chunk1).pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )
            service._handle_packet(pkt_chunk1)
            assert service._rx_received_count == 2
            assert service._rx_expected_chunk == 2

            end_payload = FileEndPayload(file_crc32=file_crc, total_bytes_sent=len(file_bytes))
            pkt_end = Packet(
                header=Header(sof=SOF_MAGIC, seq=13, opcode=Opcode.TRANSFER_END, length=8),
                payload=end_payload.pack(),
                crc32=123,
                eof=EOF_MAGIC,
            )

            with qtbot.waitSignal(service.transfer_completed, timeout=2000) as blocker:
                service._handle_packet(pkt_end)

            assert _state(service) == TransferState.COMPLETED
            completed_fname, total_b, _, crc_hex = blocker.args
            assert completed_fname == fname
            assert total_b == len(file_bytes)
            assert crc_hex == f"0x{file_crc:08X}"

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

            assert _state(service) == TransferState.FAILED
            (err,) = blocker.args
            assert "missing chunk 1" in err.lower()

        manager.shutdown()

    def test_file_receiver_size_mismatch_fails(self, qtbot: QtBot) -> None:
        """Streamed length must match START file_size and END total_bytes_sent."""
        manager = SerialManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = FileTransferService(manager, transfers_dir=Path(tmp_dir))
            content = b"1234567890"
            crc = zlib.crc32(content) & 0xFFFFFFFF
            start_payload = FileStartPayload(
                file_size=10,
                chunk_size=5,
                total_chunks=2,
                file_crc32=crc,
                filename="short.bin",
            )
            service._handle_packet(
                Packet(
                    header=Header(sof=SOF_MAGIC, seq=1, opcode=Opcode.TRANSFER_START, length=10),
                    payload=start_payload.pack(),
                    crc32=123,
                    eof=EOF_MAGIC,
                )
            )
            for idx in range(2):
                chunk = FileChunkPayload(chunk_id=idx, chunk_data=content[idx * 5 : (idx + 1) * 5])
                service._handle_packet(
                    Packet(
                        header=Header(
                            sof=SOF_MAGIC, seq=2 + idx, opcode=Opcode.DATA_CHUNK, length=7
                        ),
                        payload=chunk.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            end_payload = FileEndPayload(file_crc32=crc, total_bytes_sent=8)
            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(
                    Packet(
                        header=Header(sof=SOF_MAGIC, seq=4, opcode=Opcode.TRANSFER_END, length=8),
                        payload=end_payload.pack(),
                        crc32=123,
                        eof=EOF_MAGIC,
                    )
                )

            assert _state(service) == TransferState.FAILED
            (err,) = blocker.args
            assert "size mismatch" in err.lower()
            assert service._rx_temp_path is None

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

            assert _state(service) == TransferState.FAILED
            (err,) = blocker.args
            assert "checksum mismatch" in err.lower()

        manager.shutdown()

    def test_file_receiver_abort_clears_buffer(self, qtbot: QtBot) -> None:
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
            assert service._rx_received_count == 0
            assert service._rx_file_handle is None
            assert service._rx_temp_path is None

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

            assert _state(service) == TransferState.TRANSFERRING
            assert service._rx_filename == ""
            qtbot.waitUntil(lambda: len(raw_tx) > 0, timeout=2000)
            assert Opcode.NACK in _nack_opcodes_in(raw_tx)
        finally:
            manager.shutdown()

    def test_rx_start_above_legacy_limit_is_accepted(self, qtbot: QtBot, tmp_path: Path) -> None:
        """The receiver no longer rejects files above the old 10 MB cap."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager, transfers_dir=tmp_path / "rx")
        try:
            failures: list[str] = []
            service.transfer_failed.connect(failures.append)

            file_size = 10 * 1024 * 1024 + 1024
            chunk_size = 240
            total = (file_size + chunk_size - 1) // chunk_size
            start = FileStartPayload(
                file_size=file_size,
                chunk_size=chunk_size,
                total_chunks=total,
                file_crc32=1,
                filename="huge.bin",
            )
            service._handle_packet(_rx_packet(3, Opcode.TRANSFER_START, start.pack()))

            assert _state(service) == TransferState.RECEIVING
            assert service._rx_file_size == file_size
            assert failures == []
            service.abort_transfer()
            assert service._rx_temp_path is None
        finally:
            manager.shutdown()

    def test_rx_large_opcode_accepts_u64_file_size(self, qtbot: QtBot, tmp_path: Path) -> None:
        """TRANSFER_START_LARGE accepts a file size above 4 GiB without RAM use."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(serial_manager=manager, transfers_dir=tmp_path / "rx")
        try:
            file_size = 5 * 1024 * 1024 * 1024
            chunk_size = MAX_EXTENDED_CHUNK_DATA_SIZE
            total = (file_size + chunk_size - 1) // chunk_size
            start = FileStartPayloadLarge(
                file_size=file_size,
                chunk_size=chunk_size,
                total_chunks=total,
                file_crc32=1,
                filename="huge_large.bin",
            )
            service._handle_packet(_rx_packet(3, Opcode.TRANSFER_START_LARGE, start.pack()))

            assert _state(service) == TransferState.RECEIVING
            assert service._rx_extended is True
            assert service._rx_file_size == file_size
            service.abort_transfer()
        finally:
            manager.shutdown()

    def test_rx_invalid_chunk_size_rejected(self, qtbot: QtBot) -> None:
        """Verifies receiver refuses zero or over-limit chunk sizes."""
        manager = SerialManager()
        service = FileTransferService(serial_manager=manager)
        try:
            failures: list[str] = []
            service.transfer_failed.connect(failures.append)

            for bad_size in (0, MAX_PAYLOAD_SIZE + 1):
                start = FileStartPayload(
                    file_size=100,
                    chunk_size=bad_size,
                    total_chunks=2,
                    file_crc32=1,
                    filename="x.bin",
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

            assert _state(service) == TransferState.IDLE
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
            assert _state(service) == TransferState.RECEIVING
        finally:
            manager.shutdown()

    def test_rx_duplicate_end_reacks_after_completion(self, qtbot: QtBot, tmp_path: Path) -> None:
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
            assert _state(service) == TransferState.COMPLETED
            assert len(completed) == 1

            service._handle_packet(_rx_packet(13, Opcode.TRANSFER_END, end))
            qtbot.waitUntil(lambda: ack_count() >= 2, timeout=2000)
            assert _state(service) == TransferState.COMPLETED
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
            assert _state(service) == TransferState.HANDSHAKING

            stale_seq = (service._seq + 1) % 256
            nack_payload = AckPayload(acked_seq=stale_seq, status=AckStatus.CRC_ERROR).pack()
            service._handle_packet(_rx_packet(stale_seq, Opcode.NACK, nack_payload))

            assert service._retry_count == 0
            assert _state(service) == TransferState.HANDSHAKING
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


class TestStreamingAndCancellation:
    """Covers streaming I/O and symmetric sender/receiver cancellation."""

    def test_tx_streams_without_read_bytes(
        self, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """TX must not call Path.read_bytes; only the current chunk is held in RAM."""
        manager = SerialManager()
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        source = tmp_path / "stream.bin"
        source.write_bytes(b"streaming payload" * 1024)

        def _forbidden_read_bytes(self: Path) -> bytes:
            raise AssertionError("read_bytes() must not be used for TX streaming")

        monkeypatch.setattr(Path, "read_bytes", _forbidden_read_bytes)
        try:
            service.start_transfer(str(source))
            assert _state(service) == TransferState.HANDSHAKING
            assert _tx_file(service) is not None
            service.abort_transfer()
            assert service._tx_file is None
        finally:
            manager.shutdown()

    def test_rx_writes_chunks_to_part_file_incrementally(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """RX appends each chunk to a .part file instead of buffering in a dict."""
        manager = SerialManager()
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        content = b"0123456789"
        crc = zlib.crc32(content) & 0xFFFFFFFF
        start = FileStartPayload(10, 5, 2, crc, "stream_rx.bin")
        service._handle_packet(_rx_packet(1, Opcode.TRANSFER_START, start.pack()))

        part_path = service._rx_temp_path
        assert part_path is not None
        service._handle_packet(
            _rx_packet(2, Opcode.DATA_CHUNK, FileChunkPayload(0, content[:5]).pack())
        )
        assert part_path.exists()
        assert part_path.stat().st_size == 5
        assert service._rx_received_count == 1
        assert service._rx_bytes_received == 5

        service.abort_transfer()
        assert not part_path.exists()
        assert service._rx_temp_path is None
        manager.shutdown()

    def test_sender_abort_sends_cancel_and_releases_handle(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Sender cancel emits TRANSFER_PAUSE/ABORT, closes file and resets state."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        source = tmp_path / "cancel_tx.bin"
        source.write_bytes(b"cancel me" * 100)
        raw_tx: list[bytes] = []
        manager.raw_tx.connect(raw_tx.append)
        try:
            service.start_transfer(str(source))
            assert _tx_file(service) is not None

            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service.abort_transfer()

            assert "cancelled by user" in blocker.args[0].lower()
            assert _state(service) == TransferState.ABORTED
            assert service._tx_file is None
            assert service._current_file_path is None
            qtbot.waitUntil(lambda: len(raw_tx) > 0, timeout=2000)
            opcodes = _nack_opcodes_in(raw_tx)
            assert Opcode.TRANSFER_PAUSE in opcodes
        finally:
            manager.shutdown()

    def test_receiver_abort_sends_cancel_to_sender(self, qtbot: QtBot, tmp_path: Path) -> None:
        """Receiver-side cancel notifies the peer and removes partial data."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        raw_tx: list[bytes] = []
        manager.raw_tx.connect(raw_tx.append)
        try:
            start = FileStartPayload(10, 5, 2, zlib.crc32(b"0123456789"), "rx_cancel.bin")
            service._handle_packet(_rx_packet(7, Opcode.TRANSFER_START, start.pack()))
            assert _state(service) == TransferState.RECEIVING
            part_path = service._rx_temp_path
            assert part_path is not None

            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service.abort_transfer()

            assert "aborted by user" in blocker.args[0].lower()
            assert _state(service) == TransferState.ABORTED
            assert service._rx_file_handle is None
            assert service._rx_temp_path is None
            assert not part_path.exists()
            qtbot.waitUntil(lambda: len(raw_tx) > 0, timeout=2000)
            assert Opcode.TRANSFER_PAUSE in _nack_opcodes_in(raw_tx)
        finally:
            manager.shutdown()

    def test_remote_cancel_stops_active_sender(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A TRANSFER_PAUSE/ABORT from the receiver tears down an active TX session."""
        manager = SerialManager()
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        source = tmp_path / "remote_cancel_tx.bin"
        source.write_bytes(b"remote cancel" * 100)
        try:
            service.start_transfer(str(source))
            assert _state(service) == TransferState.HANDSHAKING
            assert _tx_file(service) is not None

            pause = b"ABORT"
            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(_rx_packet(99, Opcode.TRANSFER_PAUSE, pause))

            assert "remote cancelled" in blocker.args[0].lower()
            assert _state(service) == TransferState.ABORTED
            assert service._tx_file is None
            assert service._current_file_path is None
        finally:
            manager.shutdown()

    def test_remote_cancel_stops_active_receiver_and_cleans_part(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """A TRANSFER_PAUSE/ABORT from the sender removes the RX .part file."""
        manager = SerialManager()
        manager.connect_port("MOCK_COM_LOOPBACK", 115200)
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        try:
            start = FileStartPayload(10, 5, 2, zlib.crc32(b"0123456789"), "remote_rx.bin")
            service._handle_packet(_rx_packet(1, Opcode.TRANSFER_START, start.pack()))
            part_path = service._rx_temp_path
            assert part_path is not None and part_path.exists()

            with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
                service._handle_packet(_rx_packet(2, Opcode.TRANSFER_PAUSE, b"ABORT"))

            assert "remote aborted" in blocker.args[0].lower()
            assert _state(service) == TransferState.ABORTED
            assert service._rx_temp_path is None
            assert not part_path.exists()
        finally:
            manager.shutdown()

    def test_rx_large_chunk_extension_roundtrip(self, qtbot: QtBot, tmp_path: Path) -> None:
        """RX accepts DATA_CHUNK_LARGE streams and verifies the published file."""
        manager = SerialManager()
        service = FileTransferService(manager, transfers_dir=tmp_path / "rx")
        content = b"L" * 1200
        chunk_size = MAX_EXTENDED_CHUNK_DATA_SIZE
        total = (len(content) + chunk_size - 1) // chunk_size
        crc = zlib.crc32(content) & 0xFFFFFFFF
        try:
            start = FileStartPayloadLarge(
                file_size=len(content),
                chunk_size=chunk_size,
                total_chunks=total,
                file_crc32=crc,
                filename="large_mode.bin",
            )
            service._handle_packet(_rx_packet(1, Opcode.TRANSFER_START_LARGE, start.pack()))
            for idx in range(total):
                part = content[idx * chunk_size : (idx + 1) * chunk_size]
                service._handle_packet(
                    _rx_packet(
                        2 + idx,
                        Opcode.DATA_CHUNK_LARGE,
                        FileChunkPayloadLarge(idx, part).pack(),
                    )
                )

            completed: list[tuple[str, int, float, str]] = []
            service.transfer_completed.connect(lambda *args: completed.append(args))
            end = FileEndPayloadLarge(crc, len(content)).pack()
            service._handle_packet(_rx_packet(50, Opcode.TRANSFER_END_LARGE, end))

            assert _state(service) == TransferState.COMPLETED
            assert completed and completed[0][1] == len(content)
            assert (tmp_path / "rx" / "large_mode.bin").read_bytes() == content
        finally:
            manager.shutdown()


class TestRxDestinationDirectory:
    """Covers the configurable receive directory and its per-session lock."""

    @staticmethod
    def _feed_rx(service: FileTransferService, filename: str, content: bytes) -> None:
        """Drives a complete RX session (START + chunks + END) into the service."""
        chunk_size = 8
        total = (len(content) + chunk_size - 1) // chunk_size if content else 1
        crc = zlib.crc32(content) & 0xFFFFFFFF
        service._handle_packet(
            _rx_packet(
                1,
                Opcode.TRANSFER_START,
                FileStartPayload(len(content), chunk_size, total, crc, filename).pack(),
            )
        )
        for idx in range(total):
            part = content[idx * chunk_size : (idx + 1) * chunk_size]
            service._handle_packet(
                _rx_packet(2 + idx, Opcode.DATA_CHUNK, FileChunkPayload(idx, part).pack())
            )
        service._handle_packet(
            _rx_packet(9 + total, Opcode.TRANSFER_END, FileEndPayload(crc, len(content)).pack())
        )

    def test_rx_writes_to_configured_custom_dir(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A receive dir saved through settings is where the produced file lands."""
        manager = SerialManager()
        storage = StorageService(data_dir=tmp_path / "data")
        default_dir = tmp_path / "rx_default"
        custom_dir = tmp_path / "custom" / "incoming"
        service = FileTransferService(manager, storage_service=storage, transfers_dir=default_dir)
        content = b"hello custom dir"  # 16 bytes -> 2 chunks
        try:
            resolved = service.set_transfers_dir(custom_dir)
            assert storage.get_setting("transfers_dir") == str(resolved)

            self._feed_rx(service, "custom.bin", content)

            assert _state(service) == TransferState.COMPLETED
            assert (custom_dir / "custom.bin").read_bytes() == content
            assert not (default_dir / "custom.bin").exists()
        finally:
            manager.shutdown()

    def test_rx_session_locks_destination_dir_across_settings_change(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Changing transfers_dir mid-RX must not relocate the target (no EXDEV)."""
        manager = SerialManager()
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        service = FileTransferService(manager, transfers_dir=dir_a)
        content = b"LOCK-CONTENT"  # 12 bytes
        try:
            service._handle_packet(
                _rx_packet(
                    1,
                    Opcode.TRANSFER_START,
                    FileStartPayload(
                        len(content), 4, 3, zlib.crc32(content) & 0xFFFFFFFF, "locked.bin"
                    ).pack(),
                )
            )
            assert service._rx_dest_dir == dir_a
            part_path = service._rx_temp_path
            assert part_path is not None and part_path.parent == dir_a

            service.set_transfers_dir(dir_b)
            assert service.transfers_dir_path == dir_b.resolve()

            for idx in range(3):
                part = content[idx * 4 : (idx + 1) * 4]
                service._handle_packet(
                    _rx_packet(2 + idx, Opcode.DATA_CHUNK, FileChunkPayload(idx, part).pack())
                )
            service._handle_packet(
                _rx_packet(
                    20,
                    Opcode.TRANSFER_END,
                    FileEndPayload(zlib.crc32(content) & 0xFFFFFFFF, len(content)).pack(),
                )
            )

            assert _state(service) == TransferState.COMPLETED
            assert (dir_a / "locked.bin").read_bytes() == content
            assert not (dir_b / "locked.bin").exists()
            assert not list(dir_a.glob(".ir_rx_*.part"))
            assert service._rx_dest_dir is None
        finally:
            manager.shutdown()

    def test_default_transfers_dir_is_immutable(self, qtbot: QtBot, tmp_path: Path) -> None:
        """The reset target stays at the constructed default after custom changes."""
        manager = SerialManager()
        default_dir = tmp_path / "rx_default"
        service = FileTransferService(manager, transfers_dir=default_dir)
        try:
            assert service.default_transfers_dir_path.resolve() == default_dir.resolve()
            service.set_transfers_dir(tmp_path / "rx_custom")
            assert service.transfers_dir_path == (tmp_path / "rx_custom").resolve()
            assert service.default_transfers_dir_path.resolve() == default_dir.resolve()

            service.set_transfers_dir(service.default_transfers_dir_path)
            assert service.transfers_dir_path == default_dir.resolve()
        finally:
            manager.shutdown()
