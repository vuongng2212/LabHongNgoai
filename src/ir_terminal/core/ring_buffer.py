"""Thread-safe circular byte ring buffer for high-speed UART stream ingestion."""

from __future__ import annotations

import threading


class ByteRingBuffer:
    """Fixed-capacity circular ring buffer for accumulating raw serial byte streams.

    Prevents memory runaway when high-speed burst frames arrive from the MCU.
    """

    def __init__(self, capacity: int = 65536) -> None:
        """Initializes circular buffer with fixed memory capacity.

        Args:
            capacity: Maximum buffer size in bytes (default 64 KB).
        """
        self._capacity = capacity
        self._buffer = bytearray(capacity)
        self._head = 0  # Write pointer
        self._tail = 0  # Read pointer
        self._size = 0  # Current valid bytes in buffer
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Returns maximum capacity of the ring buffer."""
        return self._capacity

    def __len__(self) -> int:
        """Returns the number of readable bytes currently in the buffer."""
        with self._lock:
            return self._size

    def write(self, data: bytes) -> int:
        """Appends byte sequence to the buffer.

        If new data exceeds remaining capacity, oldest bytes are dropped.

        Args:
            data: Byte sequence to write.

        Returns:
            Number of bytes actually written.
        """
        with self._lock:
            data_len = len(data)
            if data_len == 0:
                return 0

            # If incoming data exceeds capacity, take only the most recent bytes
            if data_len > self._capacity:
                data = data[-self._capacity :]
                data_len = self._capacity

            for b in data:
                self._buffer[self._head] = b
                self._head = (self._head + 1) % self._capacity
                if self._size < self._capacity:
                    self._size += 1
                else:
                    # Buffer full: drop oldest byte (advance tail pointer)
                    self._tail = (self._tail + 1) % self._capacity

            return data_len

    def read(self, max_bytes: int | None = None) -> bytes:
        """Extracts and removes up to max_bytes from the buffer.

        Args:
            max_bytes: Maximum number of bytes to retrieve. If None, retrieves all.

        Returns:
            Extracted bytes sequence.
        """
        with self._lock:
            if self._size == 0:
                return b""

            to_read = self._size if max_bytes is None else min(max_bytes, self._size)
            result = bytearray(to_read)

            for i in range(to_read):
                result[i] = self._buffer[self._tail]
                self._tail = (self._tail + 1) % self._capacity

            self._size -= to_read
            return bytes(result)

    def peek(self) -> bytes:
        """Inspects all currently available bytes without removing them from buffer.

        Returns:
            Copy of available bytes in order.
        """
        with self._lock:
            if self._size == 0:
                return b""

            result = bytearray(self._size)
            curr = self._tail
            for i in range(self._size):
                result[i] = self._buffer[curr]
                curr = (curr + 1) % self._capacity

            return bytes(result)

    def clear(self) -> None:
        """Resets head, tail pointers and empties the buffer."""
        with self._lock:
            self._head = 0
            self._tail = 0
            self._size = 0
