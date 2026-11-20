"""Giao diện trừu tượng và hợp đồng giao thức cho tầng truyền tải phần cứng (ITransport).

Quy định các hành vi chuẩn cho việc giao tiếp dòng byte (Serial vật lý, Cổng COM ảo, Mock Loopback, Socket Transport).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class ITransport(ABC):
    """Lớp cơ sở trừu tượng (Abstract Base Class) quy định giao diện chuẩn cho tầng truyền tải."""

    @property
    def poll_interval_ms(self) -> int:
        """Chu kỳ định thời thăm dò (polling interval) nhận dữ liệu tính bằng mili-giây (ms)."""
        return 10

    @abstractmethod
    def open(self) -> None:
        """Mở kết nối tới môi trường truyền thông vật lý hoặc logic."""

    @abstractmethod
    def close(self) -> None:
        """Đóng kết nối và giải phóng tài nguyên phần cứng."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Kiểm tra xem kết nối truyền tải hiện có đang ở trạng thái mở hay không."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Ghi dữ liệu nhị phân xuống kênh truyền.

        Args:
            data: Chuỗi byte thô cần truyền đi.

        Returns:
            Số lượng byte thực tế đã được ghi thành công.
        """

    @abstractmethod
    def read(self, size: int | None = None) -> bytes:
        """Đọc dữ liệu từ bộ đệm nhận của kênh truyền.

        Args:
            size: Số lượng byte tối đa cần đọc, hoặc None để đọc toàn bộ dữ liệu có sẵn.

        Returns:
            Chuỗi byte nhận được từ bộ đệm.
        """

    @property
    @abstractmethod
    def in_waiting(self) -> int:
        """Số lượng byte hiện có sẵn trong bộ đệm nhận để đọc."""

    @abstractmethod
    def flush(self) -> None:
        """Xả sạch toàn bộ dữ liệu trong hàng đợi phát xuống môi trường vật lý."""

    def send_baud_command(self, baud: int) -> str:
        """Gửi lệnh chuyển đổi tốc độ Baud qua kênh điều khiển (ví dụ: COM2 STM32).

        Args:
            baud: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).

        Returns:
            Chuỗi 2 ký tự mã lệnh ASCII được firmware chấp nhận ("00", "01", "10", "11").

        Raises:
            NotImplementedError: Nếu transport hiện hành không hỗ trợ kênh điều khiển riêng.
        """
        raise NotImplementedError(f"{type(self).__name__} does not expose a control channel")

    def reconfigure_data_baud(self, baud: int) -> None:
        """Tái cấu hình tốc độ Baud của kênh dữ liệu quang (COM1).

        Args:
            baud: Tốc độ Baud quang học mới cần áp dụng.

        Raises:
            NotImplementedError: Nếu transport không hỗ trợ thay đổi tốc độ kênh dữ liệu.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot reconfigure the data baudrate")

    on_link_lost: Callable[[], None] | None = None
