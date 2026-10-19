"""Streaming file integrity operations used by transfer managers."""

from __future__ import annotations

import zlib
from pathlib import Path

CRC_STREAM_BUFFER_SIZE = 1024 * 1024


class IntegrityVerifier:
    """Computes and compares CRC-32 values without loading entire files."""

    @staticmethod
    def file_crc32(path: Path, buffer_size: int = CRC_STREAM_BUFFER_SIZE) -> int:
        """Computes a whole-file CRC-32 using a bounded-memory read."""
        crc = 0
        with path.open("rb") as handle:
            while block := handle.read(buffer_size):
                crc = zlib.crc32(block, crc)
        return crc & 0xFFFFFFFF

    @staticmethod
    def update_crc32(crc: int, data: bytes) -> int:
        """Returns the incremental CRC-32 after adding one data block."""
        return zlib.crc32(data, crc) & 0xFFFFFFFF

    @staticmethod
    def matches(expected_values: tuple[int, ...], actual: int) -> bool:
        """Returns whether all supplied CRC values match the calculated value."""
        normalized = actual & 0xFFFFFFFF
        return all((value & 0xFFFFFFFF) == normalized for value in expected_values)
