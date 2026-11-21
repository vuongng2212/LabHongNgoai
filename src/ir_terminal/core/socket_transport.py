"""Đường truyền mạng ảo giả lập tia quang giữa 2 tiến trình trên máy tính (Socket Transport).

Thay vì phải cắm dây cáp nối mạch quang thật giữa 2 máy tính, module này mở cổng mạng TCP nội bộ (loopback 127.0.0.1):
- Kích hoạt khi người dùng chọn cổng ảo LINK_A hoặc LINK_B trên màn hình.
- Đóng vai trò như một sợi dây nối ảo giữa trạm phát và trạm thu, chuyển tiếp từng byte y hệt như tia hồng ngoại bắn trong không khí.
- Giúp kỹ sư kiểm thử được toàn bộ tính năng nhắn tin, gửi nhận file khi chỉ có 1 chiếc máy tính xách tay và không có mạch phần cứng bên cạnh.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import (
    BAUD_COMMANDS,
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    SOCKET_CONNECT_TIMEOUT_SEC,
    SOCKET_READ_TIMEOUT_SEC,
    SOCKET_RECV_CHUNK,
)
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)

LINK_PORT_MAP: dict[str, tuple[str, int]] = {
    "LINK_A": ("127.0.0.1", 6001),
    "LINK_B": ("127.0.0.1", 6002),
    "LINK_A_TX": ("127.0.0.1", 6001),
    "LINK_A_RX": ("127.0.0.1", 6001),
    "LINK_B_TX": ("127.0.0.1", 6002),
    "LINK_B_RX": ("127.0.0.1", 6002),
}

_HELLO_PREFIX = "IRLINK 1 "


def resolve_link_endpoint(
    port_name: str,
    endpoints: dict[str, tuple[str, int]] | None = None,
) -> tuple[str, int] | None:
    """Tra cứu địa chỉ host và port của cổng ảo LINK_*, trả về None nếu không tìm thấy.

    Args:
        port_name: Tên định danh cổng ảo (không phân biệt chữ hoa chữ thường).
        endpoints: Bảng ánh xạ tùy chọn; mặc định sử dụng LINK_PORT_MAP.

    Returns:
        Tuple gồm (host, port) hoặc None nếu tên cổng không hợp lệ.
    """
    key = port_name.strip().upper()
    table = endpoints if endpoints is not None else LINK_PORT_MAP
    return table.get(key)


class SocketTransport(ITransport):
    """Tầng truyền tải TCP Client tuân thủ giao diện ITransport cho chế độ mô phỏng hai ứng dụng."""

    def __init__(
        self,
        port_name: str = "LINK_A",
        host: str = "127.0.0.1",
        port: int = 6001,
        *,
        connect_retries: int = 0,
        retry_interval_sec: float = 0.25,
        connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
        token: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Khởi tạo tầng truyền tải TCP Socket.

        Args:
            port_name: Tên định danh cổng ảo (LINK_A hoặc LINK_B).
            host: Địa chỉ IP hoặc hostname của Broker.
            port: Cổng TCP của Broker.
            connect_retries: Số lần thử kết nối lại bổ sung khi broker chưa sẵn sàng khởi động.
            retry_interval_sec: Khoảng thời gian chờ giữa các lần thử kết nối lại (giây).
            connect_timeout_sec: Thời gian chờ tối đa cho mỗi lần kết nối TCP (giây).
            token: Khóa bí mật chia sẻ gửi kèm trong dòng bắt tay xác thực (nếu có).
            baudrate: Tốc độ Baud quang học mô phỏng cho kênh dữ liệu.
            ctrl_baudrate: Tốc độ Baud điều khiển mô phỏng cố định.
        """
        self._port_name = port_name
        self._baudrate = int(baudrate)
        self._ctrl_baudrate = int(ctrl_baudrate)
        self._sent_baud_commands: list[tuple[int, str]] = []
        self._host = host
        self._port = port
        self._connect_retries = max(0, int(connect_retries))
        self._retry_interval_sec = max(0.0, float(retry_interval_sec))
        self._connect_timeout_sec = float(connect_timeout_sec)
        self._token = token
        self._sock: socket.socket | None = None
        self._rx_buffer = bytearray()
        self._is_open = False
        self._lost_notified = False
        self.on_link_lost: Callable[[], None] | None = None

    def open(self) -> None:
        """Mở kết nối TCP tới tiến trình Broker điều phối liên kết quang ảo."""
        attempts = self._connect_retries + 1
        logger.info(
            "%s Socket open: %s -> %s:%d (attempts=%d, connect_timeout=%.1fs, read_timeout=%.3fs)",
            LogTag.HW_SERIAL,
            self._port_name,
            self._host,
            self._port,
            attempts,
            self._connect_timeout_sec,
            SOCKET_READ_TIMEOUT_SEC,
        )
        self.close()
        last_exc: OSError | None = None
        for attempt in range(1, attempts + 1):
            try:
                sock = socket.create_connection(
                    (self._host, self._port), timeout=self._connect_timeout_sec
                )
            except OSError as exc:
                last_exc = exc
                if attempt < attempts:
                    logger.warning(
                        "%s Socket connect attempt %d/%d failed: %s -> %s:%d (%s: %s); "
                        "retrying in %.2fs (is the link host running?)",
                        LogTag.HW_SERIAL,
                        attempt,
                        attempts,
                        self._port_name,
                        self._host,
                        self._port,
                        type(exc).__name__,
                        exc,
                        self._retry_interval_sec,
                    )
                    time.sleep(self._retry_interval_sec)
                    continue
                logger.exception(
                    "%s Socket open FAILED after %d attempt(s): %s -> %s:%d",
                    LogTag.HW_SERIAL,
                    attempts,
                    self._port_name,
                    self._host,
                    self._port,
                )
                raise
            sock.settimeout(SOCKET_READ_TIMEOUT_SEC)
            self._sock = sock
            self._rx_buffer = bytearray()
            self._lost_notified = False
            if self._token:
                self._send_handshake()
            self._is_open = True
            logger.info(
                "%s SocketTransport %s connected to %s:%d (attempt %d/%d)",
                LogTag.HW_SERIAL,
                self._port_name,
                self._host,
                self._port,
                attempt,
                attempts,
            )
            return
        raise last_exc if last_exc is not None else RuntimeError("SocketTransport open failed")

    def _send_handshake(self) -> None:
        """Gửi dòng bắt tay chứa token xác thực tùy chọn tới broker."""
        sock = self._sock
        if sock is None:
            raise RuntimeError("SocketTransport not open for handshake")
        sock.sendall(f"{_HELLO_PREFIX}{self._token}\n".encode())

    def _notify_lost(self) -> None:
        """Kích hoạt callback on_link_lost tối đa một lần cho mỗi phiên kết nối."""
        if self._lost_notified:
            return
        self._lost_notified = True
        callback = self.on_link_lost
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception(
                "%s on_link_lost callback failed (%s)", LogTag.HW_SERIAL, self._port_name
            )

    def close(self) -> None:
        """Đóng kết nối TCP và giải phóng tài nguyên socket."""
        was_open = self._is_open
        buffered = len(self._rx_buffer)
        self._is_open = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError as exc:
                logger.warning(
                    "%s Socket close error: %s %s:%d: %s",
                    LogTag.HW_SERIAL,
                    self._port_name,
                    self._host,
                    self._port,
                    exc,
                )
        self._sock = None
        self._rx_buffer = bytearray()
        if was_open:
            logger.info(
                "%s Socket closed: %s was_open=True dropped_buffered=%dB",
                LogTag.HW_SERIAL,
                self._port_name,
                buffered,
            )

    @property
    def is_open(self) -> bool:
        """Kiểm tra kết nối TCP hiện có đang hoạt động hay không."""
        return self._is_open and self._sock is not None

    def inject_rx_data(self, data: bytes) -> None:
        """Bơm trực tiếp dòng byte nhận vào bộ đệm nhận cục bộ."""
        self._rx_buffer.extend(data)

    def write(self, data: bytes) -> int:
        """Gửi dữ liệu byte tới broker để chuyển tiếp sang ứng dụng đối tác.

        Args:
            data: Chuỗi byte thô của khung truyền.

        Returns:
            Số byte đã gửi đi thành công.
        """
        if not self.is_open or self._sock is None:
            logger.warning(
                "%s Socket write rejected (closed): %s len=%dB",
                LogTag.HW_SERIAL,
                self._port_name,
                len(data),
            )
            raise RuntimeError("SocketTransport not open for write")
        try:
            self._sock.sendall(data)
        except Exception:
            self._is_open = False
            self._notify_lost()
            logger.exception(
                "%s Socket write FAILED: %s -> %s:%d len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                self._host,
                self._port,
                len(data),
                format_hex_preview(data),
            )
            raise
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Socket TX: %s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                len(data),
                format_hex_preview(data),
            )
        return len(data)

    def _fill(self) -> None:
        """Kéo toàn bộ byte có sẵn từ socket vào bộ đệm nhận RX (chế độ non-blocking)."""
        sock = self._sock
        if sock is None or not self._is_open:
            return
        try:
            while True:
                chunk = sock.recv(SOCKET_RECV_CHUNK)
                if not chunk:
                    self._is_open = False
                    logger.warning(
                        "%s Socket broker closed: %s %s:%d (peer disconnected)",
                        LogTag.HW_SERIAL,
                        self._port_name,
                        self._host,
                        self._port,
                    )
                    self._notify_lost()
                    break
                self._rx_buffer.extend(chunk)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "%s Socket RX burst: %s got=%dB buffered=%dB hex=%s",
                        LogTag.HW_SERIAL,
                        self._port_name,
                        len(chunk),
                        len(self._rx_buffer),
                        format_hex_preview(chunk),
                    )
                if len(chunk) < SOCKET_RECV_CHUNK:
                    break
        except (TimeoutError, BlockingIOError):
            return
        except OSError as exc:
            self._is_open = False
            self._notify_lost()
            logger.warning(
                "%s Socket recv error, marking closed: %s %s:%d: %s",
                LogTag.HW_SERIAL,
                self._port_name,
                self._host,
                self._port,
                exc,
            )

    def read(self, size: int | None = None) -> bytes:
        """Đọc chuỗi byte từ bộ đệm nhận RX của socket.

        Chỉ kéo thêm dữ liệu từ socket khi bộ đệm hiện tại không đủ đáp ứng,
        giúp tối ưu hóa tần suất đọc mạng trong chu kỳ polling của worker.

        Args:
            size: Số lượng byte tối đa cần đọc, hoặc None để đọc toàn bộ byte có sẵn.

        Returns:
            Chuỗi byte nhị phân đã trích xuất từ bộ đệm.
        """
        if not self.is_open:
            return b""
        if size is None or size > len(self._rx_buffer):
            self._fill()
        if not self._rx_buffer:
            return b""
        if size is None or size >= len(self._rx_buffer):
            data = bytes(self._rx_buffer)
            self._rx_buffer.clear()
            return data
        data = bytes(self._rx_buffer[:size])
        del self._rx_buffer[:size]
        return data

    @property
    def in_waiting(self) -> int:
        """Số lượng byte hiện có sẵn để đọc trong bộ đệm nhận."""
        if not self.is_open:
            return 0
        self._fill()
        return len(self._rx_buffer)

    def flush(self) -> None:
        """Xả sạch bộ đệm phát (không thao tác đối với luồng TCP)."""

    @property
    def baudrate(self) -> int:
        """Tốc độ Baud quang học mô phỏng hiện hành."""
        return self._baudrate

    @property
    def control_baudrate(self) -> int:
        """Tốc độ Baud điều khiển mô phỏng cố định."""
        return self._ctrl_baudrate

    @property
    def sent_baud_commands(self) -> list[tuple[int, str]]:
        """Danh sách các mã lệnh điều khiển Baud đã ghi nhận cho liên kết ảo này."""
        return list(self._sent_baud_commands)

    def send_baud_command(self, baud: int) -> str:
        """Ghi nhận lệnh chuyển đổi tốc độ Baud cho transport mô phỏng liên kết mạng.

        Args:
            baud: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).

        Returns:
            Chuỗi 2 ký tự mã lệnh ASCII của firmware.

        Raises:
            ValueError: Nếu tốc độ Baud không được hỗ trợ.
        """
        code = BAUD_COMMANDS.get(int(baud))
        if code is None:
            raise ValueError(f"Unsupported optical baudrate {baud}")
        self._sent_baud_commands.append((int(baud), code))
        logger.info(
            "%s Socket control command recorded: %s baud=%d code=%r",
            LogTag.BAUD_CTRL,
            self._port_name,
            baud,
            code,
        )
        return code

    def reconfigure_data_baud(self, baud: int) -> None:
        """Tái cấu hình tốc độ Baud quang học mô phỏng trên kết nối socket.

        Args:
            baud: Tốc độ Baud quang học mới cần áp dụng.

        Raises:
            ValueError: Nếu tốc độ Baud không được hỗ trợ.
        """
        target = int(baud)
        if target not in BAUD_COMMANDS:
            raise ValueError(f"Unsupported optical baudrate {target}")
        previous = self._baudrate
        self._baudrate = target
        logger.info(
            "%s Socket baudrate: %s %d -> %d bps",
            LogTag.BAUD_CTRL,
            self._port_name,
            previous,
            target,
        )
