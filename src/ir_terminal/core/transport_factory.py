"""Transport factory selecting the concrete ITransport implementation."""

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
    """Creates the transport for a COM1 data port and a COM2 control port.

    Args:
        data_port: COM1 optical data port identifier.
        ctrl_port: COM2 STM32 control port identifier, or None.
        baudrate: Optical data baudrate.
        ctrl_baudrate: Fixed MCU control baudrate.
        link_endpoints: Optional LINK_* to ``(host, port)`` override map.
        link_connect_retries: Extra LINK connect attempts.
        link_retry_interval_sec: Delay between LINK connect attempts.
        link_connect_timeout_sec: Per-attempt LINK connect timeout.
        link_token: Optional shared token for the broker handshake.
        tx_port: Legacy alias for ``data_port``.
        rx_port: Legacy alias for ``ctrl_port``.

    Returns:
        Concrete, not-yet-opened ITransport instance.
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
