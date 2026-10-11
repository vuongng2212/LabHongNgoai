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

# Module-level pre-packed constants and Struct objects (refactor: avoid
# re-packing SOF/EOF and re-creating Struct layouts on every frame).
_SOF_BYTES: bytes = struct.pack("<H", SOF_MAGIC)
_HEADER_STRUCT: struct.Struct = struct.Struct("<HBBH")
_FOOTER_STRUCT: struct.Struct = struct.Struct("<IH")


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
    header_fields = _HEADER_STRUCT.pack(SOF_MAGIC, seq, opcode, payload_len)

    # Compute CRC-32 over SEQ through PAYLOAD (excluding SOF delimiter per STM32 spec).
    # Single memoryview avoids the intermediate header+payload concat copy.
    crc_view = memoryview(header_fields)[2:]
    if payload_len:
        crc_val = zlib.crc32(crc_view) & 0xFFFFFFFF
        crc_val = zlib.crc32(payload, crc_val) & 0xFFFFFFFF
    else:
        crc_val = zlib.crc32(crc_view) & 0xFFFFFFFF

    # =========================================================================
    # PACKET FOOTER STRUCTURE (Total: 6 bytes)
    # Offset 0-3: CRC-32 Checksum      -> uint32 (Little-Endian, IEEE 802.3)
    # Offset 4-5: End of Frame (EOF)   -> 0x55AA (uint16, Little-Endian: 0xAA, 0x55)
    # =========================================================================
    footer_bytes = _FOOTER_STRUCT.pack(crc_val, EOF_MAGIC)

    return b"".join((_SOF_BYTES, header_fields[2:], payload, footer_bytes))


def parse_frames_from_buffer(buffer: bytes | bytearray | memoryview) -> tuple[list[Packet], bytes]:
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
    buf_len = len(buffer)
    idx = 0

    # =========================================================================
    # SOF PATTERN (2 bytes)
    # Offset 0-1: Start of Frame -> 0xAA55 (uint16, Little-Endian: 0x55, 0xAA)
    # =========================================================================
    sof_bytes = _SOF_BYTES
    # Single memoryview over the input avoids per-frame slice copies; the only
    # copy is the verified payload and the trailing remaining bytes.
    view = memoryview(buffer) if not isinstance(buffer, memoryview) else buffer

    while idx < buf_len:
        # 1. Search for SOF magic delimiter (0xAA55)
        if isinstance(buffer, (bytes, bytearray)):
            sof_pos = buffer.find(sof_bytes, idx)
        else:
            # memoryview path: manual scan using precomputed SOF bytes
            first = sof_bytes[0]
            second = sof_bytes[1]
            sof_pos = -1
            i = idx
            while i + 1 < buf_len:
                if view[i] == first and view[i + 1] == second:
                    sof_pos = i
                    break
                i += 1
        if sof_pos == -1:
            # No SOF found in remaining buffer; discard noise except last byte
            # (which might be the first byte of a split SOF)
            remaining_len = buf_len - idx
            if remaining_len > 1:
                return packets, bytes(view[buf_len - 1 : buf_len])
            return packets, bytes(view[idx:buf_len])

        # Advance index to the SOF position (discarding any preceding noise bytes)
        idx = sof_pos

        # 2. Check if we have at least the complete 6-byte header
        if buf_len - idx < HEADER_SIZE:
            # Incomplete header; wait for more bytes in next cycle
            return packets, bytes(view[idx:buf_len])

        # =====================================================================
        # 3. PACKET HEADER UNPACK (Total: 6 bytes)
        # Offset 0-1: SOF     -> uint16 (Little-Endian magic)
        # Offset 2  : SEQ     -> uint8 (0..255)
        # Offset 3  : OPCODE  -> uint8 (Command code)
        # Offset 4-5: LEN     -> uint16 (Little-Endian, Payload size)
        # =====================================================================
        _, seq, opcode, payload_len = _HEADER_STRUCT.unpack_from(view, idx)

        # Sanity check payload length to avoid memory denial or parsing garbage
        if payload_len > MAX_PAYLOAD_SIZE:
            # Invalid length; SOF was likely false positive noise; advance past this SOF
            idx += 2
            continue

        # Total expected frame length for this packet
        total_frame_len = HEADER_SIZE + payload_len + FOOTER_SIZE

        # 4. Check if the entire frame has arrived in the buffer
        if buf_len - idx < total_frame_len:
            # Incomplete frame; wait for remaining payload/footer
            return packets, bytes(view[idx:buf_len])

        # 5. Extract components (footer via unpack_from, no slice copy)
        footer_offset = idx + HEADER_SIZE + payload_len

        # =====================================================================
        # PACKET FOOTER UNPACK (Total: 6 bytes)
        # Offset 0-3: CRC-32  -> uint32 (Little-Endian, IEEE 802.3 checksum)
        # Offset 4-5: EOF     -> uint16 (Little-Endian, 0x55AA magic)
        # =====================================================================
        rx_crc, rx_eof = _FOOTER_STRUCT.unpack_from(view, footer_offset)

        # 6. Validate EOF magic delimiter (0x55AA)
        if rx_eof != EOF_MAGIC:
            # EOF delimiter mismatch; corrupted frame; advance past SOF
            idx += 2
            continue

        # 7. Validate CRC-32 Checksum over SEQ through PAYLOAD (excluding SOF per STM32 spec)
        # view[idx + 2 : idx + HEADER_SIZE + payload_len] covers SEQ, OPCODE, LEN, and PAYLOAD.
        # Chained zlib.crc32 over two memoryview segments avoids the slice-copy.
        crc_end = idx + HEADER_SIZE + payload_len
        crc_val = zlib.crc32(view[idx + 2 : idx + HEADER_SIZE])
        if payload_len:
            crc_val = zlib.crc32(view[idx + HEADER_SIZE : crc_end], crc_val)
        expected_crc = crc_val & 0xFFFFFFFF
        if rx_crc != expected_crc:
            # CRC mismatch; frame was corrupted over optical medium
            idx += 2
            continue

        # 8. Frame successfully verified! Single copy of the payload only.
        payload = bytes(view[idx + HEADER_SIZE : crc_end])
        header = Header(sof=SOF_MAGIC, seq=seq, opcode=opcode, length=payload_len)
        packet = Packet(header=header, payload=payload, crc32=rx_crc, eof=EOF_MAGIC)
        packets.append(packet)

        # Advance buffer index past this entire frame
        idx += total_frame_len

    return packets, bytes(view[idx:buf_len])
