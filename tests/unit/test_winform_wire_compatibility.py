"""Byte-exact compatibility tests for the WinForm C# wire format."""

from __future__ import annotations

import struct

import pytest

from ir_terminal.protocol.constants import (
    HEADER_OVERHEAD,
    PACKET_SIZE,
    SEQ_END,
    SEQ_HEADER,
    SYNC_MAGIC,
)
from ir_terminal.protocol.framer import (
    pack_header_payload,
    pack_packet,
    parse_stream,
    unpack_header_payload,
)
from ir_terminal.protocol.packet import WinFormPacket


class TestWireConstants:
    """Verifies the constants mirror the firmware contract."""

    def test_sync_magic(self) -> None:
        assert SYNC_MAGIC == b"\xAA\x55"
        assert HEADER_OVERHEAD == 8
        assert PACKET_SIZE == 256

    def test_sequence_markers(self) -> None:
        assert SEQ_HEADER == 0xFFFFFFFF
        assert SEQ_END == 0xFFFFFFFE


class TestPackPacket:
    """Verifies the exact byte layout produced for each frame kind."""

    def test_data_frame_layout(self) -> None:
        frame = pack_packet(0x12345678, b"abc")

        assert frame == (
            b"\xAA\x55"
            + struct.pack("<I", 0x12345678)
            + struct.pack("<H", 3)
            + b"abc"
        )
        assert len(frame) == HEADER_OVERHEAD + 3

    def test_zero_length_frame(self) -> None:
        assert pack_packet(SEQ_END, b"") == b"\xAA\x55\xFE\xFF\xFF\xFF\x00\x00"

    def test_header_frame_sequence(self) -> None:
        frame = pack_packet(SEQ_HEADER, b"")
        assert frame[:6] == b"\xAA\x55\xFF\xFF\xFF\xFF"
        assert frame[6:8] == b"\x00\x00"

    def test_full_packet_is_264_wire_bytes(self) -> None:
        assert len(pack_packet(0, b"x" * PACKET_SIZE)) == PACKET_SIZE + HEADER_OVERHEAD

    def test_sequence_must_fit_uint32(self) -> None:
        with pytest.raises(ValueError):
            pack_packet(-1, b"")
        with pytest.raises(ValueError):
            pack_packet(0x1_0000_0000, b"")

    def test_payload_must_fit_uint16(self) -> None:
        with pytest.raises(ValueError):
            pack_packet(0, b"x" * 65536)


class TestHeaderPayload:
    """Verifies the HEADER payload matches the C# file-announcement layout."""

    def test_exact_layout(self) -> None:
        payload = pack_header_payload("lab.bin", 7)

        assert payload == struct.pack("<H", 7) + b"lab.bin" + struct.pack("<i", 7)

    def test_unicode_name_round_trip(self) -> None:
        payload = pack_header_payload("hồng_ngoại.bin", 12)
        assert unpack_header_payload(payload) == ("hồng_ngoại.bin", 12)

    def test_empty_name(self) -> None:
        payload = pack_header_payload("", 0)
        assert payload == b"\x00\x00\x00\x00\x00\x00"
        assert unpack_header_payload(payload) == ("", 0)

    def test_negative_packet_count_rejected(self) -> None:
        with pytest.raises(ValueError):
            pack_header_payload("x.bin", -1)

    def test_truncated_payload_rejected(self) -> None:
        with pytest.raises(ValueError):
            unpack_header_payload(b"\x05")

    def test_declared_name_beyond_payload_rejected(self) -> None:
        with pytest.raises(ValueError):
            unpack_header_payload(b"\x10\x00abc")


class TestParseStream:
    """Verifies fragment reassembly, multiple frames and noise handling."""

    def test_fragmented_stream_reassembly(self) -> None:
        frame = pack_packet(3, b"fragmented-payload")
        buffer = bytearray()

        packets: list[WinFormPacket] = []
        for byte in frame:
            buffer.append(byte)
            decoded, buffer = parse_stream(buffer)
            packets.extend(decoded)

        assert buffer == bytearray()
        assert packets == [WinFormPacket(sequence=3, length=18, data=b"fragmented-payload")]

    def test_multiple_frames_in_one_read(self) -> None:
        stream = pack_packet(0, b"a") + pack_packet(1, b"bb") + pack_packet(SEQ_END, b"")

        packets, remaining = parse_stream(bytearray(stream))

        assert remaining == bytearray()
        assert [packet.sequence for packet in packets] == [0, 1, SEQ_END]
        assert [packet.data for packet in packets] == [b"a", b"bb", b""]

    def test_noise_before_sync_is_discarded(self) -> None:
        stream = b"\x00garbage\xFF" + pack_packet(5, b"ok")

        packets, remaining = parse_stream(bytearray(stream))

        assert remaining == bytearray()
        assert packets == [WinFormPacket(sequence=5, length=2, data=b"ok")]

    def test_lone_first_sync_byte_is_preserved(self) -> None:
        packets, remaining = parse_stream(bytearray(b"noise\xAA"))

        assert packets == []
        assert remaining == bytearray(b"\xAA")

    def test_partial_header_is_preserved(self) -> None:
        packets, remaining = parse_stream(bytearray(b"\xAA\x55\x01\x02"))

        assert packets == []
        assert remaining == bytearray(b"\xAA\x55\x01\x02")

    def test_partial_payload_is_preserved(self) -> None:
        stream = pack_packet(0, b"0123456789")

        truncated = stream[:-4]
        packets, remaining = parse_stream(bytearray(truncated))

        assert packets == []
        assert remaining == bytearray(truncated)

    def test_declared_length_controls_frame_boundary(self) -> None:
        stream = pack_packet(0, b"abc") + b"tail"

        packets, remaining = parse_stream(bytearray(stream))

        assert packets[0].data == b"abc"
        assert remaining == bytearray()

    def test_empty_buffer(self) -> None:
        assert parse_stream(bytearray()) == ([], bytearray())
