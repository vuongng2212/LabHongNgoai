"""Bộ đệm vòng byte hiệu năng cao (Byte Ring Buffer) an toàn đa luồng.

Được thiết kế để tích lũy dòng byte tốc độ cao từ cổng UART phần cứng, ngăn ngừa tràn bộ nhớ RAM
khi nhận các loạt gói tin burst frame liên tục từ vi điều khiển MCU.
"""

from __future__ import annotations

import threading


class ByteRingBuffer:
    """Bộ đệm vòng tuần hoàn kích thước cố định để tích lũy dòng byte nối tiếp.

    Hỗ trợ cơ chế khóa đa luồng threading.Lock, tự động đẩy bỏ các byte cũ nhất khi bị tràn
    và ghi nhận số byte bị rớt (dropped bytes) phục vụ công tác chẩn đoán viễn trắc.
    """

    def __init__(self, capacity: int = 65536) -> None:
        """Khởi tạo bộ đệm vòng với dung lượng bộ nhớ cố định.

        Args:
            capacity: Dung lượng tối đa của bộ đệm tính theo byte (mặc định 64 KB = 65536 bytes).
        """
        self._capacity = capacity
        self._buffer = bytearray(capacity)
        self._head = 0
        self._tail = 0
        self._size = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Dung lượng tối đa (sức chứa) của bộ đệm vòng tính theo byte."""
        return self._capacity

    def __len__(self) -> int:
        """Số lượng byte hợp lệ hiện đang có sẵn trong bộ đệm để đọc."""
        with self._lock:
            return self._size

    @property
    def dropped_bytes(self) -> int:
        """Tổng số byte lũy kế đã bị loại bỏ do tràn bộ đệm vòng."""
        with self._lock:
            return self._dropped

    def write(self, data: bytes) -> int:
        """Ghi chuỗi byte vào bộ đệm vòng tuần hoàn.

        Nếu dữ liệu mới vượt quá dung lượng còn trống, các byte cũ nhất sẽ tự động bị ghi đè.

        Args:
            data: Chuỗi byte nhị phân cần ghi vào bộ đệm.

        Returns:
            Số lượng byte thực tế đã được tiếp nhận.
        """
        with self._lock:
            data_len = len(data)
            if data_len == 0:
                return 0

            if data_len > self._capacity:
                data = data[-self._capacity :]
                data_len = self._capacity

            first = min(data_len, self._capacity - self._head)
            self._buffer[self._head : self._head + first] = data[:first]
            second = data_len - first
            if second:
                self._buffer[0:second] = data[first:]
            self._head = (self._head + data_len) % self._capacity
            if self._size + data_len <= self._capacity:
                self._size += data_len
            else:
                overflow = self._size + data_len - self._capacity
                self._tail = (self._tail + overflow) % self._capacity
                self._size = self._capacity
                self._dropped += overflow

            return data_len

    def read(self, max_bytes: int | None = None) -> bytes:
        """Đọc và trích xuất dữ liệu ra khỏi bộ đệm vòng (xóa khỏi buffer).

        Args:
            max_bytes: Số byte tối đa cần đọc. Nếu truyền None, đọc toàn bộ byte hiện có.

        Returns:
            Chuỗi byte nhị phân đã trích xuất từ bộ đệm.
        """
        with self._lock:
            if self._size == 0:
                return b""

            to_read = self._size if max_bytes is None else min(max_bytes, self._size)
            first = min(to_read, self._capacity - self._tail)
            result = bytearray(to_read)
            result[0:first] = self._buffer[self._tail : self._tail + first]
            second = to_read - first
            if second:
                result[first:] = self._buffer[0:second]
            self._tail = (self._tail + to_read) % self._capacity

            self._size -= to_read
            return bytes(result)

    def peek(self) -> bytes:
        """Xem trước toàn bộ dữ liệu hiện có trong bộ đệm mà không làm thay đổi con trỏ đọc.

        Returns:
            Bản sao của chuỗi byte hiện có theo đúng thứ tự FIFO.
        """
        with self._lock:
            if self._size == 0:
                return b""

            first = min(self._size, self._capacity - self._tail)
            result = bytearray(self._size)
            result[0:first] = self._buffer[self._tail : self._tail + first]
            second = self._size - first
            if second:
                result[first:] = self._buffer[0:second]

            return bytes(result)

    def clear(self) -> None:
        """Xóa sạch bộ đệm và đặt lại các con trỏ head, tail về vị trí ban đầu."""
        with self._lock:
            self._head = 0
            self._tail = 0
            self._size = 0
