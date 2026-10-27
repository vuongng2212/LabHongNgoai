"""TCP socket transport for dual-app mock optical link (LINK_A / LINK_B).

Production hardware path (PySerialTransport) is untouched. This transport is
selected ONLY when the port name starts with ``LINK_`` (see SerialWorker).
It connects to the external broker ``scripts/virtual_optical_link.py`` which
forwards bytes A<->B to emulate the infrared beam on a single PC.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import (
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
    """Returns (host, port) for a LINK_* virtual port, else None.

    Args:
        port_name: Virtual port identifier (case-insensitive).
        endpoints: Optional override map; defaults to LINK_PORT_MAP.
    """
    key = port_name.strip().upper()
    table = endpoints if endpoints is not None else LINK_PORT_MAP
    return table.get(key)


class SocketTransport(ITransport):
    """TCP client transport conforming to ITransport for dual-mock mode."""

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
    ) -> None:
        """Initializes TCP transport.

        Args:
            port_name: Virtual identifier (LINK_A or LINK_B).
            host: Broker host address.
            port: Broker TCP port.
            connect_retries: Extra connect attempts when the broker is not up
                yet (lets either dual node launch first).
            retry_interval_sec: Delay between connect attempts.
            connect_timeout_sec: Per-attempt TCP connect timeout.
            token: Optional shared secret sent as a handshake line.
        """
        self._port_name = port_name
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
        """Opens TCP connection to the virtual optical link broker."""
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
        """Sends the optional shared-token handshake line to the broker."""
        sock = self._sock
        if sock is None:
            raise RuntimeError("SocketTransport not open for handshake")
        sock.sendall(f"{_HELLO_PREFIX}{self._token}\n".encode())

    def _notify_lost(self) -> None:
        """Fires the on_link_lost callback at most once per open connection."""
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
        """Closes TCP connection."""
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
        """Pulls available bytes from socket into the RX buffer (non-blocking)."""
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

    def flush(self) -> None:
        """Flushes the outgoing transmit buffer (no-op for TCP stream)."""
