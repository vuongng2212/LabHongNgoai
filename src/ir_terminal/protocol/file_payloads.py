"""Binary payload definitions for optical file transfer and flow control.

All multi-byte integer fields strictly follow Little-Endian byte ordering.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from ir_terminal.protocol.constants import AckStatus
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)


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
        """Packs metadata into binary bytes.

        Returns:
            Serialized binary bytes including fixed 13-byte header and UTF-8 filename.
        """
        fname_bytes = self.filename.encode("utf-8")
        if len(fname_bytes) > 255:
            logger.warning(
                "%s FileStart filename truncated: %dB->255B name=%r",
                LogTag.FILE_SVC,
                len(fname_bytes),
                self.filename,
            )
            fname_bytes = fname_bytes[:255]

        # =========================================================================
        # FILE_START METADATA STRUCTURE (Total: 13 bytes header)
        # Offset 0-3  : file_size    -> uint32 (Little-Endian, bytes)
        # Offset 4-5  : chunk_size   -> uint16 (Little-Endian, bytes)
        # Offset 6-7  : total_chunks -> uint16 (Little-Endian, count)
        # Offset 8-11 : file_crc32   -> uint32 (Little-Endian, IEEE 802.3 CRC-32)
        # Offset 12   : fname_len    -> uint8  (filename length in bytes)
        # =========================================================================
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
        """Unpacks binary bytes into FileStartPayload instance.

        Args:
            data: Binary payload bytes to deserialize.

        Returns:
            Deserialized FileStartPayload instance.

        Raises:
            ValueError: If data is shorter than 13 bytes or filename is truncated.
        """
        if len(data) < 13:
            logger.error(
                "%s FileStartPayload unpack FAILED: got=%dB need>=13B hex=%s",
                LogTag.FILE_SVC,
                len(data),
                format_hex_preview(data),
            )
            raise ValueError(f"Payload too short for FileStartPayload: {len(data)} < 13 bytes")

        # =========================================================================
        # FILE_START METADATA UNPACK (Total: 13 bytes header)
        # Offset 0-3  : file_size    -> uint32 (Little-Endian, bytes)
        # Offset 4-5  : chunk_size   -> uint16 (Little-Endian, bytes)
        # Offset 6-7  : total_chunks -> uint16 (Little-Endian, count)
        # Offset 8-11 : file_crc32   -> uint32 (Little-Endian, IEEE 802.3 CRC-32)
        # Offset 12   : fname_len    -> uint8  (filename length in bytes)
        # =========================================================================
        file_size, chunk_size, total_chunks, file_crc32, fname_len = struct.unpack(
            "<IHHIB", data[:13]
        )
        if len(data) < 13 + fname_len:
            logger.error(
                "%s FileStartPayload filename truncated: declared=%dB got=%dB hex=%s",
                LogTag.FILE_SVC,
                fname_len,
                len(data) - 13,
                format_hex_preview(data),
            )
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
        """Packs chunk ID and binary data.

        Returns:
            Serialized 2-byte chunk ID prefix followed by raw payload bytes.
        """
        # =========================================================================
        # FILE_CHUNK HEADER STRUCTURE (Total: 2 bytes prefix)
        # Offset 0-1: chunk_id -> uint16 (Little-Endian, 0-based chunk index)
        # =========================================================================
        prefix = struct.pack("<H", self.chunk_id)
        return prefix + self.chunk_data

    @classmethod
    def unpack(cls, data: bytes) -> FileChunkPayload:
        """Unpacks binary bytes into FileChunkPayload instance.

        Args:
            data: Binary chunk bytes including 2-byte prefix.

        Returns:
            Deserialized FileChunkPayload instance.

        Raises:
            ValueError: If data length is less than 2 bytes.
        """
        if len(data) < 2:
            logger.error(
                "%s FileChunkPayload unpack FAILED: got=%dB need>=2B",
                LogTag.ARQ_ENGINE,
                len(data),
            )
            raise ValueError("Data too short for FileChunkPayload")
        # =========================================================================
        # FILE_CHUNK HEADER UNPACK (Total: 2 bytes prefix)
        # Offset 0-1: chunk_id -> uint16 (Little-Endian, 0-based chunk index)
        # =========================================================================
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
        """Packs completion metadata.

        Returns:
            Serialized 8-byte binary completion frame.
        """
        # =========================================================================
        # FILE_END STRUCTURE (Total: 8 bytes)
        # Offset 0-3: file_crc32       -> uint32 (Little-Endian, IEEE 802.3 CRC-32)
        # Offset 4-7: total_bytes_sent -> uint32 (Little-Endian, total payload bytes)
        # =========================================================================
        return struct.pack("<II", self.file_crc32, self.total_bytes_sent)

    @classmethod
    def unpack(cls, data: bytes) -> FileEndPayload:
        """Unpacks completion metadata.

        Args:
            data: Binary payload bytes to deserialize.

        Returns:
            Deserialized FileEndPayload instance.

        Raises:
            ValueError: If data length is less than 8 bytes.
        """
        if len(data) < 8:
            logger.error(
                "%s FileEndPayload unpack FAILED: got=%dB need>=8B hex=%s",
                LogTag.FILE_SVC,
                len(data),
                format_hex_preview(data),
            )
            raise ValueError("Data too short for FileEndPayload")
        # =========================================================================
        # FILE_END UNPACK (Total: 8 bytes)
        # Offset 0-3: file_crc32       -> uint32 (Little-Endian, IEEE 802.3 CRC-32)
        # Offset 4-7: total_bytes_sent -> uint32 (Little-Endian, total payload bytes)
        # =========================================================================
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
    status: int = AckStatus.OK  # 0: OK, 1: CRC_ERROR, 2: OUT_OF_ORDER

    def pack(self) -> bytes:
        """Packs ACK payload.

        Returns:
            Serialized 2-byte ACK payload.
        """
        # =========================================================================
        # ACK/NACK PAYLOAD STRUCTURE (Total: 2 bytes)
        # Offset 0: acked_seq -> uint8 (0..255 sequence number being acknowledged)
        # Offset 1: status    -> uint8 (0=OK, 1=CRC_ERROR, 2=OUT_OF_ORDER, 3=TIMEOUT)
        # =========================================================================
        return struct.pack("<BB", self.acked_seq, self.status)

    @classmethod
    def unpack(cls, data: bytes) -> AckPayload:
        """Unpacks ACK payload.

        Args:
            data: Binary payload bytes to deserialize.

        Returns:
            Deserialized AckPayload instance.

        Raises:
            ValueError: If data length is less than 2 bytes.
        """
        if len(data) < 2:
            logger.error(
                "%s AckPayload unpack FAILED: got=%dB need>=2B hex=%s",
                LogTag.ARQ_ENGINE,
                len(data),
                format_hex_preview(data),
            )
            raise ValueError("Data too short for AckPayload")
        # =========================================================================
        # ACK/NACK PAYLOAD UNPACK (Total: 2 bytes)
        # Offset 0: acked_seq -> uint8 (0..255 sequence number being acknowledged)
        # Offset 1: status    -> uint8 (0=OK, 1=CRC_ERROR, 2=OUT_OF_ORDER, 3=TIMEOUT)
        # =========================================================================
        acked_seq, status = struct.unpack("<BB", data[:2])
        return cls(acked_seq=acked_seq, status=status)
