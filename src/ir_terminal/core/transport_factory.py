"""Nhà máy khởi tạo tầng truyền tải (Transport Factory) lựa chọn đối tượng ITransport phù hợp.

Dựa trên tiền tố và định dạng của tên cổng COM do người dùng chọn:
- Cổng bắt đầu bằng "MOCK": Khởi tạo MockSerialPort giả lập trong bộ nhớ.
- Cổng bắt đầu bằng "LINK_": Khởi tạo SocketTransport kết nối TCP loopback tới Broker.
- Cổng COM phần cứng chuẩn: Khởi tạo DualSerialTransport giao tiếp trực tiếp qua driver Serial.
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
    """Khởi tạo đối tượng truyền tải ITransport tương ứng cho kênh dữ liệu COM1 và điều khiển COM2.

    Args:
        data_port: Tên định danh cổng nối tiếp dữ liệu quang COM1.
        ctrl_port: Tên định danh cổng nối tiếp điều khiển STM32 COM2, hoặc None.
        baudrate: Tốc độ Baud quang học áp dụng cho COM1.
        ctrl_baudrate: Tốc độ Baud vi điều khiển áp dụng cho COM2 (115200 bps).
        link_endpoints: Bảng ánh xạ tùy chọn cổng ảo LINK_* tới địa chỉ (host, port).
        link_connect_retries: Số lần thử lại kết nối cho cổng LINK.
        link_retry_interval_sec: Khoảng thời gian giãn cách giữa các lần thử kết nối LINK (giây).
        link_connect_timeout_sec: Thời gian chờ tối đa cho mỗi lần kết nối socket LINK (giây).
        link_token: Token xác thực tùy chọn khi bắt tay với LinkBroker.
        tx_port: Tên thay thế tương thích ngược cho data_port.
        rx_port: Tên thay thế tương thích ngược cho ctrl_port.

    Returns:
        Đối tượng cụ thể kế thừa ITransport đã được khởi tạo (chưa mở cổng).
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
