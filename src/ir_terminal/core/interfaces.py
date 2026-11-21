"""Quy chuẩn giao tiếp phần cứng chung cho các loại cổng truyền dẫn (ITransport).

Định nghĩa bộ hàm mẫu mà bất kỳ loại cổng kết nối nào (cổng COM thật nối dây USB, cổng COM ảo giả lập trong RAM, hay cổng Socket mạng LAN) đều phải có:
- Mở cổng (open) và đóng cổng (close).
- Đẩy byte ra chân phát TX (write) và nhặt byte từ chân thu RX (read).
- Kiểm tra xem cổng đang có bao nhiêu byte chờ đọc (in_waiting).
- Bắn lệnh đổi tốc độ Baudrate qua cổng điều khiển STM32 (send_baud_command).
Nhờ quy chuẩn chung này, toàn bộ phần mềm có thể chuyển đổi mượt mà giữa việc cắm mạch thật và chạy mô phỏng giả lập mà không phải sửa một dòng code xử lý nào bên trên.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class ITransport(ABC):
    """Khuôn mẫu chung cho mọi cổng truyền dẫn tín hiệu (cổng COM thật hoặc giả lập)."""

    @property
    def poll_interval_ms(self) -> int:
        """Thời gian định kỳ (10 mili-giây) để quay lại kiểm tra chân RX xem có byte nào mới hay không."""
        return 10

    @abstractmethod
    def open(self) -> None:
        """Mở cổng kết nối ra ngoài mạch phần cứng hoặc mở đường truyền ảo."""

    @abstractmethod
    def close(self) -> None:
        """Đóng cổng và giải phóng cổng cho các phần mềm khác dùng."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Kiểm tra xem cổng hiện tại có đang mở và thông suốt hay không."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Đẩy một chuỗi byte ra chân phát TX.

        Args:
            data: Chuỗi byte thô cần truyền đi.

        Returns:
            Số lượng byte thực tế đã được ghi thành công.
        """

    @abstractmethod
    def read(self, size: int | None = None) -> bytes:
        """Nhặt các byte đang nằm trong bộ đệm chân RX của cổng.

        Args:
            size: Số lượng byte tối đa cần đọc, hoặc None để đọc toàn bộ dữ liệu có sẵn.

        Returns:
            Chuỗi byte nhận được từ bộ đệm.
        """

    @property
    @abstractmethod
    def in_waiting(self) -> int:
        """Số byte hiện đang nằm chờ sẵn ở chân RX chưa đọc ra."""

    @abstractmethod
    def flush(self) -> None:
        """Ép phần cứng đẩy hết tất cả byte còn sót trong hàng đợi phát ra ngoài dây."""

    def send_baud_command(self, baud: int) -> str:
        """Bắn mã lệnh ASCII ('00', '01', '10', '11') sang vi điều khiển STM32 để đổi tốc độ phần cứng.

        Args:
            baud: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).

        Returns:
            Chuỗi 2 ký tự mã lệnh ASCII được firmware chấp nhận ("00", "01", "10", "11").

        Raises:
            NotImplementedError: Nếu transport hiện hành không hỗ trợ kênh điều khiển riêng.
        """
        raise NotImplementedError(f"{type(self).__name__} does not expose a control channel")

    def reconfigure_data_baud(self, baud: int) -> None:
        """Chỉnh lại tốc độ Baudrate cho cổng dữ liệu quang COM1 trên máy tính cho khớp với vi điều khiển.

        Args:
            baud: Tốc độ Baud quang học mới cần áp dụng.

        Raises:
            NotImplementedError: Nếu transport không hỗ trợ thay đổi tốc độ kênh dữ liệu.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot reconfigure the data baudrate")

    on_link_lost: Callable[[], None] | None = None
