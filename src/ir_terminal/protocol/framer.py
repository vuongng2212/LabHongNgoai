"""Binary frame packing, serialization, and robust stream parsing with CRC-32."""

from __future__ import annotations

import logging
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
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview, opcode_name

logger = logging.getLogger(__name__)

_SOF_BYTES: bytes = struct.pack("<H", SOF_MAGIC)
_HEADER_STRUCT: struct.Struct = struct.Struct("<HBBH")
_FOOTER_STRUCT: struct.Struct = struct.Struct("<IH")

FRAME_PARSE_OK: str = "ok"
FRAME_PARSE_INCOMPLETE: str = "incomplete"
FRAME_PARSE_INVALID: str = "invalid"


class FrameCorruptedError(Exception):
    """Raised when frame CRC-32 mismatch or structural corruption is detected."""


class FramerStats:
    """Cumulative stream-parse diagnostics (loss visibility for optical link)."""

    def __init__(self) -> None:
        """Initializes zeroed parse counters."""
        self.crc_errors: int = 0
        self.eof_errors: int = 0
        self.len_errors: int = 0
        self.noise_bytes: int = 0
        self.frames_ok: int = 0
        self.control_lines: int = 0
        self.control_bytes: int = 0

    def snapshot(self) -> dict[str, int]:
        """Returns a copy of current counters."""
        return {
            "crc_errors": self.crc_errors,
            "eof_errors": self.eof_errors,
            "len_errors": self.len_errors,
            "noise_bytes": self.noise_bytes,
            "frames_ok": self.frames_ok,
            "control_lines": self.control_lines,
            "control_bytes": self.control_bytes,
        }


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
        logger.error(
            "%s pack_frame rejected: payload %dB exceeds MAX %dB | %s",
            LogTag.PROTO_FRAME,
            payload_len,
            MAX_PAYLOAD_SIZE,
            format_frame_summary(seq, opcode, payload_len),
        )
        raise ValueError(f"Payload size {payload_len} exceeds max allowed {MAX_PAYLOAD_SIZE} bytes")
    if not (0 <= seq <= 255):
        logger.error(
            "%s pack_frame rejected: seq %d out of range 0..255 | opcode=%s len=%dB",
            LogTag.PROTO_FRAME,
            seq,
            opcode_name(opcode),
            payload_len,
        )
        raise ValueError(f"Sequence number {seq} must be in range 0..255")
    if not (0 <= opcode <= 255):
        logger.error(
            "%s pack_frame rejected: opcode %d out of range 0..255 | seq=%d len=%dB",
            LogTag.PROTO_FRAME,
            opcode,
            seq,
            payload_len,
        )
        raise ValueError(f"Opcode {opcode} must be in range 0..255")

    header_fields = _HEADER_STRUCT.pack(SOF_MAGIC, seq, opcode, payload_len)

    crc_view = memoryview(header_fields)[2:]
    if payload_len:
        crc_val = zlib.crc32(crc_view) & 0xFFFFFFFF
        crc_val = zlib.crc32(payload, crc_val) & 0xFFFFFFFF
    else:
        crc_val = zlib.crc32(crc_view) & 0xFFFFFFFF

    footer_bytes = _FOOTER_STRUCT.pack(crc_val, EOF_MAGIC)

    frame = b"".join((_SOF_BYTES, header_fields[2:], payload, footer_bytes))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "%s TX pack %s frame_len=%dB hex=%s",
            LogTag.PROTO_FRAME,
            format_frame_summary(seq, opcode, payload_len, crc_val),
            len(frame),
            format_hex_preview(frame),
        )
    return frame


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
    packets, _remaining, _stats = parse_frames_with_stats(buffer)
    return packets, _remaining


