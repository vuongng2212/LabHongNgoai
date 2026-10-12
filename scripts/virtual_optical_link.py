"""Virtual optical link broker for dual-mock mode (2 apps on 1 PC).

Listens on 127.0.0.1:6001 (LINK_A) and 127.0.0.1:6002 (LINK_B) and forwards
raw bytes A<->B without parsing frames, emulating the infrared beam.

Simulates channel effects:
  --loss 0.0..1.0 : random packet drop probability (byte-chunk level).
  --latency-ms N  : artificial forwarding delay per chunk.
  --los-block     : start with beam blocked (drop everything).

Runtime: type ``b`` + Enter on stdin to toggle LOS blockage (emulates the
3s cardboard blockage in ACCEPTANCE.md step 3 without restarting).

This script is used ONLY for mock testing. Production hardware path
(PySerialTransport with real COM ports) never touches it.
"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import threading
import time

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger("virtual_link")


class VirtualOpticalLink:
    """Bidirectional TCP relay emulating the IR beam."""

    def __init__(
        self,
        port_a: int = 6001,
        port_b: int = 6002,
        loss_rate: float = 0.0,
        latency_ms: float = 5.0,
    ) -> None:
        """Initializes broker endpoints and channel parameters."""
        self._port_a = port_a
        self._port_b = port_b
        self._loss_rate = loss_rate
        self._latency_s = latency_ms / 1000.0
        self._los_blocked = False
        self._conns: dict[str, socket.socket] = {}
        self._lock = threading.Lock()

    @property
    def los_blocked(self) -> bool:
        """Returns True when the virtual beam is currently blocked."""
        return self._los_blocked

    def set_los_blocked(self, blocked: bool) -> None:
        """Blocks or restores the virtual beam."""
        self._los_blocked = blocked
        logger.info("LOS beam %s", "BLOCKED - dropping bytes" if blocked else "RESTORED")

    def _accept_loop(self, label: str, tcp_port: int) -> None:
        """Accepts app connections in a loop so reconnects always work."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", tcp_port))
        srv.listen(1)
        logger.info("Waiting for App %s on 127.0.0.1:%d ...", label, tcp_port)
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            logger.info("App %s connected from %s", label, addr)
            with self._lock:
                old = self._conns.get(label)
                self._conns[label] = conn
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass

    def _forward(self, src: str, dst: str) -> None:
        """Forwards bytes from src app to dst app with channel effects."""
        while True:
            with self._lock:
                conn = self._conns.get(src)
            if conn is None:
                time.sleep(0.05)
                continue
            try:
                data = conn.recv(4096)
            except OSError:
                # Socket error: drop dead conn and wait for app to reconnect.
                with self._lock:
                    if self._conns.get(src) is conn:
                        self._conns.pop(src, None)
                try:
                    conn.close()
                except OSError:
                    pass
                logger.info("App %s link error, waiting for reconnect", src)
                continue
            if not data:
                logger.info("App %s disconnected, waiting for reconnect", src)
                with self._lock:
                    if self._conns.get(src) is conn:
                        self._conns.pop(src, None)
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            if self._los_blocked:
                continue
            if self._loss_rate > 0.0 and random.random() < self._loss_rate:
                continue
            if self._latency_s > 0:
                time.sleep(self._latency_s)
            with self._lock:
                dst_conn = self._conns.get(dst)
            if dst_conn is None:
                continue
            try:
                dst_conn.sendall(data)
            except OSError:
                logger.warning("Failed to forward %dB to peer; peer may reconnect", len(data))
                with self._lock:
                    if self._conns.get(dst) is dst_conn:
                        self._conns.pop(dst, None)
                try:
                    if dst_conn is not None:
                        dst_conn.close()
                except OSError:
                    pass
                continue

    def run(self) -> None:
        """Accepts both apps then relays until interrupted."""
        t_a = threading.Thread(target=self._accept_loop, args=("A", self._port_a), daemon=True)
        t_b = threading.Thread(target=self._accept_loop, args=("B", self._port_b), daemon=True)
        t_a.start()
        t_b.start()
        logger.info("Relaying A<->B once both apps connect. Type 'b' + Enter to toggle LOS block.")
        fwd_ab = threading.Thread(target=self._forward, args=("A", "B"), daemon=True)
        fwd_ba = threading.Thread(target=self._forward, args=("B", "A"), daemon=True)
        fwd_ab.start()
        fwd_ba.start()
        # Stdin toggle loop (main thread). If stdin is unavailable (background
        # launch without tty), keep relaying instead of exiting so apps can
        # still connect; LOS toggle just stays unavailable.
        try:
            while True:
                line = input()
                if line.strip().lower() == "b":
                    self.set_los_blocked(not self._los_blocked)
        except (EOFError, KeyboardInterrupt):
            logger.info("Stdin closed; broker keeps relaying (LOS toggle unavailable).")
            while True:
                time.sleep(3600)
        except OSError:
            logger.info("Stdin unavailable; broker keeps relaying.")
            while True:
                time.sleep(3600)


def main() -> None:
    """Parses args and starts the virtual optical link broker."""
    parser = argparse.ArgumentParser(description="Virtual IR optical link broker (dual-mock).")
    parser.add_argument("--port-a", type=int, default=6001)
    parser.add_argument("--port-b", type=int, default=6002)
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--latency-ms", type=float, default=5.0)
    parser.add_argument("--los-block", action="store_true")
    args = parser.parse_args()
    link = VirtualOpticalLink(
        port_a=args.port_a,
        port_b=args.port_b,
        loss_rate=args.loss,
        latency_ms=args.latency_ms,
    )
    if args.los_block:
        link.set_los_blocked(True)
    link.run()


if __name__ == "__main__":
    main()
