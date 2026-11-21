"""Thùng chứa dữ liệu đệm vòng: chứa các byte nhận về từ UART trước khi đem ra phân tích gói tin (Byte Ring Buffer).

Đóng vai trò như một kho chứa đệm dung lượng cố định 64 KB (FIFO) nằm giữa phần cứng UART và bộ giải mã gói tin:
- Khi mạch thu quang TFDU4101 nhận dữ liệu dồn dập, các byte sẽ được đổ tạm vào thùng chứa này trước.
- Giúp dữ liệu không bị nghẽn hay tràn RAM của máy tính.
- Nếu ứng dụng bận xử lý tác vụ khác khiến thùng chứa bị đầy tràn (overflow), bộ đệm sẽ tự động bỏ các byte cũ nhất để chứa byte mới và ghi nhận số lượng byte bị rớt (dropped bytes) để báo lên biểu đồ viễn trắc.
"""

from __future__ import annotations

import threading


class ByteRingBuffer:
    """Thùng chứa dữ liệu đệm vòng: chứa các byte nhận về từ UART trước khi đem ra phân tích gói tin."""

    def __init__(self, capacity: int = 65536) -> None:
        """Khởi tạo thùng chứa đệm vòng với sức chứa cố định (mặc định 64 KB = 65536 bytes).

        Args:
            capacity: Sức chứa tối đa của thùng đệm tính theo byte.
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
        """Sức chứa tối đa của thùng đệm (byte)."""
        return self._capacity

    def __len__(self) -> int:
        """Số lượng byte thực tế hiện đang nằm trong thùng chờ lấy ra xử lý."""
        with self._lock:
            return self._size

    @property
    def dropped_bytes(self) -> int:
        """Tổng số byte bị tràn thùng và buộc phải bỏ đi do mạch quang gửi về quá nhanh."""
        with self._lock:
            return self._dropped

    def write(self, data: bytes) -> int:
        """Đổ thêm các byte mới nhận từ cổng UART vào thùng đệm vòng.

        Nếu dữ liệu mới nhiều hơn dung lượng còn trống, thùng đệm sẽ tự động ghi đè lên các byte cũ nhất.

        Args:
            data: Chuỗi byte nhị phân vừa nhặt được từ chân RX.

        Returns:
            Số lượng byte thực tế đã nhét vào thùng.
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
        """Lấy bớt các byte trong thùng đệm ra để đem đi ráp khung gói tin (dữ liệu lấy ra sẽ được xóa khỏi thùng).

        Args:
            max_bytes: Số byte tối đa muốn lấy ra. Nếu để None sẽ lấy toàn bộ byte đang có trong thùng.

        Returns:
            Chuỗi byte nhị phân đã lấy ra theo thứ tự vào trước - ra trước (FIFO).
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
        """Nhìn lén dữ liệu hiện có trong thùng mà không rút byte nào ra khỏi thùng.

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
        """Đổ sạch toàn bộ dữ liệu trong thùng đệm và đặt các con trỏ đầu/cuối về vị trí ban đầu."""
        with self._lock:
            self._head = 0
            self._tail = 0
            self._size = 0
