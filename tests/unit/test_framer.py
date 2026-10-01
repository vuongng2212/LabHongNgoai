"""Unit tests for binary frame packing, CRC-32 validation, and stream parser."""

from __future__ import annotations

import pytest

from ir_terminal.protocol.constants import (
    EOF_MAGIC,
    MAX_PAYLOAD_SIZE,
    SOF_MAGIC,
    Opcode,
)
from ir_terminal.protocol.framer import (
    pack_frame,
    parse_frames_from_buffer,
)
from ir_terminal.protocol.packet import Packet


class TestFramer:
    """Test suite for binary protocol framing."""

    def test_pack_and_parse_single_frame(self) -> None:
        """Verifies that a packed frame can be parsed back with 100% integrity."""
        payload = b"Hello Optical Lab!"
        seq = 42
        opcode = Opcode.DATA_TEXT

        frame = pack_frame(seq=seq, opcode=opcode, payload=payload)
        assert len(frame) == 6 + len(payload) + 6

        packets, remaining = parse_frames_from_buffer(frame)
        assert len(packets) == 1
        assert remaining == b""

        pkt = packets[0]
        assert isinstance(pkt, Packet)
        assert pkt.seq == seq
        assert pkt.opcode == opcode
        assert pkt.payload == payload
        assert pkt.header.sof == SOF_MAGIC
        assert pkt.eof == EOF_MAGIC

    def test_pack_empty_payload_frame(self) -> None:
        """Verifies frames with zero-length payload (e.g. ACK, PING)."""
        frame = pack_frame(seq=10, opcode=Opcode.PING, payload=b"")
        assert len(frame) == 12  # Header (6) + Footer (6)

        packets, remaining = parse_frames_from_buffer(frame)
        assert len(packets) == 1
        assert remaining == b""
        assert packets[0].payload == b""
        assert packets[0].opcode == Opcode.PING

    def test_pack_max_payload(self) -> None:
        """Verifies packing maximum allowed payload size."""
        payload = b"X" * MAX_PAYLOAD_SIZE
        frame = pack_frame(seq=255, opcode=Opcode.DATA_CHUNK, payload=payload)

        packets, remaining = parse_frames_from_buffer(frame)
        assert len(packets) == 1
        assert packets[0].payload == payload

    def test_pack_oversized_payload_raises_error(self) -> None:
        """Verifies that exceeding MAX_PAYLOAD_SIZE raises ValueError."""
        oversized = b"A" * (MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds max allowed"):
            pack_frame(seq=1, opcode=Opcode.DATA_CHUNK, payload=oversized)

    def test_invalid_seq_number_raises_error(self) -> None:
        """Verifies that sequence numbers outside 0..255 raise ValueError."""
        with pytest.raises(ValueError, match="must be in range 0..255"):
            pack_frame(seq=256, opcode=Opcode.DATA_CHUNK, payload=b"test")
        with pytest.raises(ValueError, match="must be in range 0..255"):
            pack_frame(seq=-1, opcode=Opcode.DATA_CHUNK, payload=b"test")

    def test_corrupted_crc_is_rejected(self) -> None:
        """Verifies that bit errors in payload or header cause packet to be dropped."""
        payload = b"Sensor Data 12345"
        frame = bytearray(pack_frame(seq=1, opcode=Opcode.DATA_CHUNK, payload=payload))

        # Corrupt 1 byte in payload
        frame[8] ^= 0xFF

        packets, remaining = parse_frames_from_buffer(bytes(frame))
        assert len(packets) == 0  # Corrupted packet must NOT be accepted

    def test_corrupted_eof_is_rejected(self) -> None:
        """Verifies that EOF delimiter mismatch causes packet rejection."""
        frame = bytearray(pack_frame(seq=1, opcode=Opcode.DATA_CHUNK, payload=b"test"))
        # Tamper with EOF byte
        frame[-1] = 0x00

        packets, remaining = parse_frames_from_buffer(bytes(frame))
        assert len(packets) == 0

    def test_stream_noise_recovery(self) -> None:
        """Verifies that random garbage bytes before a valid frame are cleanly skipped."""
        noise = b"\x00\xff\x12\x34\x56\x78\x99\xaa"
        valid_frame = pack_frame(seq=5, opcode=Opcode.DATA_TEXT, payload=b"Recovered!")
        stream = noise + valid_frame

        packets, remaining = parse_frames_from_buffer(stream)
        assert len(packets) == 1
        assert packets[0].seq == 5
        assert packets[0].payload == b"Recovered!"
        assert remaining == b""

    def test_multiple_concatenated_frames(self) -> None:
        """Verifies parsing multiple back-to-back frames in a single buffer read."""
        frame1 = pack_frame(seq=1, opcode=Opcode.DATA_CHUNK, payload=b"Chunk 1")
        frame2 = pack_frame(seq=2, opcode=Opcode.DATA_CHUNK, payload=b"Chunk 2")
        frame3 = pack_frame(seq=3, opcode=Opcode.ACK, payload=b"")

        stream = frame1 + frame2 + frame3
        packets, remaining = parse_frames_from_buffer(stream)

        assert len(packets) == 3
        assert packets[0].seq == 1
        assert packets[0].payload == b"Chunk 1"
        assert packets[1].seq == 2
        assert packets[1].payload == b"Chunk 2"
        assert packets[2].seq == 3
        assert packets[2].opcode == Opcode.ACK
        assert remaining == b""

    def test_fragmented_stream_parsing(self) -> None:
        """Verifies that incomplete fragmented frames are preserved in remaining buffer."""
        full_frame = pack_frame(seq=7, opcode=Opcode.DATA_TEXT, payload=b"Long message payload")

        # Split into two halves
        part1 = full_frame[:12]
        part2 = full_frame[12:]

        # Feed part 1
        packets1, rem1 = parse_frames_from_buffer(part1)
        assert len(packets1) == 0
        assert rem1 == part1

        # Feed part 2 appended to part 1
        packets2, rem2 = parse_frames_from_buffer(rem1 + part2)
        assert len(packets2) == 1
        assert packets2[0].seq == 7
        assert packets2[0].payload == b"Long message payload"
        assert rem2 == b""
