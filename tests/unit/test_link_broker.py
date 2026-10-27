"""Unit tests for the in-app LinkBroker and link transport wiring."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable

import pytest

from ir_terminal.core.link_broker import LinkBroker
from ir_terminal.core.socket_transport import SocketTransport


def _free_ports(count: int) -> list[int]:
    """Reserves and returns ``count`` distinct loopback TCP ports."""
    ports: list[int] = []
    reserved: list[socket.socket] = []
    try:
        while len(ports) < count:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
            if port not in ports:
                ports.append(port)
                reserved.append(sock)
    finally:
        for sock in reserved:
            sock.close()
    return ports


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Polls a predicate until true or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _read_until(transport: SocketTransport, expected: bytes, timeout: float = 3.0) -> bytes:
    """Reads from a SocketTransport until ``expected`` length is collected."""
    deadline = time.monotonic() + timeout
    received = b""
    while time.monotonic() < deadline:
        chunk = transport.read(4096)
        if chunk:
            received += chunk
            if len(received) >= len(expected):
                break
        else:
            time.sleep(0.01)
    return received


def test_relay_roundtrip_and_peer_status() -> None:
    """Both nodes connect and bytes relay in both directions."""
    port_a, port_b = _free_ports(2)
    broker = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    broker.start()
    transport_a = SocketTransport("LINK_A", "127.0.0.1", port_a, connect_timeout_sec=1.0)
    transport_b = SocketTransport("LINK_B", "127.0.0.1", port_b, connect_timeout_sec=1.0)
    try:
        transport_a.open()
        transport_b.open()
        assert broker.wait_for_peers(2.0) is True
        assert broker.connected_roles() == ["A", "B"]

        transport_a.write(b"hello-from-a")
        assert _read_until(transport_b, b"hello-from-a") == b"hello-from-a"

        transport_b.write(b"hello-from-b")
        assert _read_until(transport_a, b"hello-from-b") == b"hello-from-b"
    finally:
        transport_a.close()
        transport_b.close()
        broker.stop()


def test_los_blocks_then_restores_the_beam() -> None:
    """Blocked LOS drops bytes; restoring the beam resumes relay."""
    port_a, port_b = _free_ports(2)
    broker = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    broker.start()
    transport_a = SocketTransport("LINK_A", "127.0.0.1", port_a, connect_timeout_sec=1.0)
    transport_b = SocketTransport("LINK_B", "127.0.0.1", port_b, connect_timeout_sec=1.0)
    try:
        transport_a.open()
        transport_b.open()
        assert broker.wait_for_peers(2.0) is True

        broker.set_los_blocked(True)
        transport_a.write(b"blocked-payload")
        time.sleep(0.2)
        assert transport_b.in_waiting == 0

        broker.set_los_blocked(False)
        transport_a.write(b"open-payload")
        assert _read_until(transport_b, b"open-payload") == b"open-payload"
    finally:
        transport_a.close()
        transport_b.close()
        broker.stop()


def test_token_handshake_rejects_missing_token() -> None:
    """A client without the shared token is not registered with the broker."""
    port_a, port_b = _free_ports(2)
    broker = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0, token="s3cret")
    broker.start()
    rejected = SocketTransport("LINK_A", "127.0.0.1", port_a, connect_timeout_sec=1.0)
    accepted = SocketTransport(
        "LINK_A", "127.0.0.1", port_a, connect_timeout_sec=1.0, token="s3cret"
    )
    try:
        rejected.open()
        assert _wait_until(lambda: broker.connected_roles() == ["A"], timeout=0.5) is False
        rejected.close()

        accepted.open()
        assert _wait_until(lambda: broker.connected_roles() == ["A"], timeout=2.0) is True
    finally:
        rejected.close()
        accepted.close()
        broker.stop()


def test_connect_retries_until_host_starts() -> None:
    """The join node retries instead of failing when launched before the host."""
    port_a, port_b = _free_ports(2)
    transport = SocketTransport(
        "LINK_B",
        "127.0.0.1",
        port_b,
        connect_retries=40,
        retry_interval_sec=0.05,
        connect_timeout_sec=0.2,
    )
    errors: list[BaseException] = []

    def _open() -> None:
        try:
            transport.open()
        except BaseException as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    opener = threading.Thread(target=_open, daemon=True)
    opener.start()
    time.sleep(0.2)

    broker = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    broker.start()
    try:
        opener.join(timeout=5.0)
        assert not opener.is_alive()
        assert errors == []
        assert transport.is_open is True
        assert _wait_until(lambda: broker.connected_roles() == ["B"])
    finally:
        transport.close()
        broker.stop()


def test_bind_conflict_raises_without_leaking_the_first_port() -> None:
    """A second broker on the same ports fails cleanly."""
    port_a, port_b = _free_ports(2)
    first = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    first.start()
    second = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    try:
        with pytest.raises(OSError):
            second.start()
        assert second.is_running is False
    finally:
        first.stop()
