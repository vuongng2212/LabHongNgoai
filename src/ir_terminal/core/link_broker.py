"""Tiến trình Broker điều phối liên kết quang ảo nội bộ (In-App Virtual Optical Link Broker).

Mô phỏng đường truyền hồng ngoại giữa hai trạm Node A (cổng LINK_A) và Node B (cổng LINK_B)
trên cùng một máy tính cá nhân thông qua giao thức TCP loopback.
Chuyển tiếp nguyên vẹn các khung byte hai chiều mà không cần cài đặt driver kernel hay quyền quản trị viên (Admin).
Tích hợp sẵn các mô hình suy hao kênh: độ trễ (latency ms), rớt gói (loss rate) và che khuất tầm nhìn (LOS blockage).
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

_HELLO_PREFIX: str = "IRLINK 1 "

logger = logging.getLogger(__name__)


def _close_quietly(sock: socket.socket | None) -> None:
    """Đóng an toàn một socket và bỏ qua các ngoại lệ hệ điều hành khi tắt."""
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


class LinkBroker:
    """Bộ chuyển tiếp TCP hai chiều mô phỏng tia hồng ngoại giữa hai trạm Node A và Node B."""

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
        """Khởi tạo cấu hình các endpoint và tham số kênh truyền ảo.

        Args:
            port_a: Cổng TCP dành cho Node A (LINK_A).
            port_b: Cổng TCP dành cho Node B (LINK_B).
            host: Địa chỉ IP lắng nghe loopback (mặc định 127.0.0.1; tuyệt đối không dùng 0.0.0.0).
            loss_rate: Xác suất rớt gói ngẫu nhiên trên mỗi chunk dữ liệu trong khoảng 0.0 đến 1.0.
            latency_ms: Độ trễ nhân tạo bổ sung cho mỗi chunk dữ liệu tính bằng mili-giây (ms).
            token: Khóa bí mật chia sẻ tùy chọn cho bước bắt tay xác thực client.
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

    @property
    def host(self) -> str:
        """Địa chỉ IP lắng nghe kết nối (loopback)."""
        return self._host

    @property
    def port_a(self) -> int:
        """Cổng TCP kết nối dành cho Node A (LINK_A)."""
        return self._port_a

    @property
    def port_b(self) -> int:
        """Cổng TCP kết nối dành cho Node B (LINK_B)."""
        return self._port_b

    @property
    def los_blocked(self) -> bool:
        """Kiểm tra tia quang ảo hiện có đang bị chặn hay không."""
        return self._los_blocked

    @property
    def is_running(self) -> bool:
        """Kiểm tra các luồng lắng nghe và chuyển tiếp của broker có đang chạy hay không."""
        return self._started

    def set_los_blocked(self, blocked: bool) -> None:
        """Chặn hoặc khôi phục tia quang ảo (loại bỏ toàn bộ dữ liệu khi bị chặn).

        Args:
            blocked: True để chặn tia sáng, False để khôi phục truyền thông bình thường.
        """
        self._los_blocked = bool(blocked)
        logger.info("LOS beam %s", "BLOCKED - dropping bytes" if blocked else "RESTORED")

    def connected_roles(self) -> list[str]:
        """Danh sách các trạm hiện đang kết nối thành công (tập con của 'A' và 'B')."""
        with self._lock:
            return sorted(self._conns.keys())

    def wait_for_peers(self, timeout: float = 10.0) -> bool:
        """Chờ cho đến khi cả hai Node A và Node B đều kết nối thành công hoặc hết thời gian timeout.

        Args:
            timeout: Thời gian chờ tối đa tính bằng giây.

        Returns:
            True nếu cả hai node đều đã kết nối; False nếu hết thời gian chờ.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline and not self._stop.is_set():
            with self._lock:
                if "A" in self._conns and "B" in self._conns:
                    return True
            time.sleep(IDLE_POLL_SEC)
        return False

    def start(self) -> None:
        """Mở lắng nghe trên cả hai cổng TCP và khởi chạy các luồng nhận kết nối và chuyển tiếp dữ liệu."""
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
        """Dừng toàn bộ các vòng lặp luồng và đóng tất cả các socket đang mở."""
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
        """Khởi động broker và chặn luồng hiện tại cho đến khi nhận tín hiệu dừng."""
        self.start()
        logger.info("Relaying A<->B once both nodes connect.")
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _bind_server(self, tcp_port: int) -> socket.socket:
        """Khởi tạo và gán (bind) socket máy chủ lắng nghe; dùng cờ độc quyền trên Windows."""
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
        """Vòng lặp chấp nhận kết nối từ các node để luôn sẵn sàng kết nối lại."""
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
        """Trả về cổng TCP tương ứng với nhãn node ('A' hoặc 'B')."""
        return self._port_a if label == "A" else self._port_b

    def _verify_handshake(self, conn: socket.socket) -> bool:
        """Xác thực dòng bắt tay chứa token bí mật tùy chọn của client."""
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
        """Gỡ bỏ và đóng kết nối đã mất để node có thể tự do kết nối lại."""
        with self._lock:
            if self._conns.get(label) is conn:
                self._conns.pop(label, None)
        _close_quietly(conn)
        logger.info("Node %s disconnected, waiting for reconnect", label)

    def _forward(self, src: str, dst: str) -> None:
        """Chuyển tiếp dữ liệu từ node nguồn sang node đích kèm mô phỏng suy hao kênh."""
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


VirtualOpticalLink = LinkBroker


def _stdin_los_toggle(broker: LinkBroker) -> None:
    """Hàm trợ giúp phát triển: gõ ký tự 'b' và Enter trên bàn phím để bật/tắt chặn tia quang (LOS)."""
    if sys.stdin is None:
        return
    try:
        for line in sys.stdin:
            if line.strip().lower() == "b":
                broker.set_los_blocked(not broker.los_blocked)
    except (OSError, ValueError):
        return


def main(argv: list[str] | None = None) -> int:
    """Phân tích tham số dòng lệnh và khởi chạy LinkBroker ở chế độ độc lập (blocking).

    Args:
        argv: Danh sách tham số tùy chọn (mặc định sys.argv[1:]).

    Returns:
        Mã thoát tiến trình (0 nếu thành công).
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
