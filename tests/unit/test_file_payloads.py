"""Unit tests for binary file transfer and ACK payload serialization."""

from __future__ import annotations

import pytest

from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileChunkPayload,
    FileEndPayload,
    FileStartPayload,
)


class TestFilePayloads:
    """Test suite for binary struct packing and unpacking."""

    def test_file_start_payload_pack_unpack(self) -> None:
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

    def test_file_start_payload_truncated_data_raises_error(self) -> None:
        with pytest.raises(ValueError, match="Payload too short"):
            FileStartPayload.unpack(b"\x00" * 5)

    def test_file_chunk_payload_pack_unpack(self) -> None:
        raw_binary = b"\xde\xad\xbe\xef" * 32
        chunk = FileChunkPayload(chunk_id=42, chunk_data=raw_binary)
        packed = chunk.pack()
        assert len(packed) == 2 + len(raw_binary)

        unpacked = FileChunkPayload.unpack(packed)
        assert unpacked.chunk_id == 42
        assert unpacked.chunk_data == raw_binary

    def test_file_end_payload_pack_unpack(self) -> None:
        end_payload = FileEndPayload(file_crc32=0xDEADBEEF, total_bytes_sent=204800)
        packed = end_payload.pack()
        assert len(packed) == 8

        unpacked = FileEndPayload.unpack(packed)
        assert unpacked.file_crc32 == 0xDEADBEEF
        assert unpacked.total_bytes_sent == 204800

    def test_ack_payload_pack_unpack(self) -> None:
        ack = AckPayload(acked_seq=15, status=0)
        packed = ack.pack()
        assert len(packed) == 2

        unpacked = AckPayload.unpack(packed)
        assert unpacked.acked_seq == 15
        assert unpacked.status == 0
