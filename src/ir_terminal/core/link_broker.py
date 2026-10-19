"""In-app virtual optical link broker (zero-driver dual-node mode).

Emulates the infrared beam between two app instances on one PC over TCP
loopback: Node A connects to ``port_a`` (``LINK_A``), Node B connects to
``port_b`` (``LINK_B``), and raw frame bytes are relayed A<->B without parsing.
No kernel driver, no admin rights, works with Secure Boot enabled.

The broker is started **inside** the host node (``--link-role host``) so no
external Python process or ``scripts/`` checkout is required in the packaged
``.exe``. ``main()`` remains available for headless/standalone use.
"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import sys
import threading
import time

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT_A: int = 6001
DEFAULT_PORT_B: int = 6002
DEFAULT_LATENCY_MS: float = 5.0

ACCEPT_TIMEOUT_SEC: float = 0.5
FORWARD_RECV_CHUNK: int = 4096
IDLE_POLL_SEC: float = 0.02
HANDSHAKE_TIMEOUT_SEC: float = 2.0

# Optional token handshake: "<HELLO_PREFIX><token>\n" sent by the client.
_HELLO_PREFIX: str = "IRLINK 1 "

logger = logging.getLogger(__name__)


def _close_quietly(sock: socket.socket | None) -> None:
    """Closes a socket, swallowing OS errors during shutdown."""
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


class LinkBroker:
    """Bidirectional TCP relay emulating the IR beam between two nodes."""

    def __init__(
        self,
        port_a: int = DEFAULT_PORT_A,
        port_b: int = DEFAULT_PORT_B,
        *,
        host: str = DEFAULT_HOST,
        loss_rate: float = 0.0,
        latency_ms: float = DEFAULT_LATENCY_MS,
        token: str | None = None,
    ) -> None:
        """Initializes broker endpoints and channel parameters.

        Args:
            port_a: TCP port for Node A (LINK_A).
            port_b: TCP port for Node B (LINK_B).
            host: Bind/loopback host (default 127.0.0.1; never 0.0.0.0).
            loss_rate: Random per-chunk drop probability in ``0.0..1.0``.
            latency_ms: Artificial forwarding delay per chunk (ms).
            token: Optional shared secret for the client handshake.
        """
        self._port_a = int(port_a)
        self._port_b = int(port_b)
        self._host = host
        self._loss_rate = float(loss_rate)
        self._latency_s = max(0.0, float(latency_ms)) / 1000.0
        self._token = token
        self._los_blocked = False
        self._conns: dict[str, socket.socket] = {}
        self._servers: dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False

    # ------------------------------------------------------------------ props
    @property
    def host(self) -> str:
        """Returns the bind host (loopback)."""
        return self._host

    @property
    def port_a(self) -> int:
        """Returns the TCP port for Node A (LINK_A)."""
        return self._port_a

    @property
    def port_b(self) -> int:
        """Returns the TCP port for Node B (LINK_B)."""
        return self._port_b

    @property
    def los_blocked(self) -> bool:
        """Returns True when the virtual beam is currently blocked."""
        return self._los_blocked

    @property
    def is_running(self) -> bool:
        """Returns True while accept/forward threads are active."""
        return self._started

    def set_los_blocked(self, blocked: bool) -> None:
        """Blocks or restores the virtual beam (drops all bytes while blocked)."""
        self._los_blocked = bool(blocked)
        logger.info("LOS beam %s", "BLOCKED - dropping bytes" if blocked else "RESTORED")

    def connected_roles(self) -> list[str]:
        """Returns the currently connected node labels (subset of A/B)."""
        with self._lock:
            return sorted(self._conns.keys())

    def wait_for_peers(self, timeout: float = 10.0) -> bool:
        """Waits until both nodes are connected or the timeout elapses."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline and not self._stop.is_set():
            with self._lock:
                if "A" in self._conns and "B" in self._conns:
                    return True
            time.sleep(IDLE_POLL_SEC)
        return False

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Binds both ports and spawns accept/forward daemon threads."""
        if self._started:
            return
        self._stop.clear()
        try:
            for label, tcp_port in (("A", self._port_a), ("B", self._port_b)):
                server = self._bind_server(tcp_port)
                self._servers[label] = server
                accept = threading.Thread(
                    target=self._accept_loop,
                    args=(label, server),
                    name=f"link-accept-{label}",
                    daemon=True,
                )
                accept.start()
                self._threads.append(accept)
        except OSError:
            # Do not leak the first listener if the second port is busy.
            self.stop()
            raise
        for src, dst in (("A", "B"), ("B", "A")):
            forward = threading.Thread(
                target=self._forward,
                args=(src, dst),
                name=f"link-fwd-{src}{dst}",
                daemon=True,
            )
            forward.start()
            self._threads.append(forward)
        self._started = True
        logger.info(
            "LinkBroker listening A=%s:%d B=%s:%d (loss=%.2f latency=%.1fms token=%s)",
            self._host,
            self._port_a,
            self._host,
            self._port_b,
            self._loss_rate,
            self._latency_s * 1000.0,
            "yes" if self._token else "no",
        )

    def stop(self) -> None:
        """Stops accept/forward loops and closes every socket."""
        self._stop.set()
        with self._lock:
            conns = list(self._conns.values())
            self._conns.clear()
            servers = list(self._servers.values())
            self._servers.clear()
        for conn in conns:
            _close_quietly(conn)
        for server in servers:
            _close_quietly(server)
        self._threads.clear()
        if self._started:
            logger.info("LinkBroker stopped (A=%d B=%d)", self._port_a, self._port_b)
        self._started = False

    def run(self) -> None:
        """Starts the broker and blocks until stopped (back-compat entry)."""
        self.start()
        logger.info("Relaying A<->B once both nodes connect.")
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ---------------------------------------------------------------- internals
    def _bind_server(self, tcp_port: int) -> socket.socket:
        """Binds one listening socket, exclusive on Windows to avoid hijack."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._host, tcp_port))
        server.listen(1)
        server.settimeout(ACCEPT_TIMEOUT_SEC)
        return server

    def _accept_loop(self, label: str, server: socket.socket) -> None:
        """Accepts node connections in a loop so reconnects always work."""
        logger.info("Waiting for Node %s on %s:%d ...", label, self._host, self._server_port(label))
        while not self._stop.is_set():
            try:
                conn, addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self._verify_handshake(conn):
                _close_quietly(conn)
                continue
            conn.settimeout(ACCEPT_TIMEOUT_SEC)
            with self._lock:
                old = self._conns.get(label)
                self._conns[label] = conn
            if old is not None:
                _close_quietly(old)
            logger.info("Node %s connected from %s", label, addr)

    def _server_port(self, label: str) -> int:
        """Returns the TCP port associated with a node label."""
        return self._port_a if label == "A" else self._port_b

    def _verify_handshake(self, conn: socket.socket) -> bool:
        """Validates the optional shared-token handshake line."""
        if self._token is None:
            return True
        conn.settimeout(HANDSHAKE_TIMEOUT_SEC)
        try:
            raw = conn.recv(256)
        except OSError:
            return False
        text = raw.decode("utf-8", errors="replace").strip()
        if text != f"{_HELLO_PREFIX}{self._token}":
            logger.warning("Rejected link client: bad or missing token")
            return False
        return True

    def _drop(self, label: str, conn: socket.socket) -> None:
        """Removes and closes a dead connection so the node can reconnect."""
        with self._lock:
            if self._conns.get(label) is conn:
                self._conns.pop(label, None)
        _close_quietly(conn)
        logger.info("Node %s disconnected, waiting for reconnect", label)

    def _forward(self, src: str, dst: str) -> None:
        """Forwards bytes from src node to dst node with channel effects."""
        while not self._stop.is_set():
            with self._lock:
                conn = self._conns.get(src)
            if conn is None:
                time.sleep(IDLE_POLL_SEC)
                continue
            try:
                data = conn.recv(FORWARD_RECV_CHUNK)
            except TimeoutError:
                continue
            except OSError:
                self._drop(src, conn)
                continue
            if not data:
                self._drop(src, conn)
                continue
            if self._los_blocked:
                continue
            if self._loss_rate > 0.0 and random.random() < self._loss_rate:
                continue
            if self._latency_s > 0.0:
                time.sleep(self._latency_s)
            with self._lock:
                dst_conn = self._conns.get(dst)
            if dst_conn is None:
                continue
            try:
                dst_conn.sendall(data)
            except OSError:
                logger.warning("Failed to forward %dB to Node %s", len(data), dst)
                self._drop(dst, dst_conn)


