"""Binary payload definitions for optical file transfer and flow control.

All multi-byte integer fields strictly follow Little-Endian byte ordering.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class FileStartPayload:
    """File transfer initialization metadata payload.

    Binary Layout:
    +---------------+-----------+--------+---------------+----------------------+
    | Byte Offset   | Field Name| Type   | Endianness    | Description          |
    +---------------+-----------+--------+---------------+----------------------+
    | 0x00 - 0x03   | file_size | uint32 | Little-Endian | Total file size (B)  |
    | 0x04 - 0x05   | chunk_size| uint16 | Little-Endian | Chunk payload size   |
    | 0x06 - 0x07   | total_chnk| uint16 | Little-Endian | Total chunk count    |
    | 0x08 - 0x0B   | file_crc32| uint32 | Little-Endian | Whole-file CRC-32    |
    | 0x0C          | fname_len | uint8  | -             | Length of filename   |
    | 0x0D - 0x0D+N | filename  | bytes  | UTF-8 string  | File name string     |
    +---------------+-----------+--------+---------------+----------------------+
    """

    file_size: int
    chunk_size: int
    total_chunks: int
    file_crc32: int
    filename: str

    def pack(self) -> bytes:
        """Packs metadata into binary bytes."""
        fname_bytes = self.filename.encode("utf-8")
        if len(fname_bytes) > 255:
            fname_bytes = fname_bytes[:255]

        # Format: < (Little-Endian) I (uint32) H (uint16) H (uint16) I (uint32) B (uint8)
        header_bytes = struct.pack(
            "<IHHIB",
            self.file_size,
            self.chunk_size,
            self.total_chunks,
            self.file_crc32,
            len(fname_bytes),
        )
        return header_bytes + fname_bytes

    @classmethod
    def unpack(cls, data: bytes) -> FileStartPayload:
        """Unpacks binary bytes into FileStartPayload instance."""
        if len(data) < 13:
            raise ValueError(f"Payload too short for FileStartPayload: {len(data)} < 13 bytes")

        file_size, chunk_size, total_chunks, file_crc32, fname_len = struct.unpack(
            "<IHHIB", data[:13]
        )
        if len(data) < 13 + fname_len:
            raise ValueError(f"Filename truncated: expected {fname_len} bytes")

        fname = data[13 : 13 + fname_len].decode("utf-8", errors="replace")
        return cls(
            file_size=file_size,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            file_crc32=file_crc32,
            filename=fname,
        )


@dataclass(frozen=True)
class FileChunkPayload:
    """Indexed binary chunk payload for file streaming.

    Binary Layout:
    +---------------+-----------+--------+---------------+----------------------+
    | Byte Offset   | Field Name| Type   | Endianness    | Description          |
    +---------------+-----------+--------+---------------+----------------------+
    | 0x00 - 0x01   | chunk_id  | uint16 | Little-Endian | Zero-based chunk ID  |
    | 0x02 - end    | chunk_data| bytes  | Raw Bytes     | Binary payload       |
    +---------------+-----------+--------+---------------+----------------------+
    """

    chunk_id: int
    chunk_data: bytes

    def pack(self) -> bytes:
        """Packs chunk ID and binary data."""
        # Format: < (Little-Endian) H (uint16)
        prefix = struct.pack("<H", self.chunk_id)
        return prefix + self.chunk_data

    @classmethod
    def unpack(cls, data: bytes) -> FileChunkPayload:
        """Unpacks binary bytes into FileChunkPayload instance."""
        if len(data) < 2:
            raise ValueError("Data too short for FileChunkPayload")
        (chunk_id,) = struct.unpack("<H", data[:2])
        return cls(chunk_id=chunk_id, chunk_data=data[2:])


@dataclass(frozen=True)
class FileEndPayload:
    """File transfer completion payload.

    Binary Layout:
    +---------------+-----------+--------+---------------+----------------------+
    | Byte Offset   | Field Name| Type   | Endianness    | Description          |
    +---------------+-----------+--------+---------------+----------------------+
    | 0x00 - 0x03   | file_crc32| uint32 | Little-Endian | Whole-file CRC-32    |
    | 0x04 - 0x07   | bytes_sent| uint32 | Little-Endian | Total bytes sent     |
    +---------------+-----------+--------+---------------+----------------------+
    """

    file_crc32: int
    total_bytes_sent: int

    def pack(self) -> bytes:
        """Packs completion metadata."""
        return struct.pack("<II", self.file_crc32, self.total_bytes_sent)

    @classmethod
    def unpack(cls, data: bytes) -> FileEndPayload:
        """Unpacks completion metadata."""
        if len(data) < 8:
            raise ValueError("Data too short for FileEndPayload")
        file_crc32, bytes_sent = struct.unpack("<II", data[:8])
        return cls(file_crc32=file_crc32, total_bytes_sent=bytes_sent)


@dataclass(frozen=True)
class AckPayload:
    """Acknowledgment payload for Stop-and-Wait ARQ flow control.

    Binary Layout:
    +---------------+-----------+--------+---------------+----------------------+
    | Byte Offset   | Field Name| Type   | Endianness    | Description          |
    +---------------+-----------+--------+---------------+----------------------+
    | 0x00          | acked_seq | uint8  | -             | Packet sequence num  |
    | 0x01          | status    | uint8  | -             | 0=OK, 1=CRC, 2=OOD   |
    +---------------+-----------+--------+---------------+----------------------+
    """

    acked_seq: int
    status: int = 0  # 0: OK, 1: CRC_ERROR, 2: OUT_OF_ORDER

    def pack(self) -> bytes:
        """Packs ACK payload."""
        return struct.pack("<BB", self.acked_seq, self.status)

    @classmethod
    def unpack(cls, data: bytes) -> AckPayload:
        """Unpacks ACK payload."""
        if len(data) < 2:
            raise ValueError("Data too short for AckPayload")
        acked_seq, status = struct.unpack("<BB", data[:2])
        return cls(acked_seq=acked_seq, status=status)
