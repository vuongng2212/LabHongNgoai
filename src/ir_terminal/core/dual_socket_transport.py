"""Dual-socket transport separating TX and RX virtual TCP links."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.socket_transport import SocketTransport
from ir_terminal.protocol.constants import SOCKET_CONNECT_TIMEOUT_SEC
from ir_terminal.utils.logger import LogTag

logger = logging.getLogger(__name__)

__all__ = ["DualSocketTransport"]


class DualSocketTransport(ITransport):
    """ITransport writing to a TX socket and reading from a separate RX socket."""

    def __init__(
        self,
        tx_port_name: str,
        rx_port_name: str,
        tx_endpoint: tuple[str, int],
        rx_endpoint: tuple[str, int],
        *,
        connect_retries: int = 0,
        retry_interval_sec: float = 0.25,
        connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
        token: str | None = None,
    ) -> None:
        """Initializes the dual-socket transport without opening sockets.

        Args:
            tx_port_name: Name identifier for TX socket endpoint (e.g. LINK_A_TX).
            rx_port_name: Name identifier for RX socket endpoint (e.g. LINK_A_RX).
            tx_endpoint: (host, port) tuple for outgoing data.
            rx_endpoint: (host, port) tuple for incoming data.
            connect_retries: Extra connection retry attempts.
            retry_interval_sec: Delay between connection attempts.
            connect_timeout_sec: Connection timeout in seconds.
            token: Optional shared secret authentication token.
        """
        self._tx_port_name = tx_port_name
        self._rx_port_name = rx_port_name
        self._tx_endpoint = tx_endpoint
        self._rx_endpoint = rx_endpoint
        self._connect_retries = connect_retries
        self._retry_interval_sec = retry_interval_sec
        self._connect_timeout_sec = connect_timeout_sec
        self._token = token

        self._tx_transport: SocketTransport | None = None
        self._rx_transport: SocketTransport | None = None
        self._on_link_lost: Callable[[], None] | None = None

    @property
    def tx_port(self) -> str:
        """Returns TX port identifier."""
        return self._tx_port_name

    @property
    def rx_port(self) -> str:
        """Returns RX port identifier."""
        return self._rx_port_name

    @property
    def on_link_lost(self) -> Callable[[], None] | None:
        """Returns link lost callback."""
        return self._on_link_lost

    @on_link_lost.setter
    def on_link_lost(self, callback: Callable[[], None] | None) -> None:
        """Sets link lost callback on both sub-transports."""
        self._on_link_lost = callback
        if self._tx_transport is not None:
            self._tx_transport.on_link_lost = callback
        if self._rx_transport is not None:
            self._rx_transport.on_link_lost = callback

    def open(self) -> None:
        """Opens both TX and RX socket connections with rollback on error."""
        if self.is_open:
            self.close()

        logger.info(
            "%s DualSocket open: tx=%s (%s:%d) rx=%s (%s:%d)",
            LogTag.HW_SERIAL,
            self._tx_port_name,
            self._tx_endpoint[0],
            self._tx_endpoint[1],
            self._rx_port_name,
            self._rx_endpoint[0],
            self._rx_endpoint[1],
        )

        tx = SocketTransport(
            port_name=self._tx_port_name,
            host=self._tx_endpoint[0],
            port=self._tx_endpoint[1],
            connect_retries=self._connect_retries,
            retry_interval_sec=self._retry_interval_sec,
            connect_timeout_sec=self._connect_timeout_sec,
            token=self._token,
        )
        tx.on_link_lost = self._on_link_lost

        rx = SocketTransport(
            port_name=self._rx_port_name,
            host=self._rx_endpoint[0],
            port=self._rx_endpoint[1],
            connect_retries=self._connect_retries,
            retry_interval_sec=self._retry_interval_sec,
            connect_timeout_sec=self._connect_timeout_sec,
            token=self._token,
        )
        rx.on_link_lost = self._on_link_lost

        try:
            tx.open()
            self._tx_transport = tx
            rx.open()
            self._rx_transport = rx
        except Exception:
            logger.exception(
                "%s DualSocket open failed: rolling back",
                LogTag.HW_SERIAL,
            )
            self.close()
            raise

    def close(self) -> None:
        """Closes both TX and RX socket transports."""
        tx = self._tx_transport
        rx = self._rx_transport
        self._tx_transport = None
        self._rx_transport = None

        if tx is not None:
            tx.close()
        if rx is not None:
            rx.close()

        logger.info(
            "%s DualSocket closed: tx=%s rx=%s",
            LogTag.HW_SERIAL,
            self._tx_port_name,
            self._rx_port_name,
        )

    @property
    def is_open(self) -> bool:
        """Returns True only when both TX and RX transports are open."""
        tx_ok = bool(self._tx_transport and self._tx_transport.is_open)
        rx_ok = bool(self._rx_transport and self._rx_transport.is_open)
        return tx_ok and rx_ok

    def write(self, data: bytes) -> int:
        """Writes binary data exclusively to the TX socket transport."""
        if not self._tx_transport or not self._tx_transport.is_open:
            raise RuntimeError("TX socket transport is not open for write")
        return self._tx_transport.write(data)

    def read(self, size: int | None = None) -> bytes:
        """Reads incoming bytes exclusively from the RX socket transport."""
        if not self._rx_transport or not self._rx_transport.is_open:
            return b""
        return self._rx_transport.read(size)

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes buffered on the RX socket transport."""
        if not self._rx_transport or not self._rx_transport.is_open:
            return 0
        return self._rx_transport.in_waiting

    def flush(self) -> None:
        """Flushes the outgoing transmit buffer on the TX socket transport."""
        if self._tx_transport and self._tx_transport.is_open:
            self._tx_transport.flush()
