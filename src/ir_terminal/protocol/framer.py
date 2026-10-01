"""Binary frame packing, serialization, and robust stream parsing with CRC-32."""

from __future__ import annotations

import struct
import zlib

from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    FOOTER_SIZE,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    SOF_MAGIC,
)
from ir_terminal.protocol.packet import Header, Packet


class FrameCorruptedError(Exception):
    """Raised when frame CRC-32 mismatch or structural corruption is detected."""


def compute_crc32(data: bytes) -> int:
    """Computes standard IEEE 802.3 32-bit CRC checksum.

    Args:
        data: Byte sequence to hash.

    Returns:
        32-bit unsigned integer CRC value.
    """
    return zlib.crc32(data) & 0xFFFFFFFF


def pack_frame(seq: int, opcode: int, payload: bytes = b"") -> bytes:
    """Packs header, payload, and footer into a serialized binary frame.

    Frame Geometry:
        [SOF: 2B] [SEQ: 1B] [OPCODE: 1B] [LEN: 2B] [PAYLOAD: NB] [CRC32: 4B] [EOF: 2B]
        Endianness: Little-Endian for numeric fields.

    Args:
        seq: Sequence number (0..255).
        opcode: Operation command code (uint8).
        payload: Binary payload bytes (max 512 bytes).

    Returns:
        Fully serialized byte sequence ready for UART transmission.

    Raises:
        ValueError: If payload length exceeds MAX_PAYLOAD_SIZE or seq exceeds 255.
    """
    payload_len = len(payload)
    if payload_len > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload size {payload_len} exceeds max allowed {MAX_PAYLOAD_SIZE} bytes")
    if not (0 <= seq <= 255):
        raise ValueError(f"Sequence number {seq} must be in range 0..255")
    if not (0 <= opcode <= 255):
        raise ValueError(f"Opcode {opcode} must be in range 0..255")

    # =========================================================================
    # PACKET HEADER STRUCTURE (Total: 6 bytes)
    # Offset 0-1: Start of Frame (SOF) -> 0xAA55 (uint16, Little-Endian: 0x55, 0xAA)
    # Offset 2  : Sequence Number      -> 0x00..0xFF (uint8)
    # Offset 3  : Opcode / Command     -> uint8
    # Offset 4-5: Payload Length (N)   -> uint16 (Little-Endian, bytes)
    # =========================================================================
    header_bytes = struct.pack("<HBBH", SOF_MAGIC, seq, opcode, payload_len)

    # Compute CRC-32 over Header + Payload
    body_for_crc = header_bytes + payload
    crc_val = compute_crc32(body_for_crc)

    # =========================================================================
    # PACKET FOOTER STRUCTURE (Total: 6 bytes)
    # Offset 0-3: CRC-32 Checksum      -> uint32 (Little-Endian, IEEE 802.3)
    # Offset 4-5: End of Frame (EOF)   -> 0x55AA (uint16, Little-Endian: 0xAA, 0x55)
    # =========================================================================
    footer_bytes = struct.pack("<IH", crc_val, EOF_MAGIC)

    return header_bytes + payload + footer_bytes


def parse_frames_from_buffer(buffer: bytes) -> tuple[list[Packet], bytes]:
    """Extracts all valid frames from an accumulated byte buffer.

    Scans the buffer for the SOF magic delimiter, extracts the packet length,
    checks if the full frame has arrived, validates the CRC-32 and EOF delimiter,
    and discards corrupted noise bytes preceding the frame.

    Args:
        buffer: Accumulated raw byte stream from UART receiver.

    Returns:
        A tuple containing:
            - List of successfully parsed and verified Packet instances.
            - Remaining unprocessed bytes to retain for the next read cycle.
    """
    packets: list[Packet] = []
    idx = 0

    # =========================================================================
    # SOF PATTERN (2 bytes)
    # Offset 0-1: Start of Frame -> 0xAA55 (uint16, Little-Endian: 0x55, 0xAA)
    # =========================================================================
    sof_bytes = struct.pack("<H", SOF_MAGIC)

    while idx < len(buffer):
        # 1. Search for SOF magic delimiter (0xAA55)
        sof_pos = buffer.find(sof_bytes, idx)
        if sof_pos == -1:
            # No SOF found in remaining buffer; discard noise except last byte
            # (which might be the first byte of a split SOF)
            remaining_len = len(buffer) - idx
            if remaining_len > 1:
                return packets, buffer[-1:]
            return packets, buffer[idx:]

        # Advance index to the SOF position (discarding any preceding noise bytes)
        idx = sof_pos

        # 2. Check if we have at least the complete 6-byte header
        if len(buffer) - idx < HEADER_SIZE:
            # Incomplete header; wait for more bytes in next cycle
            return packets, buffer[idx:]

        # =====================================================================
        # 3. PACKET HEADER UNPACK (Total: 6 bytes)
        # Offset 0-1: SOF     -> uint16 (Little-Endian magic)
        # Offset 2  : SEQ     -> uint8 (0..255)
        # Offset 3  : OPCODE  -> uint8 (Command code)
        # Offset 4-5: LEN     -> uint16 (Little-Endian, Payload size)
        # =====================================================================
        _, seq, opcode, payload_len = struct.unpack("<HBBH", buffer[idx : idx + HEADER_SIZE])

        # Sanity check payload length to avoid memory denial or parsing garbage
        if payload_len > MAX_PAYLOAD_SIZE:
            # Invalid length; SOF was likely false positive noise; advance past this SOF
            idx += 2
            continue

        # Total expected frame length for this packet
        total_frame_len = HEADER_SIZE + payload_len + FOOTER_SIZE

        # 4. Check if the entire frame has arrived in the buffer
        if len(buffer) - idx < total_frame_len:
            # Incomplete frame; wait for remaining payload/footer
            return packets, buffer[idx:]

        # 5. Extract components
        header_raw = buffer[idx : idx + HEADER_SIZE]
        payload = buffer[idx + HEADER_SIZE : idx + HEADER_SIZE + payload_len]
        footer_offset = idx + HEADER_SIZE + payload_len
        footer_raw = buffer[footer_offset : footer_offset + FOOTER_SIZE]

        # =====================================================================
        # PACKET FOOTER UNPACK (Total: 6 bytes)
        # Offset 0-3: CRC-32  -> uint32 (Little-Endian, IEEE 802.3 checksum)
        # Offset 4-5: EOF     -> uint16 (Little-Endian, 0x55AA magic)
        # =====================================================================
        rx_crc, rx_eof = struct.unpack("<IH", footer_raw)

        # 6. Validate EOF magic delimiter (0x55AA)
        if rx_eof != EOF_MAGIC:
            # EOF delimiter mismatch; corrupted frame; advance past SOF
            idx += 2
            continue

        # 7. Validate CRC-32 Checksum over Header + Payload
        expected_crc = compute_crc32(header_raw + payload)
        if rx_crc != expected_crc:
            # CRC mismatch; frame was corrupted over optical medium
            idx += 2
            continue

        # 8. Frame successfully verified!
        header = Header(sof=SOF_MAGIC, seq=seq, opcode=opcode, length=payload_len)
        packet = Packet(header=header, payload=payload, crc32=rx_crc, eof=EOF_MAGIC)
        packets.append(packet)

        # Advance buffer index past this entire frame
        idx += total_frame_len

    return packets, buffer[idx:]
