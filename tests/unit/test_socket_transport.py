"""Unit tests for SocketTransport dual-mock TCP transport."""

from __future__ import annotations

import socket
import threading

from ir_terminal.core.socket_transport import SocketTransport, resolve_link_endpoint


def _run_echo_server(sock: socket.socket) -> None:
    """Echoes received bytes back until client closes."""
    conn, _ = sock.accept()
    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(data)


def test_resolve_link_endpoints() -> None:
    """LINK_A/B resolve to broker ports; other names return None."""
    assert resolve_link_endpoint("LINK_A") == ("127.0.0.1", 6001)
    assert resolve_link_endpoint("link_b") == ("127.0.0.1", 6002)
    assert resolve_link_endpoint("COM3") is None
    assert resolve_link_endpoint("MOCK_COM_LOOPBACK") is None


def test_socket_transport_echo_roundtrip() -> None:
    """SocketTransport writes and reads back through a local echo server."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    tcp_port = srv.getsockname()[1]
    worker = threading.Thread(target=_run_echo_server, args=(srv,), daemon=True)
    worker.start()

    transport = SocketTransport(port_name="LINK_A", host="127.0.0.1", port=tcp_port)
    try:
        transport.open()
        assert transport.is_open
        payload = b"\x55\xaa dual-mock echo \x00\x01\x02"
        assert transport.write(payload) == len(payload)
        received = b""
        for _ in range(100):
            chunk = transport.read(4096)
            if chunk:
                received += chunk
            if len(received) >= len(payload):
                break
        assert received == payload
        assert transport.in_waiting == 0
    finally:
        transport.close()
        srv.close()
    assert not transport.is_open
