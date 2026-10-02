"""Unit tests for binary file transfer and ACK payload serialization."""

from __future__ import annotations

import pytest

from ir_terminal.protocol.constants import AckStatus
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileEndPayload,
    FileStartPayload,
)


class TestFilePayloads:
    """Test suite for binary struct packing and unpacking."""

    def test_file_start_payload_pack_unpack(self) -> None:
        """Verifies FileStartPayload packs and unpacks metadata correctly."""
        payload = FileStartPayload(
            file_size=1048576,
            chunk_size=256,
            total_chunks=4096,
            file_crc32=0x12345678,
            filename="lab_spectrum_sample.png",
        )
        packed = payload.pack()
        assert len(packed) == 13 + len("lab_spectrum_sample.png")

        unpacked = FileStartPayload.unpack(packed)
        assert unpacked.file_size == 1048576
        assert unpacked.chunk_size == 256
        assert unpacked.total_chunks == 4096
        assert unpacked.file_crc32 == 0x12345678
        assert unpacked.filename == "lab_spectrum_sample.png"

    def test_file_start_payload_long_filename_truncation(self) -> None:
        """Verifies filenames longer than 255 bytes are truncated during packing."""
        long_name = "A" * 300
        payload = FileStartPayload(
            file_size=100,
            chunk_size=64,
            total_chunks=2,
            file_crc32=0x9999,
            filename=long_name,
        )
        packed = payload.pack()
        assert len(packed) == 13 + 255

        unpacked = FileStartPayload.unpack(packed)
        assert unpacked.filename == "A" * 255

    def test_file_start_payload_empty_filename(self) -> None:
        """Verifies FileStartPayload with empty string filename."""
        payload = FileStartPayload(
            file_size=0,
            chunk_size=128,
            total_chunks=1,
            file_crc32=0,
            filename="",
        )
        packed = payload.pack()
        assert len(packed) == 13

        unpacked = FileStartPayload.unpack(packed)
        assert unpacked.filename == ""

    def test_file_start_payload_truncated_data_raises_error(self) -> None:
        """Verifies unpacking too short data raises ValueError."""
        with pytest.raises(ValueError, match="Payload too short"):
            FileStartPayload.unpack(b"\x00" * 5)

    def test_file_start_payload_truncated_filename_raises_error(self) -> None:
        """Verifies unpacking where fname_len indicates more bytes than present raises ValueError."""
        # 13 bytes header indicating fname_len = 20, but no trailing filename bytes
        header = b"\x00\x00\x00\x00" + b"\x00\x00" + b"\x00\x00" + b"\x00\x00\x00\x00" + b"\x14"
        with pytest.raises(ValueError, match="Filename truncated"):
            FileStartPayload.unpack(header)

    def test_file_chunk_payload_pack_unpack(self) -> None:
        """Verifies FileChunkPayload packing and unpacking."""
        raw_binary = b"\xde\xad\xbe\xef" * 32
        chunk = FileChunkPayload(chunk_id=42, chunk_data=raw_binary)
        packed = chunk.pack()
        assert len(packed) == 2 + len(raw_binary)

        unpacked = FileChunkPayload.unpack(packed)
        assert unpacked.chunk_id == 42
        assert unpacked.chunk_data == raw_binary

    def test_file_chunk_payload_empty_data(self) -> None:
        """Verifies FileChunkPayload with zero-length data."""
        chunk = FileChunkPayload(chunk_id=0, chunk_data=b"")
        packed = chunk.pack()
        assert len(packed) == 2

        unpacked = FileChunkPayload.unpack(packed)
        assert unpacked.chunk_id == 0
        assert unpacked.chunk_data == b""

    def test_file_chunk_payload_too_short_raises_error(self) -> None:
        """Verifies unpacking fewer than 2 bytes raises ValueError."""
        with pytest.raises(ValueError, match="Data too short"):
            FileChunkPayload.unpack(b"\x00")

    def test_file_end_payload_pack_unpack(self) -> None:
        """Verifies FileEndPayload packing and unpacking."""
        end_payload = FileEndPayload(file_crc32=0xDEADBEEF, total_bytes_sent=204800)
        packed = end_payload.pack()
        assert len(packed) == 8

        unpacked = FileEndPayload.unpack(packed)
        assert unpacked.file_crc32 == 0xDEADBEEF
        assert unpacked.total_bytes_sent == 204800

    def test_file_end_payload_too_short_raises_error(self) -> None:
        """Verifies unpacking fewer than 8 bytes raises ValueError."""
        with pytest.raises(ValueError, match="Data too short"):
            FileEndPayload.unpack(b"\x00" * 7)

    def test_ack_payload_pack_unpack(self) -> None:
        """Verifies AckPayload with various status codes."""
        ack = AckPayload(acked_seq=15, status=AckStatus.OK)
        packed = ack.pack()
        assert len(packed) == 2

        unpacked = AckPayload.unpack(packed)
        assert unpacked.acked_seq == 15
        assert unpacked.status == AckStatus.OK

        ack_crc_err = AckPayload(acked_seq=20, status=AckStatus.CRC_ERROR)
        unpacked_crc = AckPayload.unpack(ack_crc_err.pack())
        assert unpacked_crc.acked_seq == 20
        assert unpacked_crc.status == AckStatus.CRC_ERROR

    def test_ack_payload_too_short_raises_error(self) -> None:
        """Verifies unpacking fewer than 2 bytes raises ValueError."""
        with pytest.raises(ValueError, match="Data too short"):
            AckPayload.unpack(b"\x00")