# Backward-compatible name used by scripts and existing tests.
VirtualOpticalLink = LinkBroker


def _stdin_los_toggle(broker: LinkBroker) -> None:
    """Dev helper: type ``b`` + Enter on stdin to toggle LOS blockage."""
    if sys.stdin is None:
        return
    try:
        for line in sys.stdin:
            if line.strip().lower() == "b":
                broker.set_los_blocked(not broker.los_blocked)
    except (OSError, ValueError):
        return


def main(argv: list[str] | None = None) -> int:
    """Parses args and runs the standalone link broker (blocking).

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    parser = argparse.ArgumentParser(description="Virtual IR optical link broker.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port-a", type=int, default=DEFAULT_PORT_A)
    parser.add_argument("--port-b", type=int, default=DEFAULT_PORT_B)
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--latency-ms", type=float, default=DEFAULT_LATENCY_MS)
    parser.add_argument("--token", default=None)
    parser.add_argument("--los-block", action="store_true")
    args = parser.parse_args(argv)

    broker = LinkBroker(
        port_a=args.port_a,
        port_b=args.port_b,
        host=args.host,
        loss_rate=args.loss,
        latency_ms=args.latency_ms,
        token=args.token,
    )
    if args.los_block:
        broker.set_los_blocked(True)
    toggle = threading.Thread(target=_stdin_los_toggle, args=(broker,), daemon=True)
    toggle.start()
    broker.run()
    return 0
