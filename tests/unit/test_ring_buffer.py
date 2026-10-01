"""Unit tests for circular byte ring buffer."""

from __future__ import annotations

from ir_terminal.core.ring_buffer import ByteRingBuffer


class TestByteRingBuffer:
    """Test suite for ByteRingBuffer."""

    def test_initial_state(self) -> None:
        rb = ByteRingBuffer(capacity=16)
        assert len(rb) == 0
        assert rb.capacity == 16
        assert rb.read() == b""

    def test_write_and_read_sequential(self) -> None:
        rb = ByteRingBuffer(capacity=16)
        written = rb.write(b"ABCDEF")
        assert written == 6
        assert len(rb) == 6

        data = rb.read(max_bytes=3)
        assert data == b"ABC"
        assert len(rb) == 3

        data2 = rb.read()
        assert data2 == b"DEF"
        assert len(rb) == 0

    def test_peek_does_not_consume(self) -> None:
        rb = ByteRingBuffer(capacity=16)
        rb.write(b"HELLO")
        assert rb.peek() == b"HELLO"
        assert len(rb) == 5
        assert rb.read() == b"HELLO"
        assert len(rb) == 0

    def test_circular_wrap_around(self) -> None:
        rb = ByteRingBuffer(capacity=8)
        rb.write(b"123456")  # 6 bytes
        assert rb.read(4) == b"1234"  # 2 bytes left ("56"), tail advanced

        rb.write(b"7890")  # Writes wrap around circular buffer
        assert len(rb) == 6
        assert rb.read() == b"567890"

    def test_overflow_overwrites_oldest(self) -> None:
        rb = ByteRingBuffer(capacity=5)
        rb.write(b"12345678")  # Exceeds capacity 5 -> keeps last 5: "45678"
        assert len(rb) == 5
        assert rb.read() == b"45678"

    def test_clear(self) -> None:
        rb = ByteRingBuffer(capacity=16)
        rb.write(b"TESTDATA")
        assert len(rb) == 8
        rb.clear()
        assert len(rb) == 0
        assert rb.read() == b""
