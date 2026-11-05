"""Unit tests for the frame-aware RX demultiplexer."""

from __future__ import annotations

from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.control_channel import ControlKind, ControlMessage
from ir_terminal.protocol.framer import FramerStats, pack_frame, parse_frames_with_stats
from ir_terminal.protocol.packet import Packet
from ir_terminal.protocol.stream_demux import demux_stream


class TestControlLineDemux:
    """Verifies control-line recognition and retention across chunks."""

    def test_single_control_line(self) -> None:
        stats = FramerStats()
        result = demux_stream(b"BAUD_OK:19200\n", stats)

        assert result.packets == []
        assert len(result.control_messages) == 1
        assert result.control_messages[0].kind == ControlKind.BAUD_OK
        assert result.control_messages[0].baudrate == 19200
        assert result.remaining == b""
        assert result.consumed == len(b"BAUD_OK:19200\n")
        assert stats.control_lines == 1
        assert stats.noise_bytes == 0

    def test_control_line_split_across_chunks(self) -> None:
        first = demux_stream(b"BAUD_OK:19", FramerStats())
        assert first.control_messages == []
        assert first.remaining == b"BAUD_OK:19"
        assert first.consumed == 0

        stats = FramerStats()
        second = demux_stream(first.remaining + b"200\n", stats)

        assert len(second.control_messages) == 1
        assert second.control_messages[0].baudrate == 19200
        assert second.remaining == b""
        assert stats.control_lines == 1

    def test_crlf_terminator_is_tolerated(self) -> None:
        result = demux_stream(b"NOTIFY_BAUD:4800\r\n")
        assert len(result.control_messages) == 1
        assert result.control_messages[0].kind == ControlKind.NOTIFY_BAUD

    def test_noise_before_control_line(self) -> None:
        stats = FramerStats()
        result = demux_stream(b"\x00\x01BAUD_OK:9600\n", stats)

        assert len(result.control_messages) == 1
        assert stats.noise_bytes == 2
        assert stats.crc_errors == 0

    def test_unknown_ascii_line_counts_as_noise(self) -> None:
        stats = FramerStats()
        result = demux_stream(b"HELLO:WORLD\n", stats)

        assert result.control_messages == []
        assert stats.control_lines == 0
        assert stats.noise_bytes == len(b"HELLO:WORLD\n")

    def test_recognised_control_line_does_not_increment_errors(self) -> None:
        stats = FramerStats()
        demux_stream(b"BAUD_OK:19200\nNOTIFY_BAUD:4800\n", stats)

        assert stats.control_lines == 2
        assert stats.crc_errors == 0
        assert stats.eof_errors == 0
        assert stats.len_errors == 0
        assert stats.noise_bytes == 0


class TestFrameAwareDemux:
    """Verifies binary framing is untouched by the control channel."""

    def test_control_token_inside_payload_is_not_emitted(self) -> None:
        payload = b"BAUD_OK:19200\n"
        frame = pack_frame(seq=7, opcode=Opcode.DATA_TEXT, payload=payload)
        stats = FramerStats()

        result = demux_stream(frame, stats)

        assert result.control_messages == []
        assert len(result.packets) == 1
        assert result.packets[0].payload == payload
        assert stats.frames_ok == 1
        assert stats.crc_errors == 0

    def test_newline_inside_payload_does_not_split_frame(self) -> None:
        payload = b"line1\nline2\r\nline3"
        frame = pack_frame(seq=3, opcode=Opcode.DATA_TEXT, payload=payload)

        result = demux_stream(frame)

        assert result.control_messages == []
        assert result.packets[0].payload == payload

    def test_split_frame_is_retained(self) -> None:
        frame = pack_frame(seq=9, opcode=Opcode.DATA_CHUNK, payload=b"A" * 32)
        first = demux_stream(frame[:10])

        assert first.packets == []
        assert first.remaining == frame[:10]
        assert first.consumed == 0

        second = demux_stream(first.remaining + frame[10:])
        assert len(second.packets) == 1
        assert second.packets[0].payload == b"A" * 32
        assert second.remaining == b""

    def test_split_sof_byte_is_retained(self) -> None:
        frame = pack_frame(seq=11, opcode=Opcode.PING, payload=b"")
        first = demux_stream(frame[:1])

        assert first.packets == []
        assert first.remaining == b"\x55"
        assert first.consumed == 0

        second = demux_stream(first.remaining + frame[1:])
        assert len(second.packets) == 1
        assert second.packets[0].seq == 11

    def test_control_lines_before_and_after_frame_preserve_order(self) -> None:
        frame = pack_frame(seq=5, opcode=Opcode.PING, payload=b"ok")
        stream = b"BAUD_OK:4800\n" + frame + b"NOTIFY_BAUD:9600\n"

        result = demux_stream(stream)

        assert len(result.events) == 3
        first, second, third = result.events
        assert isinstance(first, ControlMessage)
        assert first.kind == ControlKind.BAUD_OK
        assert isinstance(second, Packet)
        assert second.seq == 5
        assert isinstance(third, ControlMessage)
        assert third.kind == ControlKind.NOTIFY_BAUD

    def test_multiple_frames_and_control_lines(self) -> None:
        frames = b"".join(
            pack_frame(seq=index, opcode=Opcode.DATA_CHUNK, payload=b"X" * 8)
            for index in range(5)
        )
        stream = b"BAUD_OK:19200\n" + frames + b"BAUD_OK:2400\n"

        result = demux_stream(stream, FramerStats())

        assert len(result.packets) == 5
        assert [packet.seq for packet in result.packets] == [0, 1, 2, 3, 4]
        assert len(result.control_messages) == 2
        assert result.consumed == len(stream)

    def test_incomplete_frame_tail_after_control_line(self) -> None:
        frame = pack_frame(seq=1, opcode=Opcode.PING, payload=b"")
        stream = b"BAUD_OK:9600\n" + frame[:5]

        result = demux_stream(stream)

        assert len(result.control_messages) == 1
        assert result.remaining == frame[:5]
        assert result.consumed == len(b"BAUD_OK:9600\n")

    def test_frame_parser_public_behaviour_unchanged(self) -> None:
        frame = pack_frame(seq=2, opcode=Opcode.PONG, payload=b"data")
        packets, remaining, stats = parse_frames_with_stats(b"\x00\xff" + frame)

        assert len(packets) == 1
        assert packets[0].payload == b"data"
        assert remaining == b""
        assert stats.noise_bytes == 2
        assert stats.frames_ok == 1
