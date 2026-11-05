"""Frame-aware RX demultiplexer separating binary packets from ASCII control lines."""

from __future__ import annotations

from dataclasses import dataclass, field

from ir_terminal.protocol.control_channel import (
    CONTROL_LINE_MAX_BYTES,
    ControlMessage,
    parse_control_line,
)
from ir_terminal.protocol.framer import (
    FRAME_PARSE_INCOMPLETE,
    FRAME_PARSE_OK,
    FramerStats,
    parse_frame_at,
)
from ir_terminal.protocol.packet import Packet

_SOF_FIRST: int = 0x55
_SOF_SECOND: int = 0xAA


@dataclass(slots=True)
class DemuxResult:
    """Ordered outcome of one demultiplexing pass."""

    packets: list[Packet] = field(default_factory=list)
    control_messages: list[ControlMessage] = field(default_factory=list)
    events: list[Packet | ControlMessage] = field(default_factory=list)
    remaining: bytes = b""
    consumed: int = 0


def _flush_line(
    line_buf: bytearray,
    stats: FramerStats,
    result: DemuxResult,
) -> None:
    if not line_buf:
        return
    message = parse_control_line(bytes(line_buf))
    if message is None:
        stats.noise_bytes += len(line_buf) + 1
    else:
        stats.control_lines += 1
        stats.control_bytes += len(line_buf) + 1
        result.control_messages.append(message)
        result.events.append(message)
    line_buf.clear()


def demux_stream(
    buffer: bytes | bytearray | memoryview,
    stats: FramerStats | None = None,
) -> DemuxResult:
    """Splits an accumulated RX buffer into packets, control lines and a tail.

    Binary frames are recognised first and consumed whole, so control prefixes
    inside a validated frame payload are never interpreted as commands.
    Partial frames and partial control lines are retained in ``remaining``.

    Args:
        buffer: Accumulated raw byte stream from the UART receiver.
        stats: Optional accumulator updated in place.

    Returns:
        A :class:`DemuxResult` with ordered events, separate packet and control
        lists, the unconsumed tail, and the consumed byte count.
    """
    view = memoryview(buffer) if not isinstance(buffer, memoryview) else buffer
    buf_len = len(view)
    own_stats = stats if stats is not None else FramerStats()
    result = DemuxResult()
    line_buf = bytearray()
    idx = 0

    while idx < buf_len:
        byte = view[idx]

        if byte == _SOF_FIRST and idx + 1 < buf_len and view[idx + 1] == _SOF_SECOND:
            if line_buf:
                own_stats.noise_bytes += len(line_buf)
                line_buf.clear()
            packet, next_idx, status = parse_frame_at(view, idx, own_stats)
            if status == FRAME_PARSE_INCOMPLETE:
                result.remaining = bytes(view[idx:buf_len])
                result.consumed = idx
                return result
            if status == FRAME_PARSE_OK and packet is not None:
                result.packets.append(packet)
                result.events.append(packet)
            idx = next_idx
            continue

        if byte == _SOF_FIRST and idx + 1 == buf_len:
            start = idx - len(line_buf)
            result.remaining = bytes(view[start:buf_len])
            result.consumed = start
            return result

        if 0x20 <= byte <= 0x7E:
            line_buf.append(byte)
            if len(line_buf) > CONTROL_LINE_MAX_BYTES:
                own_stats.noise_bytes += len(line_buf)
                line_buf.clear()
            idx += 1
            continue

        if byte == 0x0D:
            if not line_buf:
                own_stats.noise_bytes += 1
            idx += 1
            continue

        if byte == 0x0A:
            if line_buf:
                _flush_line(line_buf, own_stats, result)
            else:
                own_stats.noise_bytes += 1
            idx += 1
            continue

        if line_buf:
            own_stats.noise_bytes += len(line_buf)
            line_buf.clear()
        own_stats.noise_bytes += 1
        idx += 1

    if line_buf:
        start = idx - len(line_buf)
        result.remaining = bytes(view[start:buf_len])
        result.consumed = start
        return result

    result.remaining = b""
    result.consumed = idx
    return result
