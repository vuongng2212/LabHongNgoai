"""Unit tests for the dual TX/RX socket transport."""

from __future__ import annotations

import socket
import threading

import pytest

from ir_terminal.core.dual_socket_transport import DualSocketTransport


class TestDualSocketTransport:
    """Verifies strict 1-way pipeline separation for DualSocketTransport."""

    def test_open_and_unidirectional_io(self) -> None:
        server_tx = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_tx.bind(("127.0.0.1", 0))
        server_tx.listen(1)
        tx_port = server_tx.getsockname()[1]

        server_rx = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_rx.bind(("127.0.0.1", 0))
        server_rx.listen(1)
        rx_port = server_rx.getsockname()[1]

        accepted_tx: list[socket.socket] = []
        accepted_rx: list[socket.socket] = []

        def accept_conn(srv: socket.socket, target_list: list[socket.socket]) -> None:
            conn, _ = srv.accept()
            target_list.append(conn)

        t_tx = threading.Thread(target=accept_conn, args=(server_tx, accepted_tx), daemon=True)
        t_rx = threading.Thread(target=accept_conn, args=(server_rx, accepted_rx), daemon=True)
        t_tx.start()
        t_rx.start()

        transport = DualSocketTransport(
            tx_port_name="LINK_A_TX",
            rx_port_name="LINK_A_RX",
            tx_endpoint=("127.0.0.1", tx_port),
            rx_endpoint=("127.0.0.1", rx_port),
            connect_timeout_sec=2.0,
        )

        try:
            transport.open()
            t_tx.join(timeout=2.0)
            t_rx.join(timeout=2.0)

            assert transport.is_open is True
            assert transport.tx_port == "LINK_A_TX"
            assert transport.rx_port == "LINK_A_RX"
            assert len(accepted_tx) == 1
            assert len(accepted_rx) == 1

            conn_tx = accepted_tx[0]
            conn_rx = accepted_rx[0]

            payload = b"optical_pulse_test"
            written = transport.write(payload)
            assert written == len(payload)

            received_by_server = conn_tx.recv(1024)
            assert received_by_server == payload

            conn_rx.sendall(b"incoming_telemetry")
            import time

            time.sleep(0.05)

            assert transport.in_waiting > 0
            read_bytes = transport.read()
            assert read_bytes == b"incoming_telemetry"

            transport.flush()

        finally:
            transport.close()
            assert transport.is_open is False
            for c in accepted_tx + accepted_rx:
                try:
                    c.close()
                except OSError:
                    pass
            server_tx.close()
            server_rx.close()

    def test_open_rollback_on_rx_failure(self) -> None:
        server_tx = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_tx.bind(("127.0.0.1", 0))
        server_tx.listen(1)
        tx_port = server_tx.getsockname()[1]

        accepted_tx: list[socket.socket] = []

        def accept_conn(srv: socket.socket) -> None:
            try:
                conn, _ = srv.accept()
                accepted_tx.append(conn)
            except OSError:
                pass

        t_tx = threading.Thread(target=accept_conn, args=(server_tx,), daemon=True)
        t_tx.start()

        transport = DualSocketTransport(
            tx_port_name="LINK_A_TX",
            rx_port_name="LINK_A_RX",
            tx_endpoint=("127.0.0.1", tx_port),
            rx_endpoint=("127.0.0.1", 1),
            connect_retries=0,
            connect_timeout_sec=0.2,
        )

        try:
            with pytest.raises((OSError, RuntimeError)):
                transport.open()

            assert transport.is_open is False
            assert transport._tx_transport is None
            assert transport._rx_transport is None
        finally:
            server_tx.close()
            for c in accepted_tx:
                try:
                    c.close()
                except OSError:
                    pass