def parse_frame_at(
    view: memoryview,
    idx: int,
    stats: FramerStats,
) -> tuple[Packet | None, int, str]:
    """Parses one candidate binary frame starting at ``idx``.

    Args:
        view: Buffer view containing the candidate frame.
        idx: Offset where the SOF magic is expected to begin.
        stats: Accumulator updated in place for structural errors.

    Returns:
        Tuple of ``(packet, next_offset, status)``. ``status`` is
        :data:`FRAME_PARSE_INCOMPLETE` when more bytes are required,
        :data:`FRAME_PARSE_INVALID` when the candidate was rejected, and
        :data:`FRAME_PARSE_OK` when a verified packet was produced.
    """
    buf_len = len(view)
    if buf_len - idx < HEADER_SIZE:
        return None, idx, FRAME_PARSE_INCOMPLETE

    _, seq, opcode, payload_len = _HEADER_STRUCT.unpack_from(view, idx)

    if payload_len > MAX_PAYLOAD_SIZE:
        stats.len_errors += 1
        logger.warning(
            "%s RX len_error @offset=%d: LEN=%d exceeds MAX=%d "
            "(probable false SOF) | %s hex=%s",
            LogTag.PROTO_FRAME,
            idx,
            payload_len,
            MAX_PAYLOAD_SIZE,
            format_frame_summary(seq, opcode, payload_len),
            format_hex_preview(bytes(view[idx : min(idx + HEADER_SIZE + 8, buf_len)])),
        )
        return None, idx + 2, FRAME_PARSE_INVALID

    total_frame_len = HEADER_SIZE + payload_len + FOOTER_SIZE

    if buf_len - idx < total_frame_len:
        return None, idx, FRAME_PARSE_INCOMPLETE

    footer_offset = idx + HEADER_SIZE + payload_len

    rx_crc, rx_eof = _FOOTER_STRUCT.unpack_from(view, footer_offset)

    if rx_eof != EOF_MAGIC:
        stats.eof_errors += 1
        logger.warning(
            "%s RX eof_error @offset=%d: got=0x%04X expected=0x%04X | %s",
            LogTag.PROTO_FRAME,
            footer_offset,
            rx_eof,
            EOF_MAGIC,
            format_frame_summary(seq, opcode, payload_len, rx_crc),
        )
        return None, idx + 2, FRAME_PARSE_INVALID

    crc_end = idx + HEADER_SIZE + payload_len
    crc_val = zlib.crc32(view[idx + 2 : idx + HEADER_SIZE])
    if payload_len:
        crc_val = zlib.crc32(view[idx + HEADER_SIZE : crc_end], crc_val)
    expected_crc = crc_val & 0xFFFFFFFF
    if rx_crc != expected_crc:
        stats.crc_errors += 1
        logger.warning(
            "%s RX crc_error @offset=%d: rx=0x%08X expected=0x%08X | %s hex=%s",
            LogTag.PROTO_FRAME,
            idx,
            rx_crc,
            expected_crc,
            format_frame_summary(seq, opcode, payload_len, rx_crc),
            format_hex_preview(bytes(view[idx : min(idx + total_frame_len, buf_len)])),
        )
        return None, idx + 2, FRAME_PARSE_INVALID

    payload = bytes(view[idx + HEADER_SIZE : crc_end])
    header = Header(sof=SOF_MAGIC, seq=seq, opcode=opcode, length=payload_len)
    packet = Packet(header=header, payload=payload, crc32=rx_crc, eof=EOF_MAGIC)
    stats.frames_ok += 1
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "%s RX ok @offset=%d %s frame_len=%dB payload_hex=%s",
            LogTag.PROTO_FRAME,
            idx,
            format_frame_summary(seq, opcode, payload_len, rx_crc),
            total_frame_len,
            format_hex_preview(payload, max_bytes=16),
        )
    return packet, idx + total_frame_len, FRAME_PARSE_OK


def parse_frames_with_stats(
    buffer: bytes | bytearray | memoryview,
    stats: FramerStats | None = None,
) -> tuple[list[Packet], bytes, FramerStats]:
    """Parses frames like parse_frames_from_buffer while counting errors.

    Args:
        buffer: Accumulated raw byte stream from UART receiver.
        stats: Optional accumulator to update in place. A new one is created
            when omitted.

    Returns:
        Tuple of (packets, remaining bytes, stats).
    """
    packets: list[Packet] = []
    buf_len = len(buffer)
    idx = 0
    own_stats = stats if stats is not None else FramerStats()

    sof_bytes = _SOF_BYTES
    view = memoryview(buffer) if not isinstance(buffer, memoryview) else buffer

    def _tail(rem_idx: int) -> tuple[list[Packet], bytes, FramerStats]:
        return packets, bytes(view[rem_idx:buf_len]), own_stats

    while idx < buf_len:
        if isinstance(buffer, (bytes, bytearray)):
            sof_pos = buffer.find(sof_bytes, idx)
        else:
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
            remaining_len = buf_len - idx
            if remaining_len > 1:
                own_stats.noise_bytes += remaining_len - 1
                return packets, bytes(view[buf_len - 1 : buf_len]), own_stats
            return packets, bytes(view[idx:buf_len]), own_stats

        if sof_pos > idx:
            own_stats.noise_bytes += sof_pos - idx
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s RX skipped %d noise byte(s) before SOF @offset=%d hex=%s",
                    LogTag.PROTO_FRAME,
                    sof_pos - idx,
                    idx,
                    format_hex_preview(bytes(view[idx : min(sof_pos, idx + 16)])),
                )
        idx = sof_pos

        packet, next_idx, status = parse_frame_at(view, idx, own_stats)
        if status == FRAME_PARSE_INCOMPLETE:
            return _tail(idx)
        if status == FRAME_PARSE_OK and packet is not None:
            packets.append(packet)
        idx = next_idx

    return packets, bytes(view[idx:buf_len]), own_stats
