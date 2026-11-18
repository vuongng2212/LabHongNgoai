"""Unit tests for the WinForm compatible protocol framer."""

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


class TestPackPacket:
    """Verifies byte-exact wire serialization."""

    def test_pack_data_packet_layout(self) -> None:
        frame = pack_packet(5, b"abc")

        assert frame[:2] == SYNC_MAGIC
        assert frame[2:6] == struct.pack("<I", 5)
        assert frame[6:8] == struct.pack("<H", 3)
        assert frame[8:] == b"abc"
        assert len(frame) == HEADER_OVERHEAD + 3

    def test_pack_header_sequence(self) -> None:
        frame = pack_packet(SEQ_HEADER, b"")
        assert frame[:2] == b"\xAA\x55"
        assert frame[2:6] == b"\xFF\xFF\xFF\xFF"
        assert frame[6:8] == b"\x00\x00"

    def test_pack_end_sequence(self) -> None:
        frame = pack_packet(SEQ_END, b"")
        assert frame[2:6] == b"\xFE\xFF\xFF\xFF"

    def test_maximum_chunk_is_256_bytes(self) -> None:
        frame = pack_packet(0, b"x" * PACKET_SIZE)
        assert len(frame) == HEADER_OVERHEAD + PACKET_SIZE
        assert frame[6:8] == struct.pack("<H", PACKET_SIZE)

    def test_rejects_oversized_payload(self) -> None:
        with pytest.raises(ValueError, match="wire limit"):
            pack_packet(0, b"x" * 65536)

    def test_rejects_negative_and_oversized_sequence(self) -> None:
        with pytest.raises(ValueError, match="uint32"):
            pack_packet(-1, b"")
        with pytest.raises(ValueError, match="uint32"):
            pack_packet(0x1_0000_0000, b"")


class TestHeaderPayload:
    """Verifies HEADER payload packing and unpacking."""

    def test_roundtrip_utf8_name(self) -> None:
        payload = pack_header_payload("bao-cao.txt", 12)

        name, total = unpack_header_payload(payload)

        assert name == "bao-cao.txt"
        assert total == 12

    def test_layout_uses_little_endian_fields(self) -> None:
        payload = pack_header_payload("a.bin", 7)
        name_length = struct.unpack_from("<H", payload, 0)[0]

        assert name_length == 5
        assert payload[2 : 2 + name_length] == b"a.bin"
        assert struct.unpack_from("<i", payload, 2 + name_length)[0] == 7

    def test_empty_name_and_zero_packets(self) -> None:
        assert unpack_header_payload(pack_header_payload("", 0)) == ("", 0)

    def test_rejects_truncated_payload(self) -> None:
        payload = pack_header_payload("name.bin", 3)
        with pytest.raises(ValueError, match="truncated"):
            unpack_header_payload(payload[:-1])

    def test_rejects_too_short_payload(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            unpack_header_payload(b"\x00\x00")

    def test_rejects_negative_packet_count(self) -> None:
        payload = struct.pack("<H", 1) + b"a" + struct.pack("<i", -2)
        with pytest.raises(ValueError, match="negative"):
            unpack_header_payload(payload)

    def test_pack_rejects_negative_packet_count(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            pack_header_payload("a.bin", -1)


class TestParseStream:
    """Verifies stream extraction, fragmentation and noise recovery."""

    def test_parses_multiple_packets(self) -> None:
        blob = pack_packet(0, b"one") + pack_packet(1, b"two") + pack_packet(SEQ_END, b"")

        packets, remaining = parse_stream(bytearray(blob))

        assert [p.sequence for p in packets] == [0, 1, SEQ_END]
        assert [p.data for p in packets] == [b"one", b"two", b""]
        assert remaining == bytearray()

    def test_keeps_partial_frame_for_next_read(self) -> None:
        blob = pack_packet(3, b"payload")
        packets, remaining = parse_stream(bytearray(blob[:5]))

        assert packets == []
        assert remaining == bytearray(blob[:5])
        packets, remaining = parse_stream(remaining + blob[5:])
        assert [p.sequence for p in packets] == [3]
        assert packets[0].data == b"payload"
        assert remaining == bytearray()

    def test_discards_noise_before_sync(self) -> None:
        blob = b"\x00\x01\x02" + pack_packet(9, b"ok")
        packets, remaining = parse_stream(bytearray(blob))

        assert [p.sequence for p in packets] == [9]
        assert remaining == bytearray()

    def test_skips_huge_noise_length_beyond_1024(self) -> None:
        noise = b"\xAA\x55\x00\x00\x00\x00\xFF\xFF"
        real = pack_packet(1, b"valid")
        packets, remaining = parse_stream(bytearray(noise + real))

        assert len(packets) == 1
        assert packets[0].sequence == 1
        assert packets[0].data == b"valid"
        assert remaining == bytearray()

    def test_keeps_trailing_partial_sync_byte(self) -> None:
        packets, remaining = parse_stream(bytearray(b"noise\xAA"))

        assert packets == []
        assert remaining == bytearray(b"\xAA")

    def test_keeps_bytes_after_complete_packet(self) -> None:
        blob = pack_packet(0, b"a") + b"\xAA\x55\x00"
        packets, remaining = parse_stream(bytearray(blob))

        assert len(packets) == 1
        assert remaining == bytearray(b"\xAA\x55\x00")

    def test_roundtrip_header_packet(self) -> None:
        payload = pack_header_payload("firmware.bin", 40)
        packets, _remaining = parse_stream(bytearray(pack_packet(SEQ_HEADER, payload)))

        assert len(packets) == 1
        assert packets[0].is_header is True
        assert packets[0].is_data is False
        assert unpack_header_payload(packets[0].data) == ("firmware.bin", 40)

    def test_empty_buffer_returns_empty(self) -> None:
        assert parse_stream(bytearray()) == ([], bytearray())


class TestWinFormPacket:
    """Verifies packet classification helpers."""

    def test_header_is_not_data_or_end(self) -> None:
        packet = WinFormPacket(sequence=SEQ_HEADER, length=0, data=b"")
        assert packet.is_header is True
        assert packet.is_end is False
        assert packet.is_data is False

    def test_end_is_not_header_or_data(self) -> None:
        packet = WinFormPacket(sequence=SEQ_END, length=0, data=b"")
        assert packet.is_header is False
        assert packet.is_end is True
        assert packet.is_data is False

    def test_zero_sequence_is_data(self) -> None:
        packet = WinFormPacket(sequence=0, length=1, data=b"x")
        assert packet.is_header is False
        assert packet.is_end is False
        assert packet.is_data is True
