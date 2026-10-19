"""Unit tests for file-transfer protocol and streaming primitives."""

from __future__ import annotations

import zlib
from pathlib import Path

from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.file_payloads import (
    FileChunkPayload,
    FileChunkPayloadLarge,
    FileEndPayload,
    FileEndPayloadLarge,
    FileStartPayload,
    FileStartPayloadLarge,
)
from ir_terminal.protocol.packet import Header, Packet
from ir_terminal.services.file_transfer.chunk_buffer import ChunkBuffer
from ir_terminal.services.file_transfer.integrity_verifier import IntegrityVerifier
from ir_terminal.services.file_transfer.protocol_adapter import ProtocolAdapter


def test_protocol_adapter_encodes_both_transfer_generations() -> None:
    """Legacy and large encodings remain byte-identical to payload serializers."""
    assert (
        ProtocolAdapter.start_payload(
            extended=False,
            file_size=1234,
            chunk_size=240,
            total_chunks=6,
            file_crc32=0x12345678,
            filename="sample.bin",
        )
        == FileStartPayload(
            file_size=1234,
            chunk_size=240,
            total_chunks=6,
            file_crc32=0x12345678,
            filename="sample.bin",
        ).pack()
    )
    assert (
        ProtocolAdapter.start_payload(
            extended=True,
            file_size=1234,
            chunk_size=240,
            total_chunks=6,
            file_crc32=0x12345678,
            filename="sample.bin",
        )
        == FileStartPayloadLarge(
            file_size=1234,
            chunk_size=240,
            total_chunks=6,
            file_crc32=0x12345678,
            filename="sample.bin",
        ).pack()
    )
    assert ProtocolAdapter.chunk_payload(extended=False, chunk_id=4, data=b"abc") == (
        FileChunkPayload(4, b"abc").pack()
    )
    assert ProtocolAdapter.chunk_payload(extended=True, chunk_id=4, data=b"abc") == (
        FileChunkPayloadLarge(4, b"abc").pack()
    )
    assert (
        ProtocolAdapter.end_payload(extended=False, file_crc32=12, total_bytes=1234)
        == FileEndPayload(12, 1234).pack()
    )
    assert (
        ProtocolAdapter.end_payload(extended=True, file_crc32=12, total_bytes=1234)
        == FileEndPayloadLarge(12, 1234).pack()
    )


def test_protocol_adapter_decodes_by_opcode() -> None:
    """Decoded payload classes follow the opcode's legacy/large format."""
    pkt_start = _packet(1, Opcode.TRANSFER_START, FileStartPayload(1, 1, 1, 0, "x").pack())
    pkt_start_large = _packet(
        1, Opcode.TRANSFER_START_LARGE, FileStartPayloadLarge(1, 1, 1, 0, "x").pack()
    )
    pkt_chunk = _packet(2, Opcode.DATA_CHUNK, FileChunkPayload(0, b"x").pack())
    pkt_chunk_large = _packet(2, Opcode.DATA_CHUNK_LARGE, FileChunkPayloadLarge(0, b"x").pack())
    pkt_end = _packet(3, Opcode.TRANSFER_END, FileEndPayload(0, 1).pack())
    pkt_end_large = _packet(3, Opcode.TRANSFER_END_LARGE, FileEndPayloadLarge(0, 1).pack())

    assert isinstance(ProtocolAdapter.decode_start(pkt_start), FileStartPayload)
    assert isinstance(ProtocolAdapter.decode_start(pkt_start_large), FileStartPayloadLarge)
    assert isinstance(ProtocolAdapter.decode_chunk(pkt_chunk), FileChunkPayload)
    assert isinstance(ProtocolAdapter.decode_chunk(pkt_chunk_large), FileChunkPayloadLarge)
    assert isinstance(ProtocolAdapter.decode_end(pkt_end), FileEndPayload)
    assert isinstance(ProtocolAdapter.decode_end(pkt_end_large), FileEndPayloadLarge)


def _packet(seq: int, opcode: int, payload: bytes) -> Packet:
    """Builds a payload packet for adapter unit tests."""
    return Packet(Header(0, seq, opcode, len(payload)), payload, 0, 0)


def test_integrity_verifier_streams_and_compares_crc(tmp_path: Path) -> None:
    """CRC helper handles streaming reads and normalized expected values."""
    payload = b"streamed integrity" * 1000
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)

    crc = IntegrityVerifier.file_crc32(path, buffer_size=37)
    assert crc == zlib.crc32(payload) & 0xFFFFFFFF
    assert IntegrityVerifier.update_crc32(0, payload) == crc
    assert IntegrityVerifier.matches((crc, crc | 0x1_0000_0000), crc)
    assert not IntegrityVerifier.matches((crc ^ 1,), crc)


def test_chunk_buffer_stages_appends_and_publishes_safely(tmp_path: Path) -> None:
    """RX staging remains bounded, flushes chunks, and strips path components."""
    handle, temporary = ChunkBuffer.create_staging_file(tmp_path)
    ChunkBuffer.append(handle, b"first")
    assert temporary.read_bytes() == b"first"
    ChunkBuffer.append(handle, b" second")
    handle.close()

    published = ChunkBuffer.publish(temporary, tmp_path, "../result.bin")
    assert published == tmp_path / "result.bin"
    assert published.read_bytes() == b"first second"

    collision_handle, collision_temp = ChunkBuffer.create_staging_file(tmp_path)
    collision_handle.write(b"replacement")
    collision_handle.close()
    collision = ChunkBuffer.publish(collision_temp, tmp_path, "result.bin")
    assert collision != published
    assert published.read_bytes() == b"first second"
