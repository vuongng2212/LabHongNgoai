"""Bộ lựa chọn đường truyền phần cứng hoặc đường truyền giả lập (Transport Factory).

Tự động chọn loại kết nối phù hợp dựa trên tên cổng COM mà người dùng bấm chọn trên màn hình:
- Tên cổng có chữ "MOCK": Tạo cổng ảo MockSerialPort chạy trong RAM máy tính để test thử logic.
- Tên cổng bắt đầu bằng "LINK_": Tạo kết nối mạng ảo SocketTransport qua cổng TCP để giả lập 2 máy tính truyền tia quang cho nhau trên cùng 1 PC.
- Tên cổng COM thực tế (như COM3, COM4 trên Windows hoặc /dev/ttyUSB0 trên Linux): Mở driver DualSerialTransport để giao tiếp trực tiếp với mạch thật.
"""

from __future__ import annotations

import logging

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.socket_transport import SocketTransport, resolve_link_endpoint
from ir_terminal.protocol.constants import (
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)

_LOOPBACK_SUPPRESSORS: tuple[str, ...] = ("PIPE", "NO_LOOPBACK", "NODE")


def create_transport(
    data_port: str | None = None,
    ctrl_port: str | None = None,
    baudrate: int = DEFAULT_BAUDRATE,
    ctrl_baudrate: int = CONTROL_BAUDRATE,
    link_endpoints: dict[str, tuple[str, int]] | None = None,
    *,
    link_connect_retries: int = 0,
    link_retry_interval_sec: float = 0.25,
    link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
    link_token: str | None = None,
    tx_port: str | None = None,
    rx_port: str | None = None,
) -> ITransport:
    """Khởi tạo đối tượng giao tiếp tương ứng cho cổng dữ liệu quang COM1 và cổng điều khiển STM32 COM2.

    Args:
        data_port: Tên cổng dữ liệu quang COM1 (ví dụ: 'COM3', 'MOCK_COM1', 'LINK_A').
        ctrl_port: Tên cổng điều khiển STM32 COM2 (ví dụ: 'COM4', 'MOCK_COM2', hoặc None nếu không dùng).
        baudrate: Tốc độ Baud quang học áp dụng cho COM1 (2400, 4800, 9600, 19200 bps).
        ctrl_baudrate: Tốc độ Baud vi điều khiển áp dụng cho COM2 (cố định 115200 bps).
        link_endpoints: Bảng ánh xạ tùy chọn cổng ảo LINK_* tới địa chỉ (host, port).
        link_connect_retries: Số lần thử lại kết nối cho cổng LINK.
        link_retry_interval_sec: Khoảng thời gian giãn cách giữa các lần thử kết nối LINK (giây).
        link_connect_timeout_sec: Thời gian chờ tối đa cho mỗi lần kết nối socket LINK (giây).
        link_token: Mã khóa bảo mật tùy chọn khi bắt tay với trạm trung chuyển ảo LinkBroker.
        tx_port: Tên thay thế tương thích ngược cho data_port.
        rx_port: Tên thay thế tương thích ngược cho ctrl_port.

    Returns:
        Đối tượng cụ thể kế thừa ITransport đã được khởi tạo (sẵn sàng gọi lệnh open để mở cổng).
    """
    data_name = (data_port or tx_port or "").strip()
    ctrl_name = (ctrl_port if ctrl_port is not None else rx_port) or ""
    ctrl_name = ctrl_name.strip()

    if data_name.startswith("MOCK"):
        auto_respond = not any(token in data_name for token in _LOOPBACK_SUPPRESSORS)
        mock_ctrl = ctrl_name if ctrl_name.startswith("MOCK") else None
        logger.info(
            "%s Transport select: MOCK data=%s ctrl=%s baud=%d ctrl_baud=%d auto_respond=%s",
            LogTag.HW_SERIAL,
            data_name,
            mock_ctrl or "<none>",
            baudrate,
            ctrl_baudrate,
            auto_respond,
        )
        return MockSerialPort(
            data_port=data_name,
            ctrl_port=mock_ctrl,
            baudrate=baudrate,
            ctrl_baudrate=ctrl_baudrate,
            auto_respond=auto_respond,
        )

    data_endpoint = resolve_link_endpoint(data_name, link_endpoints)
    ctrl_endpoint = resolve_link_endpoint(ctrl_name, link_endpoints)
    endpoint = data_endpoint or ctrl_endpoint
    if endpoint is not None:
        host, tcp_port = endpoint
        logger.info(
            "%s Transport select: SOCKET %s -> %s:%d (retries=%d)",
            LogTag.HW_SERIAL,
            (data_name or ctrl_name).upper(),
            host,
            tcp_port,
            link_connect_retries,
        )
        return SocketTransport(
            port_name=(data_name or ctrl_name).upper(),
            host=host,
            port=tcp_port,
            connect_retries=link_connect_retries,
            retry_interval_sec=link_retry_interval_sec,
            connect_timeout_sec=link_connect_timeout_sec,
            token=link_token,
            baudrate=baudrate,
            ctrl_baudrate=ctrl_baudrate,
        )

    from ir_terminal.core.dual_serial_transport import DualSerialTransport

    logger.info(
        "%s Transport select: DUAL SERIAL data=%s ctrl=%s baud=%d ctrl_baud=%d (8N1)",
        LogTag.HW_SERIAL,
        data_name,
        ctrl_name or "<none>",
        baudrate,
        ctrl_baudrate,
    )
    return DualSerialTransport(
        data_port=data_name,
        ctrl_port=ctrl_name or None,
        baudrate=baudrate,
        ctrl_baudrate=ctrl_baudrate,
    )
