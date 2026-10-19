"""Transport factory selecting concrete ITransport by port name (OCP)."""

from __future__ import annotations

import logging

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.socket_transport import SocketTransport, resolve_link_endpoint
from ir_terminal.protocol.constants import SOCKET_CONNECT_TIMEOUT_SEC
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


def create_transport(
    port_name: str,
    baudrate: int = 115200,
    link_endpoints: dict[str, tuple[str, int]] | None = None,
    *,
    link_connect_retries: int = 0,
    link_retry_interval_sec: float = 0.25,
    link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
    link_token: str | None = None,
) -> ITransport:
    """Creates the concrete transport for a port name without editing callers.

    Args:
        port_name: Target port identifier (MOCK_*, LINK_*, or physical COM/tty).
        baudrate: Baud rate for serial transports.
        link_endpoints: Optional LINK_* -> (host, port) override map.
        link_connect_retries: Extra LINK connect attempts (dual-node startup).
        link_retry_interval_sec: Delay between LINK connect attempts.
        link_connect_timeout_sec: Per-attempt LINK connect timeout.
        link_token: Optional shared token for the broker handshake.

    Returns:
        Concrete ITransport instance (not yet opened).
    """
    # Local import avoids a hard core->pyserial dependency at module import time
    # so unit tests can import the factory without pyserial installed.
    if port_name.startswith("MOCK"):
        auto_respond = not (
            "PIPE" in port_name or "NO_LOOPBACK" in port_name or "NODE" in port_name
        )
        logger.info(
            "%s Transport select: MOCK port=%s baud=%d auto_respond=%s",
            LogTag.HW_SERIAL,
            port_name,
            baudrate,
            auto_respond,
        )
        return MockSerialPort(port_name=port_name, baudrate=baudrate, auto_respond=auto_respond)
    endpoint = resolve_link_endpoint(port_name, link_endpoints)
    if endpoint is not None:
        host, tcp_port = endpoint
        logger.info(
            "%s Transport select: SOCKET %s -> %s:%d (retries=%d)",
            LogTag.HW_SERIAL,
            port_name.strip().upper(),
            host,
            tcp_port,
            link_connect_retries,
        )
        return SocketTransport(
            port_name=port_name.strip().upper(),
            host=host,
            port=tcp_port,
            connect_retries=link_connect_retries,
            retry_interval_sec=link_retry_interval_sec,
            connect_timeout_sec=link_connect_timeout_sec,
            token=link_token,
        )
    from ir_terminal.core.pyserial_transport import PySerialTransport

    logger.info(
        "%s Transport select: PYSERIAL port=%s baud=%d (8N1)",
        LogTag.HW_SERIAL,
        port_name,
        baudrate,
    )
    return PySerialTransport(port_name=port_name, baudrate=baudrate)
