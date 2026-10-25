"""Transport factory selecting concrete ITransport by port name (OCP)."""

from __future__ import annotations

import logging

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.socket_transport import SocketTransport, resolve_link_endpoint
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE, SOCKET_CONNECT_TIMEOUT_SEC
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)


def create_transport(
    tx_port: str,
    rx_port: str | None = None,
    baudrate: int = DEFAULT_BAUDRATE,
    link_endpoints: dict[str, tuple[str, int]] | None = None,
    *,
    link_connect_retries: int = 0,
    link_retry_interval_sec: float = 0.25,
    link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
    link_token: str | None = None,
) -> ITransport:
    """Creates the concrete transport for a TX/RX port pair.

    Args:
        tx_port: Target TX port identifier (MOCK_*, LINK_*, or physical COM/tty).
        rx_port: Target RX port identifier for physical ports. None reuses tx_port.
        baudrate: Baud rate for serial transports.
        link_endpoints: Optional LINK_* -> (host, port) override map.
        link_connect_retries: Extra LINK connect attempts (dual-node startup).
        link_retry_interval_sec: Delay between LINK connect attempts.
        link_connect_timeout_sec: Per-attempt LINK connect timeout.
        link_token: Optional shared token for the broker handshake.

    Returns:
        Concrete ITransport instance (not yet opened).
    """
    tx_name = (tx_port or "").strip()
    rx_name = (rx_port or "").strip() or tx_name
    if tx_name.startswith("MOCK"):
        auto_respond = not (
            "PIPE" in tx_name or "NO_LOOPBACK" in tx_name or "NODE" in tx_name
        )
        logger.info(
            "%s Transport select: MOCK port=%s baud=%d auto_respond=%s",
            LogTag.HW_SERIAL,
            tx_name,
            baudrate,
            auto_respond,
        )
        return MockSerialPort(port_name=tx_name, baudrate=baudrate, auto_respond=auto_respond)
    endpoint = resolve_link_endpoint(tx_name, link_endpoints)
    if endpoint is not None:
        host, tcp_port = endpoint
        logger.info(
            "%s Transport select: SOCKET %s -> %s:%d (retries=%d)",
            LogTag.HW_SERIAL,
            tx_name.upper(),
            host,
            tcp_port,
            link_connect_retries,
        )
        return SocketTransport(
            port_name=tx_name.upper(),
            host=host,
            port=tcp_port,
            connect_retries=link_connect_retries,
            retry_interval_sec=link_retry_interval_sec,
            connect_timeout_sec=link_connect_timeout_sec,
            token=link_token,
        )
    from ir_terminal.core.dual_serial_transport import DualSerialTransport

    logger.info(
        "%s Transport select: DUAL SERIAL tx=%s rx=%s baud=%d (8N1)",
        LogTag.HW_SERIAL,
        tx_name,
        rx_name,
        baudrate,
    )
    return DualSerialTransport(tx_port=tx_name, rx_port=rx_name, baudrate=baudrate)
