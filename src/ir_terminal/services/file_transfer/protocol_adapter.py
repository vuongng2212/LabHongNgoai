"""Maps transfer messages to the existing legacy and large wire payloads."""

from __future__ import annotations

from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.file_payloads import (
    FileChunkPayload,
    FileChunkPayloadLarge,
    FileEndPayload,
    FileEndPayloadLarge,
    FileStartPayload,
    FileStartPayloadLarge,
)
from ir_terminal.protocol.packet import Packet


class ProtocolAdapter:
    """Encodes and decodes file-transfer payloads without owning ARQ state."""

    @staticmethod
    def start_payload(
        *,
        extended: bool,
        file_size: int,
        chunk_size: int,
        total_chunks: int,
        file_crc32: int,
        filename: str,
    ) -> bytes:
        """Packs START metadata using the selected protocol generation."""
        payload_type = FileStartPayloadLarge if extended else FileStartPayload
        return payload_type(
            file_size=file_size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            file_crc32=file_crc32,
            filename=filename,
        ).pack()

    @staticmethod
    def chunk_payload(*, extended: bool, chunk_id: int, data: bytes) -> bytes:
        """Packs one chunk with its legacy or large chunk identifier."""
        payload_type = FileChunkPayloadLarge if extended else FileChunkPayload
        return payload_type(chunk_id=chunk_id, chunk_data=data).pack()

    @staticmethod
    def end_payload(*, extended: bool, file_crc32: int, total_bytes: int) -> bytes:
        """Packs END verification metadata."""
        payload_type = FileEndPayloadLarge if extended else FileEndPayload
        return payload_type(file_crc32=file_crc32, total_bytes_sent=total_bytes).pack()

    @staticmethod
    def decode_start(pkt: Packet) -> FileStartPayload | FileStartPayloadLarge:
        """Decodes a START packet based on its opcode."""
        if pkt.opcode == Opcode.TRANSFER_START_LARGE:
            return FileStartPayloadLarge.unpack(pkt.payload)
        return FileStartPayload.unpack(pkt.payload)

    @staticmethod
    def decode_chunk(pkt: Packet) -> FileChunkPayload | FileChunkPayloadLarge:
        """Decodes a CHUNK packet based on its opcode."""
        if pkt.opcode == Opcode.DATA_CHUNK_LARGE:
            return FileChunkPayloadLarge.unpack(pkt.payload)
        return FileChunkPayload.unpack(pkt.payload)

    @staticmethod
    def decode_end(pkt: Packet) -> FileEndPayload | FileEndPayloadLarge:
        """Decodes an END packet based on its opcode."""
        if pkt.opcode == Opcode.TRANSFER_END_LARGE:
            return FileEndPayloadLarge.unpack(pkt.payload)
        return FileEndPayload.unpack(pkt.payload)
