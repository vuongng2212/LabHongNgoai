"""TCP socket transport for dual-app mock optical link (LINK_A / LINK_B).

Production hardware path (PySerialTransport) is untouched. This transport is
selected ONLY when the port name starts with ``LINK_`` (see SerialWorker).
It connects to the external broker ``scripts/virtual_optical_link.py`` which
forwards bytes A<->B to emulate the infrared beam on a single PC.
"""

from __future__ import annotations

import logging
import socket

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import (
    SOCKET_CONNECT_TIMEOUT_SEC,
    SOCKET_READ_TIMEOUT_SEC,
    SOCKET_RECV_CHUNK,
)

logger = logging.getLogger(__name__)

# Default broker endpoints for the two virtual COM ports.
LINK_PORT_MAP: dict[str, tuple[str, int]] = {
    "LINK_A": ("127.0.0.1", 6001),
    "LINK_B": ("127.0.0.1", 6002),
}


def resolve_link_endpoint(port_name: str) -> tuple[str, int] | None:
    """Returns (host, port) for a LINK_* virtual port, else None."""
    key = port_name.strip().upper()
    return LINK_PORT_MAP.get(key)


class SocketTransport(ITransport):
    """TCP client transport conforming to ITransport for dual-mock mode."""

    def __init__(
        self, port_name: str = "LINK_A", host: str = "127.0.0.1", port: int = 6001
    ) -> None:
        """Initializes TCP transport.

        Args:
            port_name: Virtual identifier (LINK_A or LINK_B).
            host: Broker host address.
            port: Broker TCP port.
        """
        self._port_name = port_name
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._rx_buffer = bytearray()
        self._is_open = False

    def open(self) -> None:
        """Opens TCP connection to the virtual optical link broker."""
        self.close()
        sock = socket.create_connection(
            (self._host, self._port), timeout=SOCKET_CONNECT_TIMEOUT_SEC
        )
        # Short timeout so worker poll stays non-blocking (mirrors PySerial 0.05s).
        sock.settimeout(SOCKET_READ_TIMEOUT_SEC)
        self._sock = sock
        self._rx_buffer = bytearray()
        self._is_open = True
        logger.info(
            "SocketTransport %s connected to %s:%d", self._port_name, self._host, self._port
        )

    def close(self) -> None:
        """Closes TCP connection."""
        self._is_open = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._rx_buffer = bytearray()

    @property
    def is_open(self) -> bool:
        """Returns True if TCP connection is active."""
        return self._is_open and self._sock is not None

    def write(self, data: bytes) -> int:
        """Sends bytes to the broker for forwarding to the peer app.

        Args:
            data: Raw frame bytes.

        Returns:
            Number of bytes sent.
        """
        if not self.is_open or self._sock is None:
            raise RuntimeError("SocketTransport not open for write")
        self._sock.sendall(data)
        return len(data)

    def _fill(self) -> None:
        """Pulls available bytes from socket into the RX buffer (non-blocking)."""
        sock = self._sock
        if sock is None or not self._is_open:
            return
        try:
            while True:
                chunk = sock.recv(SOCKET_RECV_CHUNK)
                if not chunk:
                    # Peer broker closed connection.
                    self._is_open = False
                    break
                self._rx_buffer.extend(chunk)
                if len(chunk) < SOCKET_RECV_CHUNK:
                    break
        except (TimeoutError, BlockingIOError):
            return
        except OSError:
            self._is_open = False

    def read(self, size: int | None = None) -> bytes:
        """Reads bytes from the RX buffer.

        Only pulls from the socket when the buffer cannot satisfy the request,
        so the common worker pattern (in_waiting -> read(available)) costs a
        single recv burst per poll instead of two.

        Args:
            size: Max bytes to return, or None for all available.

        Returns:
            Extracted byte sequence.
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
        """Returns number of bytes available to read."""
        if not self.is_open:
            return 0
        self._fill()
        return len(self._rx_buffer)
