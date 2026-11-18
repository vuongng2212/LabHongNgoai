"""Wire framing for the WinForm C# compatible optical protocol."""

from __future__ import annotations

import struct

from ir_terminal.protocol.constants import (
    HEADER_OVERHEAD,
    MAX_HEADER_PAYLOAD_SIZE,
    SYNC_MAGIC,
)
from ir_terminal.protocol.packet import WinFormPacket

_PACKET_HEADER: struct.Struct = struct.Struct("<IH")

_HEADER_NAME_LENGTH: struct.Struct = struct.Struct("<H")

_HEADER_TOTAL_PACKETS: struct.Struct = struct.Struct("<i")

_MAX_SEQUENCE: int = 0xFFFFFFFF


def pack_packet(sequence: int, data: bytes) -> bytes:
    """Serializes one WinForm frame.

    Args:
        sequence: Frame identifier (uint32).
        data: Payload bytes (maximum 65535 bytes).

    Returns:
        Wire bytes: sync magic, sequence, payload length and payload.

    Raises:
        ValueError: If the sequence or payload length does not fit the wire
            header.
    """
    if not 0 <= sequence <= _MAX_SEQUENCE:
        raise ValueError(f"Sequence {sequence} must fit uint32")
    payload = bytes(data)
    if len(payload) > MAX_HEADER_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload size {len(payload)} exceeds wire limit {MAX_HEADER_PAYLOAD_SIZE} bytes"
        )
    return b"".join((SYNC_MAGIC, _PACKET_HEADER.pack(sequence, len(payload)), payload))


def pack_header_payload(file_name: str, total_packets: int) -> bytes:
    """Serializes the HEADER packet payload.

    Layout: ``nameLength uint16 LE``, ``name UTF-8``, ``totalPackets int32 LE``.

    Args:
        file_name: Original file name announced to the receiver.
        total_packets: Number of DATA chunks that will follow.

    Returns:
        Encoded header payload bytes.

    Raises:
        ValueError: If the name or packet count cannot be represented.
    """
    name_bytes = (file_name or "").encode("utf-8")
    if len(name_bytes) > MAX_HEADER_PAYLOAD_SIZE:
        raise ValueError(
            f"File name is {len(name_bytes)} bytes, wire limit is {MAX_HEADER_PAYLOAD_SIZE}"
        )
    if total_packets < 0:
        raise ValueError(f"Total packets must be non-negative, got {total_packets}")
    return b"".join(
        (
            _HEADER_NAME_LENGTH.pack(len(name_bytes)),
            name_bytes,
            _HEADER_TOTAL_PACKETS.pack(total_packets),
        )
    )


def unpack_header_payload(data: bytes) -> tuple[str, int]:
    """Decodes a HEADER packet payload.

    Args:
        data: Raw HEADER payload bytes.

    Returns:
        Tuple of ``(file_name, total_packets)``.

    Raises:
        ValueError: If the payload is truncated or declares invalid fields.
    """
    raw = bytes(data)
    if len(raw) < _HEADER_NAME_LENGTH.size + _HEADER_TOTAL_PACKETS.size:
        raise ValueError(f"Header payload too short: {len(raw)} bytes")
    name_length = _HEADER_NAME_LENGTH.unpack_from(raw, 0)[0]
    total_offset = _HEADER_NAME_LENGTH.size + name_length
    if len(raw) < total_offset + _HEADER_TOTAL_PACKETS.size:
        raise ValueError(
            f"Header payload truncated: declared name {name_length} bytes in {len(raw)} byte payload"
        )
    file_name = raw[_HEADER_NAME_LENGTH.size : total_offset].decode("utf-8", errors="replace")
    total_packets = _HEADER_TOTAL_PACKETS.unpack_from(raw, total_offset)[0]
    if total_packets < 0:
        raise ValueError(f"Header declares negative packet count {total_packets}")
    return file_name, total_packets


def parse_stream(buffer: bytearray) -> tuple[list[WinFormPacket], bytearray]:
    """Extracts every complete frame from an accumulated byte stream.

    Noise bytes preceding a sync pattern are discarded. A partial trailing
    frame, including a lone ``0xAA`` that may become the next sync pattern, is
    returned as the remaining buffer for the next read cycle.

    Args:
        buffer: Accumulated bytes received from the optical channel.

    Returns:
        Tuple of decoded packets and the unconsumed byte tail.
    """
    raw = bytes(buffer)
    total = len(raw)
    packets: list[WinFormPacket] = []
    position = 0

    while True:
        sync_at = raw.find(SYNC_MAGIC, position)
        if sync_at < 0:
            position = total - 1 if total > position and raw.endswith(SYNC_MAGIC[:1]) else total
            break
        if total - sync_at < HEADER_OVERHEAD:
            position = sync_at
            break
        sequence, length = _PACKET_HEADER.unpack_from(raw, sync_at + len(SYNC_MAGIC))
        frame_end = sync_at + HEADER_OVERHEAD + length
        if total < frame_end:
            position = sync_at
            break
        payload = raw[sync_at + HEADER_OVERHEAD : frame_end]
        packets.append(WinFormPacket(sequence=sequence, length=length, data=payload))
        position = frame_end

    return packets, bytearray(raw[position:])
